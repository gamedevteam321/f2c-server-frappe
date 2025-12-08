# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import math


def recalculate_parent_circle(parent_name, depth=0, max_depth=10):
	"""
	Standalone function to recalculate parent circle after child creation/update/deletion
	Can be called from queue
	Recursively updates parent's parent up to max_depth
	"""
	# Prevent infinite recursion
	if depth >= max_depth:
		frappe.log_error(f"Maximum recursion depth ({max_depth}) reached while recalculating parent circles", "Geo Fencing Area Recalculate Parent")
		return
	
	try:
		parent = frappe.get_doc("Geo Fencing Area", parent_name)
		
		if parent.shape_type != "Circle":
			return
		
		if not parent.geo_fencing_type:
			return
		
		geo_fencing_type_doc = frappe.get_doc("Geo Fencing Type", parent.geo_fencing_type)
		
		auto_calculate_center = geo_fencing_type_doc.get("auto_calculate_center_by_child", 0)
		auto_calculate_radius = geo_fencing_type_doc.get("auto_calculate_radius_by_child", 0)
		
		if not auto_calculate_center and not auto_calculate_radius:
			return
		
		# Get all children
		children = frappe.get_all(
			"Geo Fencing Area",
			filters={"parent_area": parent.name},
			fields=["name", "shape_type", "center_latitude", "center_longitude", "radius"]
		)
		
		if not children:
			# No children, reset to default or keep current values
			return
		
		# Separate children by shape type
		circle_children = [child for child in children if child.get("shape_type") == "Circle"]
		polygon_children = [child for child in children if child.get("shape_type") == "Polygon"]
		
		if not circle_children and not polygon_children:
			return
		
		# Fetch polygon coordinates for polygon children
		polygon_children_with_coords = []
		for polygon_child in polygon_children:
			try:
				polygon_doc = frappe.get_doc("Geo Fencing Area", polygon_child["name"])
				if polygon_doc.geo_fencing_coordinates and len(polygon_doc.geo_fencing_coordinates) >= 3:
					# Sort coordinates by sequence
					coords = sorted(polygon_doc.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
					polygon_children_with_coords.append({
						"name": polygon_child["name"],
						"coordinates": coords
					})
			except Exception as e:
				frappe.log_error(f"Error fetching polygon coordinates for {polygon_child['name']}: {str(e)}", "Geo Fencing Area Recalculate Parent")
				continue
		
		# Calculate center
		if auto_calculate_center:
			total_lat = 0
			total_lng = 0
			valid_children = 0
			
			# Add circle children centers
			for child in circle_children:
				child_lat = child.get("center_latitude")
				child_lng = child.get("center_longitude")
				
				if child_lat is not None and child_lng is not None:
					total_lat += float(child_lat)
					total_lng += float(child_lng)
					valid_children += 1
			
			# Add polygon children centroids
			for polygon_child in polygon_children_with_coords:
				centroid = _calculate_polygon_centroid_static(polygon_child["coordinates"])
				if centroid:
					total_lat += centroid[0]
					total_lng += centroid[1]
					valid_children += 1
			
			if valid_children > 0:
				parent.center_latitude = total_lat / valid_children
				parent.center_longitude = total_lng / valid_children
		
		# Calculate radius
		if auto_calculate_radius and parent.center_latitude and parent.center_longitude:
			max_distance = 0
			
			# Process circle children
			for child in circle_children:
				child_lat = child.get("center_latitude")
				child_lng = child.get("center_longitude")
				child_radius = child.get("radius") or 0
				
				if child_lat is not None and child_lng is not None:
					distance = _calculate_distance_static(
						parent.center_latitude, parent.center_longitude,
						float(child_lat), float(child_lng)
					)
					
					total_radius_needed = distance + float(child_radius)
					
					if total_radius_needed > max_distance:
						max_distance = total_radius_needed
			
			# Process polygon children
			for polygon_child in polygon_children_with_coords:
				farthest_distance = _calculate_farthest_polygon_point_static(
					parent.center_latitude, parent.center_longitude,
					polygon_child["coordinates"]
				)
				
				if farthest_distance > max_distance:
					max_distance = farthest_distance
			
			if max_distance > 0:
				# Add a small buffer (1% or minimum 10 meters) to ensure all children are fully covered
				buffer = max(max_distance * 0.01, 10)
				parent.radius = max_distance + buffer
		
		parent.flags.ignore_validate = True
		parent.flags.ignore_links = True
		parent.save(ignore_permissions=True)
		
		# Recursively update parent's parent if it exists
		if parent.parent_area:
			try:
				# Recursively call for parent's parent with increased depth
				recalculate_parent_circle(parent.parent_area, depth=depth + 1, max_depth=max_depth)
			except Exception as e:
				frappe.log_error(f"Error recursively recalculating parent's parent for {parent.name}: {str(e)}", "Geo Fencing Area Recalculate Parent")
		
	except Exception as e:
		frappe.log_error(f"Error recalculating parent circle {parent_name}: {str(e)}", "Geo Fencing Area Recalculate Parent")


def _calculate_distance_static(lat1, lon1, lat2, lon2):
	"""Static version of distance calculation for use in standalone function"""
	R = 6371000
	
	lat1_rad = math.radians(lat1)
	lon1_rad = math.radians(lon1)
	lat2_rad = math.radians(lat2)
	lon2_rad = math.radians(lon2)
	
	dlat = lat2_rad - lat1_rad
	dlon = lon2_rad - lon1_rad
	
	a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
	c = 2 * math.asin(math.sqrt(a))
	
	return R * c


def _calculate_polygon_centroid_static(coords):
	"""
	Calculate the centroid (center point) of a polygon (static version)
	Returns (latitude, longitude) tuple or None if invalid
	"""
	if not coords or len(coords) < 3:
		return None
	
	total_lat = 0
	total_lng = 0
	count = 0
	
	for coord in coords:
		lat = coord.get("latitude") or coord.get("lat")
		lng = coord.get("longitude") or coord.get("lng")
		
		if lat is not None and lng is not None:
			total_lat += float(lat)
			total_lng += float(lng)
			count += 1
	
	if count > 0:
		return (total_lat / count, total_lng / count)
	
	return None


def _calculate_farthest_polygon_point_static(center_lat, center_lng, coords):
	"""
	Calculate the farthest point of a polygon from the given center (static version)
	Returns distance in meters
	"""
	if not coords or len(coords) < 3:
		return 0
	
	max_distance = 0
	
	for coord in coords:
		lat = coord.get("latitude") or coord.get("lat")
		lng = coord.get("longitude") or coord.get("lng")
		
		if lat is not None and lng is not None:
			distance = _calculate_distance_static(
				center_lat, center_lng,
				float(lat), float(lng)
			)
			
			if distance > max_distance:
				max_distance = distance
	
	return max_distance


@frappe.whitelist()
def recalculate_circle_from_children(area_name):
	"""
	Recalculate circle center and radius from its children
	Returns the calculated center_latitude, center_longitude, and radius
	"""
	try:
		# Get the area document
		area_doc = frappe.get_doc("Geo Fencing Area", area_name)
		
		# Check if it's a circle
		if area_doc.shape_type != "Circle":
			frappe.throw(f"Area {area_name} is not a Circle. Cannot recalculate.")
		
		# Get all children
		children = frappe.get_all(
			"Geo Fencing Area",
			filters={"parent_area": area_name},
			fields=["name", "shape_type", "center_latitude", "center_longitude", "radius"]
		)
		
		if not children:
			frappe.throw(f"No children found for area {area_name}. Cannot recalculate.")
		
		# Separate children by shape type
		circle_children = [child for child in children if child.get("shape_type") == "Circle"]
		polygon_children = [child for child in children if child.get("shape_type") == "Polygon"]
		
		if not circle_children and not polygon_children:
			frappe.throw(f"No valid children found for area {area_name}. Cannot recalculate.")
		
		# Fetch polygon coordinates for polygon children
		polygon_children_with_coords = []
		for polygon_child in polygon_children:
			try:
				polygon_doc = frappe.get_doc("Geo Fencing Area", polygon_child["name"])
				if polygon_doc.geo_fencing_coordinates and len(polygon_doc.geo_fencing_coordinates) >= 3:
					# Sort coordinates by sequence
					coords = sorted(polygon_doc.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
					polygon_children_with_coords.append({
						"name": polygon_child["name"],
						"coordinates": coords
					})
			except Exception as e:
				frappe.log_error(f"Error fetching polygon coordinates for {polygon_child['name']}: {str(e)}", "Geo Fencing Area Recalculate")
				continue
		
		# Calculate center from children (average of all child centers/centroids)
		total_lat = 0
		total_lng = 0
		valid_children = 0
		
		# Add circle children centers
		for child in circle_children:
			child_lat = child.get("center_latitude")
			child_lng = child.get("center_longitude")
			
			if child_lat is not None and child_lng is not None:
				total_lat += float(child_lat)
				total_lng += float(child_lng)
				valid_children += 1
		
		# Add polygon children centroids
		for polygon_child in polygon_children_with_coords:
			centroid = _calculate_polygon_centroid_static(polygon_child["coordinates"])
			if centroid:
				total_lat += centroid[0]
				total_lng += centroid[1]
				valid_children += 1
		
		if valid_children == 0:
			frappe.throw(f"No valid children with coordinates found for area {area_name}.")
		
		calculated_center_lat = total_lat / valid_children
		calculated_center_lng = total_lng / valid_children
		
		# Calculate radius to cover all children
		max_distance = 0
		
		# Process circle children
		for child in circle_children:
			child_lat = child.get("center_latitude")
			child_lng = child.get("center_longitude")
			child_radius = child.get("radius") or 0
			
			if child_lat is not None and child_lng is not None:
				# Calculate distance from calculated center to child center
				distance = _calculate_distance_static(
					calculated_center_lat, calculated_center_lng,
					float(child_lat), float(child_lng)
				)
				
				# Total radius needed = distance to child center + child's radius
				total_radius_needed = distance + float(child_radius)
				
				if total_radius_needed > max_distance:
					max_distance = total_radius_needed
		
		# Process polygon children
		for polygon_child in polygon_children_with_coords:
			# Find the farthest point of the polygon from calculated center
			farthest_distance = _calculate_farthest_polygon_point_static(
				calculated_center_lat, calculated_center_lng,
				polygon_child["coordinates"]
			)
			
			if farthest_distance > max_distance:
				max_distance = farthest_distance
		
		# Add a small buffer (1% or minimum 10 meters) to ensure all children are fully covered
		buffer = max(max_distance * 0.01, 10) if max_distance > 0 else 10
		calculated_radius = max_distance + buffer if max_distance > 0 else 100
		
		# Return the calculated values
		return {
			"center_latitude": calculated_center_lat,
			"center_longitude": calculated_center_lng,
			"radius": calculated_radius
		}
		
	except Exception as e:
		frappe.log_error(f"Error recalculating circle from children for {area_name}: {str(e)}", "Geo Fencing Area Recalculate")
		frappe.throw(f"Error recalculating circle: {str(e)}")


class GeoFencingArea(Document):
	def after_insert(self):
		"""Update parent circle if auto-calculate is enabled"""
		# Use enqueue to ensure parent recalculation happens after transaction commits
		# This ensures the new child is fully saved before parent recalculation
		if self.parent_area:
			try:
				parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
				if parent.shape_type == "Circle":
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
						parent_name=parent.name,
						queue="short",
						now=False
					)
			except Exception as e:
				frappe.log_error(f"Error scheduling parent recalculation on insert: {str(e)}", "Geo Fencing Area Insert")
	
	def on_update(self):
		"""Update parent circle if auto-calculate is enabled"""
		# Check if parent_area changed
		if hasattr(self, '_old_parent_area') and self._old_parent_area != self.parent_area:
			# Update old parent if it exists and was a circle
			if self._old_parent_area:
				try:
					old_parent = frappe.get_doc("Geo Fencing Area", self._old_parent_area)
					if old_parent.shape_type == "Circle":
						frappe.enqueue(
							"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
							parent_name=old_parent.name,
							queue="short",
							now=False
						)
				except Exception:
					pass  # Old parent might not exist anymore
		
		# Update new parent using enqueue to ensure it happens after transaction commits
		# This also ensures recursive updates happen properly
		if self.parent_area:
			try:
				parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
				if parent.shape_type == "Circle":
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
						parent_name=parent.name,
						queue="short",
						now=False
					)
			except Exception as e:
				frappe.log_error(f"Error scheduling parent recalculation on update: {str(e)}", "Geo Fencing Area Update")
	
	def before_save(self):
		"""Set level sequence based on geo fencing type"""
		# Store old parent_area before save to detect changes
		if self.is_new():
			self._old_parent_area = None
		else:
			# Get old parent_area from database
			self._old_parent_area = frappe.db.get_value("Geo Fencing Area", self.name, "parent_area")
		
		self.set_level_sequence()
		self.fetch_shape_type()
		self.calculate_area()
	
	def on_trash(self):
		"""Update parent circle when child is deleted"""
		if self.parent_area:
			try:
				parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
				if parent.shape_type == "Circle":
					# Recalculate parent after this child is deleted
					# Use enqueue to avoid issues during delete transaction
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
						parent_name=parent.name,
						queue="short",
						now=False
					)
			except Exception as e:
				frappe.log_error(f"Error scheduling parent recalculation on delete: {str(e)}", "Geo Fencing Area Delete")
	
	def fetch_shape_type(self):
		"""Fetch shape type from linked Geo Fencing Type"""
		if self.geo_fencing_type and not self.shape_type:
			geo_fencing_type_doc = frappe.get_doc("Geo Fencing Type", self.geo_fencing_type)
			if geo_fencing_type_doc.shape_type:
				self.shape_type = geo_fencing_type_doc.shape_type
	
	def set_level_sequence(self):
		"""Set level sequence based on the hierarchy:
		Farm (1) -> Cluster (2) -> Field (3) -> Plot (4) -> Block (5) -> Row (6)
		"""
		level_map = {
			"Farm": 1,
			"Cluster": 2,
			"Field": 3,
			"Plot": 4,
			"Block": 5,
			"Row": 6
		}
		
		if self.geo_fencing_type:
			self.level_sequence = level_map.get(self.geo_fencing_type, 0)
	
	def validate(self):
		"""Validate document based on shape type"""
		# Fetch shape type from linked Geo Fencing Type if not set
		if not self.shape_type and self.geo_fencing_type:
			self.fetch_shape_type()
		
		if not self.shape_type:
			frappe.throw("Shape Type is required. Please ensure the selected Geo Fencing Type has a Shape Type defined.")
		
		if self.shape_type == "Circle":
			# Center and radius are optional - can be set later or calculated from children
			# Only validate if they are provided
			if self.center_latitude is not None and self.center_longitude is not None:
				# If center is provided, radius should be positive if provided
				if self.radius is not None and self.radius <= 0:
					frappe.throw("Radius must be greater than 0 for Circle shape")
		
		elif self.shape_type == "Polygon":
			if not self.geo_fencing_coordinates or len(self.geo_fencing_coordinates) < 3:
				frappe.throw("At least 3 coordinates are required for Polygon shape")
			# Calculate area for polygon
			self.calculate_area()
	
	def calculate_area(self):
		"""Calculate area based on shape type"""
		if self.shape_type == "Polygon" and self.geo_fencing_coordinates:
			# Sort coordinates by sequence if sequence is provided
			coords = sorted(self.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
			
			if len(coords) < 3:
				self.area = 0
				return
			
			# Calculate area using spherical excess formula (accurate for geographic coordinates)
			self.area = self._calculate_polygon_area_spherical(coords)
		
		elif self.shape_type == "Circle" and self.radius:
			# Calculate area for circle: π * r²
			self.area = math.pi * (self.radius ** 2)
		else:
			self.area = 0
	
	def _calculate_polygon_area_spherical(self, coords):
		"""
		Calculate polygon area using spherical excess formula
		Uses the shoelace formula adapted for geographic coordinates
		Returns area in square meters
		"""
		# Earth's radius in meters
		R = 6371000  # meters
		
		if len(coords) < 3:
			return 0
		
		# Convert coordinates to radians
		points = []
		for coord in coords:
			lat = math.radians(float(coord.latitude))
			lon = math.radians(float(coord.longitude))
			points.append((lat, lon))
		
		# Ensure polygon is closed
		if points[0] != points[-1]:
			points.append(points[0])
		
		n = len(points) - 1
		
		# Calculate area using spherical excess
		# Using the formula: Area = R² * sum of (lon2 - lon1) * (2 + sin(lat1) + sin(lat2)) / 2
		# This is a simplified spherical excess calculation
		area = 0
		for i in range(n):
			lat1, lon1 = points[i]
			lat2, lon2 = points[i + 1]
			
			# Calculate the area contribution
			# The factor accounts for the Earth's curvature
			area += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
		
		# Convert to square meters and take absolute value
		area = abs(area) * (R ** 2) / 2
		
		return area
	
	def update_parent_circle(self, depth=0, max_depth=10):
		"""
		Update parent circle center and radius based on children if auto-calculate is enabled
		Recursively updates parent's parent up to max_depth to prevent infinite loops
		"""
		if not self.parent_area:
			return
		
		# Prevent infinite recursion
		if depth >= max_depth:
			frappe.log_error(f"Maximum recursion depth ({max_depth}) reached while updating parent circles", "Geo Fencing Area Update Parent")
			return
		
		try:
			# Get parent area
			parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
			
			# Check if parent is a circle
			if parent.shape_type != "Circle":
				return
			
			# Get parent's geo fencing type to check auto-calculate flags
			if not parent.geo_fencing_type:
				return
			
			geo_fencing_type_doc = frappe.get_doc("Geo Fencing Type", parent.geo_fencing_type)
			
			# Check if auto-calculate flags are enabled
			auto_calculate_center = geo_fencing_type_doc.get("auto_calculate_center_by_child", 0)
			auto_calculate_radius = geo_fencing_type_doc.get("auto_calculate_radius_by_child", 0)
			
			if not auto_calculate_center and not auto_calculate_radius:
				return
			
			# Get all children of the parent
			children = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": parent.name},
				fields=["name", "shape_type", "center_latitude", "center_longitude", "radius"]
			)
			
			if not children:
				return
			
			# Separate children by shape type
			circle_children = [child for child in children if child.get("shape_type") == "Circle"]
			polygon_children = [child for child in children if child.get("shape_type") == "Polygon"]
			
			if not circle_children and not polygon_children:
				return
			
			# Fetch polygon coordinates for polygon children
			polygon_children_with_coords = []
			for polygon_child in polygon_children:
				try:
					polygon_doc = frappe.get_doc("Geo Fencing Area", polygon_child["name"])
					if polygon_doc.geo_fencing_coordinates and len(polygon_doc.geo_fencing_coordinates) >= 3:
						# Sort coordinates by sequence
						coords = sorted(polygon_doc.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
						polygon_children_with_coords.append({
							"name": polygon_child["name"],
							"coordinates": coords
						})
				except Exception as e:
					frappe.log_error(f"Error fetching polygon coordinates for {polygon_child['name']}: {str(e)}", "Geo Fencing Area Update Parent")
					continue
			
			# Calculate center from children (average of all child centers/centroids)
			if auto_calculate_center:
				total_lat = 0
				total_lng = 0
				valid_children = 0
				
				# Add circle children centers
				for child in circle_children:
					child_lat = child.get("center_latitude")
					child_lng = child.get("center_longitude")
					
					if child_lat is not None and child_lng is not None:
						total_lat += float(child_lat)
						total_lng += float(child_lng)
						valid_children += 1
				
				# Add polygon children centroids
				for polygon_child in polygon_children_with_coords:
					centroid = self._calculate_polygon_centroid(polygon_child["coordinates"])
					if centroid:
						total_lat += centroid[0]
						total_lng += centroid[1]
						valid_children += 1
				
				if valid_children > 0:
					parent.center_latitude = total_lat / valid_children
					parent.center_longitude = total_lng / valid_children
			
			# Calculate radius to cover all children
			if auto_calculate_radius and parent.center_latitude and parent.center_longitude:
				max_distance = 0
				
				# Process circle children
				for child in circle_children:
					child_lat = child.get("center_latitude")
					child_lng = child.get("center_longitude")
					child_radius = child.get("radius") or 0
					
					if child_lat is not None and child_lng is not None:
						# Calculate distance from parent center to child center
						distance = self._calculate_distance(
							parent.center_latitude, parent.center_longitude,
							float(child_lat), float(child_lng)
						)
						
						# Total radius needed = distance to child center + child's radius
						total_radius_needed = distance + float(child_radius)
						
						if total_radius_needed > max_distance:
							max_distance = total_radius_needed
				
				# Process polygon children
				for polygon_child in polygon_children_with_coords:
					# Find the farthest point of the polygon from parent center
					farthest_distance = self._calculate_farthest_polygon_point(
						parent.center_latitude, parent.center_longitude,
						polygon_child["coordinates"]
					)
					
					if farthest_distance > max_distance:
						max_distance = farthest_distance
				
				if max_distance > 0:
					# Add a small buffer (1% or minimum 10 meters) to ensure all children are fully covered
					buffer = max(max_distance * 0.01, 10)
					parent.radius = max_distance + buffer
			
			# Save parent without triggering hooks to avoid infinite recursion
			parent.flags.ignore_validate = True
			parent.flags.ignore_links = True
			parent.save(ignore_permissions=True)
			
			# Recursively update parent's parent if it exists and has auto-calculate enabled
			if parent.parent_area:
				try:
					parent_doc = frappe.get_doc("Geo Fencing Area", parent.name)
					# Recursively update parent's parent with increased depth
					parent_doc.update_parent_circle(depth=depth + 1, max_depth=max_depth)
				except Exception as e:
					frappe.log_error(f"Error recursively updating parent's parent for {parent.name}: {str(e)}", "Geo Fencing Area Update Parent")
			
		except Exception as e:
			# Log error but don't fail the save
			frappe.log_error(f"Error updating parent circle for {self.name}: {str(e)}", "Geo Fencing Area Update Parent")
	
	def _calculate_distance(self, lat1, lon1, lat2, lon2):
		"""
		Calculate distance between two points using Haversine formula
		Returns distance in meters
		"""
		# Earth's radius in meters
		R = 6371000
		
		# Convert to radians
		lat1_rad = math.radians(lat1)
		lon1_rad = math.radians(lon1)
		lat2_rad = math.radians(lat2)
		lon2_rad = math.radians(lon2)
		
		# Haversine formula
		dlat = lat2_rad - lat1_rad
		dlon = lon2_rad - lon1_rad
		
		a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
		c = 2 * math.asin(math.sqrt(a))
		
		distance = R * c
		
		return distance
	
	def _calculate_polygon_centroid(self, coords):
		"""
		Calculate the centroid (center point) of a polygon
		Returns (latitude, longitude) tuple or None if invalid
		"""
		if not coords or len(coords) < 3:
			return None
		
		total_lat = 0
		total_lng = 0
		count = 0
		
		for coord in coords:
			lat = coord.get("latitude") or coord.get("lat")
			lng = coord.get("longitude") or coord.get("lng")
			
			if lat is not None and lng is not None:
				total_lat += float(lat)
				total_lng += float(lng)
				count += 1
		
		if count > 0:
			return (total_lat / count, total_lng / count)
		
		return None
	
	def _calculate_farthest_polygon_point(self, center_lat, center_lng, coords):
		"""
		Calculate the farthest point of a polygon from the given center
		Returns distance in meters
		"""
		if not coords or len(coords) < 3:
			return 0
		
		max_distance = 0
		
		for coord in coords:
			lat = coord.get("latitude") or coord.get("lat")
			lng = coord.get("longitude") or coord.get("lng")
			
			if lat is not None and lng is not None:
				distance = self._calculate_distance(
					center_lat, center_lng,
					float(lat), float(lng)
				)
				
				if distance > max_distance:
					max_distance = distance
		
		return max_distance

# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import math


def recalculate_parent_area(parent_name, depth=0, max_depth=10):
	"""
	Standalone function to recalculate parent area as sum of children's areas
	For Cluster: sums all child Fields' areas
	For Farm: sums all child Clusters' areas
	Can be called from queue
	Recursively updates parent's parent up to max_depth
	"""
	# Prevent infinite recursion
	if depth >= max_depth:
		frappe.log_error(f"Maximum recursion depth ({max_depth}) reached while recalculating parent areas", "Geo Fencing Area Recalculate Parent Area")
		return
	
	try:
		parent = frappe.get_doc("Geo Fencing Area", parent_name)
		
		if not parent.geo_fencing_type:
			return
		
		# Only recalculate for Cluster and Farm
		if parent.geo_fencing_type not in ["Cluster", "Farm"]:
			return
		
		# Get all children based on parent type
		if parent.geo_fencing_type == "Cluster":
			# Sum all child Fields' areas
			children = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": parent.name, "geo_fencing_type": "Field"},
				fields=["name", "area"]
			)
		elif parent.geo_fencing_type == "Farm":
			# Sum all child Clusters' areas
			children = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": parent.name, "geo_fencing_type": "Cluster"},
				fields=["name", "area"]
			)
		else:
			return
		
		# Calculate total area from children
		total_area = 0
		for child in children:
			child_area = child.get("area")
			if child_area is not None:
				try:
					total_area += float(child_area)
				except (ValueError, TypeError):
					# Skip invalid area values
					continue
		
		# Update parent area
		parent.area = total_area
		parent.flags.ignore_validate = True
		parent.flags.ignore_links = True
		parent.save(ignore_permissions=True)
		
		# Recursively update parent's parent if it exists
		# If this is a Cluster, update its parent Farm
		if parent.parent_area and parent.geo_fencing_type == "Cluster":
			try:
				recalculate_parent_area(parent.parent_area, depth=depth + 1, max_depth=max_depth)
			except Exception as e:
				frappe.log_error(f"Error recursively recalculating parent's parent for {parent.name}: {str(e)}", "Geo Fencing Area Recalculate Parent Area")
		
	except Exception as e:
		frappe.log_error(f"Error recalculating parent area {parent_name}: {str(e)}", "Geo Fencing Area Recalculate Parent Area")


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
def get_google_maps_api_key():
	"""Get Google Maps API key from site config"""
	return frappe.conf.get('google_maps_api_key') or ''

@frappe.whitelist()
def update_warehouse_geo_fence_area_for_area(area_name):
	"""Standalone function to update warehouse geo_fence_area field for a given Geo Fencing Area.
	
	This function can be called via enqueue to ensure updates happen after transaction commits.
	"""
	try:
		if not area_name:
			return
		
		# Get all warehouses linked to this Geo Fencing Area
		linked_warehouses = frappe.get_all(
			"Geo Fencing Area Warehouse",
			filters={
				"parent": area_name,
				"parenttype": "Geo Fencing Area"
			},
			fields=["warehouse"],
			pluck="warehouse"
		)
		
		# Update each warehouse's geo_fence_area field
		for warehouse_name in linked_warehouses:
			if warehouse_name and frappe.db.exists("Warehouse", warehouse_name):
				try:
					# Use SQL UPDATE to directly modify the database
					# This bypasses Frappe's field validation
					frappe.db.sql("""
						UPDATE `tabWarehouse`
						SET `custom_geo_fence_area` = %s
						WHERE `name` = %s
					""", (area_name, warehouse_name))
				except Exception as e:
					error_msg = str(e)
					wh_short = warehouse_name[:20] if len(warehouse_name) > 20 else warehouse_name
					area_short = area_name[:20] if len(area_name) > 20 else area_name
					# Check if error is about missing column
					if "Unknown column" in error_msg or "doesn't exist" in error_msg.lower():
						frappe.log_error(
							f"Field custom_geo_fence_area does not exist in Warehouse table. Add it as a custom field first. Warehouse: {wh_short}, Area: {area_short}",
							"Geo Fencing Area Update Warehouse Geo Fence Area - Missing Field"
						)
					else:
						frappe.log_error(
							f"Error updating custom_geo_fence_area for {wh_short} in {area_short}: {error_msg[:100]}",
							"Geo Fencing Area Update Warehouse Geo Fence Area"
						)
		
		# Commit the changes
		frappe.db.commit()
	except Exception as e:
		area_short = area_name[:20] if area_name and len(area_name) > 20 else (area_name or "Unknown")
		frappe.log_error(
			f"Error in update_warehouse_geo_fence_area_for_area for {area_short}: {str(e)[:80]}",
			"Geo Fencing Area Update Warehouse Geo Fence Area"
		)

@frappe.whitelist()
def validate_warehouse_location(area_name, latitude, longitude):
	"""
	Validate if a warehouse location is inside the current area's geo fencing and parent area's geo fencing
	
	Args:
		area_name: Name of the Geo Fencing Area where warehouse is being added
		latitude: Latitude of the warehouse location
		longitude: Longitude of the warehouse location
	
	Returns:
		dict: {
			"valid": bool,
			"message": str,
			"parent_area_name": str or None,
			"parent_area_display_name": str or None
		}
	"""
	try:
		# Convert inputs to float
		latitude = float(latitude)
		longitude = float(longitude)
		
		# Get the area document
		area_doc = frappe.get_doc("Geo Fencing Area", area_name)
		area_display_name = area_doc.area_name or area_doc.name
		
		# First, validate against the current area's geo-fencing
		if area_doc.shape_type == "Circle":
			if area_doc.center_latitude and area_doc.center_longitude and area_doc.radius:
				distance = _calculate_distance_static(
					area_doc.center_latitude,
					area_doc.center_longitude,
					latitude,
					longitude
				)
				
				if distance > area_doc.radius:
					return {
						"valid": False,
						"message": f"Location is outside the current area '{area_display_name}' geo fencing. Distance: {distance:.2f}m, Radius: {area_doc.radius:.2f}m",
						"parent_area_name": None,
						"parent_area_display_name": None
					}
		elif area_doc.shape_type == "Polygon":
			if area_doc.geo_fencing_coordinates and len(area_doc.geo_fencing_coordinates) >= 3:
				coords = sorted(area_doc.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
				polygon = [(float(coord.latitude), float(coord.longitude)) for coord in coords]
				is_inside = _is_point_in_polygon(latitude, longitude, polygon)
				
				if not is_inside:
					return {
						"valid": False,
						"message": f"Location is outside the current area '{area_display_name}' geo fencing.",
						"parent_area_name": None,
						"parent_area_display_name": None
					}
		
		# If current area validation passed, check parent area (if exists)
		if not area_doc.parent_area:
			return {
				"valid": True,
				"message": f"Location is inside area '{area_display_name}'.",
				"parent_area_name": None,
				"parent_area_display_name": None
			}
		
		# Get parent area document
		parent_area_doc = frappe.get_doc("Geo Fencing Area", area_doc.parent_area)
		parent_area_name = parent_area_doc.area_name or parent_area_doc.name
		
		# Validate based on parent area's shape type
		if parent_area_doc.shape_type == "Circle":
			# Check if location is inside circle
			if not parent_area_doc.center_latitude or not parent_area_doc.center_longitude or not parent_area_doc.radius:
				return {
					"valid": True,
					"message": "Parent area circle data incomplete. Validation skipped.",
					"parent_area_name": parent_area_doc.name,
					"parent_area_display_name": parent_area_name
				}
			
			# Calculate distance from circle center to warehouse location
			distance = _calculate_distance_static(
				parent_area_doc.center_latitude,
				parent_area_doc.center_longitude,
				latitude,
				longitude
			)
			
			if distance <= parent_area_doc.radius:
				return {
					"valid": True,
					"message": f"Location is inside area '{area_display_name}' and parent area '{parent_area_name}'.",
					"parent_area_name": parent_area_doc.name,
					"parent_area_display_name": parent_area_name
				}
			else:
				return {
					"valid": False,
					"message": f"Location is outside parent area '{parent_area_name}' geo fencing. Distance: {distance:.2f}m, Radius: {parent_area_doc.radius:.2f}m",
					"parent_area_name": parent_area_doc.name,
					"parent_area_display_name": parent_area_name
				}
		
		elif parent_area_doc.shape_type == "Polygon":
			# Check if location is inside polygon
			if not parent_area_doc.geo_fencing_coordinates or len(parent_area_doc.geo_fencing_coordinates) < 3:
				return {
					"valid": True,
					"message": "Parent area polygon data incomplete. Validation skipped.",
					"parent_area_name": parent_area_doc.name,
					"parent_area_display_name": parent_area_name
				}
			
			# Get polygon coordinates
			coords = sorted(parent_area_doc.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
			polygon = [(float(coord.latitude), float(coord.longitude)) for coord in coords]
			
			# Check if point is inside polygon using ray-casting algorithm
			is_inside = _is_point_in_polygon(latitude, longitude, polygon)
			
			if is_inside:
				return {
					"valid": True,
					"message": f"Location is inside area '{area_display_name}' and parent area '{parent_area_name}'.",
					"parent_area_name": parent_area_doc.name,
					"parent_area_display_name": parent_area_name
				}
			else:
				return {
					"valid": False,
					"message": f"Location is outside parent area '{parent_area_name}' geo fencing.",
					"parent_area_name": parent_area_doc.name,
					"parent_area_display_name": parent_area_name
				}
		
		else:
			# Unknown shape type
			return {
				"valid": True,
				"message": f"Parent area has unknown shape type '{parent_area_doc.shape_type}'. Validation skipped.",
				"parent_area_name": parent_area_doc.name,
				"parent_area_display_name": parent_area_name
			}
	
	except frappe.DoesNotExistError:
		return {
			"valid": False,
			"message": f"Area '{area_name}' not found.",
			"parent_area_name": None,
			"parent_area_display_name": None
		}
	except ValueError as e:
		return {
			"valid": False,
			"message": f"Invalid coordinates: {str(e)}",
			"parent_area_name": None,
			"parent_area_display_name": None
		}
	except Exception as e:
		frappe.log_error(f"Error validating warehouse location: {str(e)}", "Warehouse Location Validation")
		return {
			"valid": False,
			"message": f"Error validating location: {str(e)}",
			"parent_area_name": None,
			"parent_area_display_name": None
		}


def _is_point_in_polygon(lat, lon, polygon):
	"""
	Check if a point is inside a polygon using ray-casting algorithm
	
	Args:
		lat: Latitude of the point
		lon: Longitude of the point
		polygon: List of (latitude, longitude) tuples forming the polygon
	
	Returns:
		bool: True if point is inside polygon, False otherwise
	"""
	if not polygon or len(polygon) < 3:
		return False
	
	# For ray-casting, we use longitude as x and latitude as y
	x, y = lon, lat
	inside = False
	
	# Ray-casting algorithm
	j = len(polygon) - 1
	for i in range(len(polygon)):
		# polygon[i] is (lat, lon), so polygon[i][1] is lon (x) and polygon[i][0] is lat (y)
		xi, yi = polygon[i][1], polygon[i][0]
		xj, yj = polygon[j][1], polygon[j][0]
		
		# Check if ray intersects with edge
		intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
		if intersect:
			inside = not inside
		
		j = i
	
	return inside

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
		"""Update parent circle if auto-calculate is enabled and create warehouse if needed"""
		# Create warehouse if geo_fencing_type has_warehouse is true
		self.create_warehouse_if_needed()
		
		# Update warehouse geo_fence_area field for newly created area
		# Use enqueue to ensure it happens after transaction commits
		if self.name:
			frappe.enqueue(
				"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.update_warehouse_geo_fence_area_for_area",
				area_name=self.name,
				queue="short",
				now=False
			)
		
		# Use enqueue to ensure parent recalculation happens after transaction commits
		# This ensures the new child is fully saved before parent recalculation
		if self.parent_area:
			try:
				parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
				# Update parent circle if it's a circle
				if parent.shape_type == "Circle":
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
						parent_name=parent.name,
						queue="short",
						now=False
					)
				# Update parent area if it's a Cluster or Farm
				if parent.geo_fencing_type in ["Cluster", "Farm"]:
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_area",
						parent_name=parent.name,
						queue="short",
						now=False
					)
			except Exception as e:
				frappe.log_error(f"Error scheduling parent recalculation on insert: {str(e)}", "Geo Fencing Area Insert")
	
	def create_warehouse_if_needed(self):
		"""Create warehouse if geo_fencing_type has_warehouse is true
		
		Warehouse hierarchy: Farm -> Cluster -> Field
		- Farm and Cluster warehouses are group warehouses (is_group = 1)
		- Field and other warehouses are ledger warehouses (is_group = 0)
		
		For Farm and Cluster (group warehouses), automatically creates a child ledger warehouse
		named "{area_name} - Stock" to allow transactions (stock entries, asset transfers).
		"""
		try:
			if not self.geo_fencing_type:
				return
			
			# Get geo_fencing_type document
			geo_fencing_type_doc = frappe.get_doc("Geo Fencing Type", self.geo_fencing_type)
			
			# Check if has_warehouse is true
			if not geo_fencing_type_doc.get("has_warehouse"):
				return
			
			# Set has_warehouse to true for this area
			self.has_warehouse = 1
			
			# Determine if warehouse should be a group warehouse
			# Farm and Cluster are group warehouses (can have children)
			# Field and others are ledger warehouses (cannot have children)
			is_group = 1 if self.geo_fencing_type in ["Farm", "Cluster"] else 0
			
			# Get parent warehouse if parent_area exists
			parent_warehouse = None
			if self.parent_area:
				try:
					parent_area_doc = frappe.get_doc("Geo Fencing Area", self.parent_area)
					if parent_area_doc.has_warehouse and parent_area_doc.warehouses:
						# Get the first warehouse from parent area
						parent_warehouse = parent_area_doc.warehouses[0].warehouse
				except Exception as e:
					frappe.log_error(f"Error getting parent warehouse for {self.name}: {str(e)}", "Geo Fencing Area Create Warehouse")
			
			# Create a new Warehouse document
			warehouse_data = {
				"doctype": "Warehouse",
				"warehouse_name": self.area_name,
				"is_group": is_group
			}
			
			# Set parent_warehouse if parent area has a warehouse
			# Hierarchy: Farm -> Cluster -> Field
			if parent_warehouse:
				warehouse_data["parent_warehouse"] = parent_warehouse
			
			# Set custom_geo_fence_area to link warehouse back to this Geo Fencing Area
			warehouse_data["custom_geo_fence_area"] = self.name
			
			warehouse = frappe.get_doc(warehouse_data)
			warehouse.insert(ignore_permissions=True)
			
			# Calculate center coordinates and radius based on shape type
			center_latitude = None
			center_longitude = None
			radius = None
			
			if self.shape_type == "Circle":
				# For Circle: use center_latitude, center_longitude, and radius if available
				if self.center_latitude is not None and self.center_longitude is not None:
					center_latitude = float(self.center_latitude)
					center_longitude = float(self.center_longitude)
					if self.radius is not None:
						radius = float(self.radius)
			
			elif self.shape_type == "Polygon":
				# For Polygon: calculate centroid and farthest point distance
				if self.geo_fencing_coordinates and len(self.geo_fencing_coordinates) >= 3:
					# Sort coordinates by sequence
					coords = sorted(self.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
					
					# Convert child table rows to dict-like format for centroid calculation
					# The _calculate_polygon_centroid method expects objects with .get() method
					coords_for_calc = []
					for coord in coords:
						# Child table rows have .latitude and .longitude attributes
						coords_for_calc.append({
							"latitude": coord.latitude,
							"longitude": coord.longitude
						})
					
					# Calculate centroid
					centroid = self._calculate_polygon_centroid(coords_for_calc)
					if centroid:
						center_latitude = centroid[0]
						center_longitude = centroid[1]
						
						# Calculate radius as distance to farthest point from centroid
						farthest_distance = self._calculate_farthest_polygon_point(
							center_latitude, center_longitude, coords_for_calc
						)
						if farthest_distance > 0:
							# Add a small buffer (1% or minimum 10 meters) to ensure full coverage
							buffer = max(farthest_distance * 0.01, 10)
							radius = farthest_distance + buffer
			
			# Add warehouse to the warehouses child table with calculated location values
			warehouse_row = {
				"warehouse": warehouse.name
			}
			
			# Set location values if calculated
			if center_latitude is not None:
				warehouse_row["center_latitude"] = center_latitude
			if center_longitude is not None:
				warehouse_row["center_longitude"] = center_longitude
			if radius is not None:
				warehouse_row["radius"] = radius
			
			self.append("warehouses", warehouse_row)
			
			# If this is a group warehouse (Farm or Cluster), create a child ledger warehouse
			# This allows items and assets to be stored in Farm/Cluster warehouses
			if is_group == 1:
				# Get company from parent warehouse or default
				company = None
				if parent_warehouse:
					company = frappe.db.get_value("Warehouse", parent_warehouse, "company")
				
				if not company:
					# Try to get from the newly created warehouse
					company = warehouse.company if hasattr(warehouse, 'company') and warehouse.company else None
				
				if not company:
					# Get default company
					try:
						company = frappe.defaults.get_global_default("company")
					except:
						pass
					
					if not company:
						try:
							company = frappe.db.get_single_value("Global Defaults", "default_company")
						except:
							pass
				
				# Create child ledger warehouse
				child_warehouse_name = f"{self.area_name} - Stock"
				child_warehouse_data = {
					"doctype": "Warehouse",
					"warehouse_name": child_warehouse_name,
					"is_group": 0,  # Ledger warehouse
					"parent_warehouse": warehouse.name,
					"custom_geo_fence_area": self.name,
				}
				
				if company:
					child_warehouse_data["company"] = company
				
				child_warehouse = frappe.get_doc(child_warehouse_data)
				child_warehouse.insert(ignore_permissions=True)
				
				# Add child warehouse to warehouses child table with same location values
				child_warehouse_row = {
					"warehouse": child_warehouse.name
				}
				
				if center_latitude is not None:
					child_warehouse_row["center_latitude"] = center_latitude
				if center_longitude is not None:
					child_warehouse_row["center_longitude"] = center_longitude
				if radius is not None:
					child_warehouse_row["radius"] = radius
				
				self.append("warehouses", child_warehouse_row)
			
			# Save the document to persist the changes
			self.save(ignore_permissions=True)
			
			# Create Location record for this Geo Fencing Area
			# Use enqueue to avoid blocking the main transaction
			if self.name:
				frappe.enqueue(
					"f2c.farm_to_crop.location_sync.create_location_for_geo_area",
					geo_area_name=self.name,
					update_existing=False,
					queue="short",
					now=False
				)
			
			frappe.msgprint(f"Warehouse '{warehouse.name}' created and linked to area '{self.area_name}'")
			
		except Exception as e:
			frappe.log_error(f"Error creating warehouse for {self.name}: {str(e)}", "Geo Fencing Area Create Warehouse")
	
	def update_warehouse_parent_if_needed(self):
		"""Update warehouse's parent_warehouse when parent_area changes.
		
		When a field's parent cluster changes, update the field warehouse's parent_warehouse
		to match the new cluster's warehouse.
		"""
		try:
			# Only update if this area has a warehouse
			if not self.has_warehouse or not self.warehouses:
				return
			
			# Get the warehouse for this area (first warehouse in the list)
			warehouse_name = self.warehouses[0].warehouse if self.warehouses else None
			if not warehouse_name:
				return
			
			# Get the new parent warehouse
			new_parent_warehouse = None
			if self.parent_area:
				try:
					parent_area_doc = frappe.get_doc("Geo Fencing Area", self.parent_area)
					if parent_area_doc.has_warehouse and parent_area_doc.warehouses:
						# Get the first warehouse from parent area
						new_parent_warehouse = parent_area_doc.warehouses[0].warehouse
				except Exception as e:
					frappe.log_error(f"Error getting parent warehouse for {self.name}: {str(e)}", "Geo Fencing Area Update Warehouse Parent")
					return
			
			# Update the warehouse's parent_warehouse
			try:
				warehouse_doc = frappe.get_doc("Warehouse", warehouse_name)
				old_parent = warehouse_doc.parent_warehouse
				
				if warehouse_doc.parent_warehouse != new_parent_warehouse:
					warehouse_doc.parent_warehouse = new_parent_warehouse
					warehouse_doc.flags.ignore_validate = True
					warehouse_doc.save(ignore_permissions=True)
					
					# Shorten log message to avoid CharacterLengthExceededError (140 char limit)
					old_parent_str = old_parent or "None"
					new_parent_str = new_parent_warehouse or "None"
					old_area = getattr(self, '_old_parent_area', None) or "None"
					frappe.log_error(
						f"Updated {warehouse_name} parent: {old_parent_str} -> {new_parent_str} (area {self.name}, parent: {old_area} -> {self.parent_area or 'None'})",
						"Geo Fencing Area Update Warehouse Parent"
					)
			except Exception as e:
				frappe.log_error(f"Error updating warehouse {warehouse_name} parent_warehouse: {str(e)}", "Geo Fencing Area Update Warehouse Parent")
		except Exception as e:
			frappe.log_error(f"Error in update_warehouse_parent_if_needed for {self.name}: {str(e)}", "Geo Fencing Area Update Warehouse Parent")
	
	def update_warehouse_geo_location_if_needed(self):
		"""Update warehouse geo location values (center_latitude, center_longitude, radius) if they are 0 or empty.
		
		This method calculates and fills in missing location values based on the area's shape type:
		- Circle: Uses center_latitude, center_longitude, and radius from the area
		- Polygon: Calculates centroid and farthest point distance
		"""
		try:
			# Only update if this area has warehouses
			if not self.has_warehouse or not self.warehouses:
				return
			
			# Calculate center coordinates and radius based on shape type
			center_latitude = None
			center_longitude = None
			radius = None
			
			if self.shape_type == "Circle":
				# For Circle: use center_latitude, center_longitude, and radius if available
				if self.center_latitude is not None and self.center_longitude is not None:
					center_latitude = float(self.center_latitude)
					center_longitude = float(self.center_longitude)
					if self.radius is not None:
						radius = float(self.radius)
			
			elif self.shape_type == "Polygon":
				# For Polygon: calculate centroid and farthest point distance
				if self.geo_fencing_coordinates and len(self.geo_fencing_coordinates) >= 3:
					# Sort coordinates by sequence
					coords = sorted(self.geo_fencing_coordinates, key=lambda x: x.sequence or 0)
					
					# Convert child table rows to dict-like format for centroid calculation
					coords_for_calc = []
					for coord in coords:
						# Child table rows have .latitude and .longitude attributes
						if coord.latitude is not None and coord.longitude is not None:
							coords_for_calc.append({
								"latitude": coord.latitude,
								"longitude": coord.longitude
							})
					
					if len(coords_for_calc) >= 3:
						# Calculate centroid
						centroid = self._calculate_polygon_centroid(coords_for_calc)
						if centroid:
							center_latitude = centroid[0]
							center_longitude = centroid[1]
							
							# Calculate radius as distance to farthest point from centroid
							farthest_distance = self._calculate_farthest_polygon_point(
								center_latitude, center_longitude, coords_for_calc
							)
							if farthest_distance > 0:
								# Add a small buffer (1% or minimum 10 meters) to ensure full coverage
								buffer = max(farthest_distance * 0.01, 10)
								radius = farthest_distance + buffer
			
			# If we have calculated values, update warehouses that have 0 or empty values
			if center_latitude is not None or center_longitude is not None or radius is not None:
				updated = False
				for warehouse_row in self.warehouses:
					needs_update = False
					
					# Check if values are 0, None, or empty
					if center_latitude is not None:
						current_lat = warehouse_row.center_latitude
						if current_lat is None or current_lat == 0:
							warehouse_row.center_latitude = center_latitude
							needs_update = True
					
					if center_longitude is not None:
						current_lng = warehouse_row.center_longitude
						if current_lng is None or current_lng == 0:
							warehouse_row.center_longitude = center_longitude
							needs_update = True
					
					if radius is not None:
						current_radius = warehouse_row.radius
						if current_radius is None or current_radius == 0:
							warehouse_row.radius = radius
							needs_update = True
					
					if needs_update:
						updated = True
				
				if updated:
					# Shorten log message to avoid CharacterLengthExceededError (140 char limit)
					area_name_short = self.name[:20] if len(self.name) > 20 else self.name
					shape_short = self.shape_type[:5] if self.shape_type else "?"
					lat_val = f"{center_latitude:.4f}" if center_latitude is not None else "0"
					lng_val = f"{center_longitude:.4f}" if center_longitude is not None else "0"
					rad_val = f"{radius:.0f}" if radius is not None else "0"
					frappe.log_error(
						f"Updated warehouse geo loc for {area_name_short} ({shape_short}): lat={lat_val}, lng={lng_val}, r={rad_val}",
						"Geo Fencing Area Update Warehouse Location"
					)
		except Exception as e:
			frappe.log_error(f"Error in update_warehouse_geo_location_if_needed for {self.name}: {str(e)}", "Geo Fencing Area Update Warehouse Location")
	
	def update_warehouse_geo_fence_area(self):
		"""Update warehouse geo_fence_area field to sync with Geo Fencing Area Warehouse child table.
		
		This method ensures that when warehouses are linked/unlinked from a Geo Fencing Area,
		their geo_fence_area field is automatically updated to maintain bidirectional synchronization.
		When a Geo Fencing Area is updated, all linked warehouses are updated to reference it.
		"""
		try:
			# Get the document before save to detect changes in warehouses child table
			doc_before_save = self.get_doc_before_save()
			
			# Get current warehouses from child table
			current_warehouses = set()
			if self.has_warehouse and self.warehouses:
				for warehouse_row in self.warehouses:
					if warehouse_row.warehouse:
						current_warehouses.add(warehouse_row.warehouse)
			
			# Also query the database to get all warehouses linked to this Geo Fencing Area
			# This ensures we catch all linked warehouses even if child table isn't fully loaded
			if self.name:
				db_linked_warehouses = frappe.get_all(
					"Geo Fencing Area Warehouse",
					filters={
						"parent": self.name,
						"parenttype": "Geo Fencing Area"
					},
					fields=["warehouse"],
					pluck="warehouse"
				)
				# Add to current_warehouses set
				for wh in db_linked_warehouses:
					if wh:
						current_warehouses.add(wh)
			
			# Get previous warehouses if document existed before
			previous_warehouses = set()
			if doc_before_save and doc_before_save.has_warehouse and doc_before_save.warehouses:
				for warehouse_row in doc_before_save.warehouses:
					if warehouse_row.warehouse:
						previous_warehouses.add(warehouse_row.warehouse)
			
			# Update geo_fence_area for all current warehouses
			# Always update to ensure consistency, even if the value is already correct
			for warehouse_name in current_warehouses:
				try:
					# Use SQL to directly update the field in the database
					# This works even if the field isn't recognized by the doctype class
					# Check if warehouse exists first
					if frappe.db.exists("Warehouse", warehouse_name):
						# Use SQL UPDATE to directly modify the database
						# This bypasses Frappe's field validation
						frappe.db.sql("""
							UPDATE `tabWarehouse`
							SET `custom_geo_fence_area` = %s
							WHERE `name` = %s
						""", (self.name, warehouse_name))
						frappe.db.commit()
				except Exception as e:
					# Log error but don't block the update
					wh_short = warehouse_name[:20] if len(warehouse_name) > 20 else warehouse_name
					area_short = self.name[:20] if len(self.name) > 20 else self.name
					error_msg = str(e)
					# Check if error is about missing column
					if "Unknown column" in error_msg or "doesn't exist" in error_msg.lower():
						frappe.log_error(
							f"Field custom_geo_fence_area does not exist in Warehouse table. Add it as a custom field first. Warehouse: {wh_short}, Area: {area_short}",
							"Geo Fencing Area Update Warehouse Geo Fence Area - Missing Field"
						)
					else:
						frappe.log_error(
							f"Error updating custom_geo_fence_area for {wh_short} in {area_short}: {error_msg[:100]}",
							"Geo Fencing Area Update Warehouse Geo Fence Area"
						)
			
			# Handle warehouses that were removed
			removed_warehouses = previous_warehouses - current_warehouses
			for warehouse_name in removed_warehouses:
				try:
					# Check if this warehouse is linked to any other Geo Fencing Area
					other_links = frappe.get_all(
						"Geo Fencing Area Warehouse",
						filters={
							"warehouse": warehouse_name,
							"parent": ["!=", self.name]
						},
						limit=1
					)
					
					# Get current custom_geo_fence_area value
					current_geo_fence_area = frappe.db.get_value("Warehouse", warehouse_name, "custom_geo_fence_area")
					
					# If not linked to any other Geo Fencing Area, clear the field
					if not other_links and current_geo_fence_area == self.name:
						if frappe.db.exists("Warehouse", warehouse_name):
							frappe.db.sql("""
								UPDATE `tabWarehouse`
								SET `custom_geo_fence_area` = NULL
								WHERE `name` = %s
							""", warehouse_name)
				except Exception as e:
					# Log error but don't block the update
					wh_short = warehouse_name[:20] if len(warehouse_name) > 20 else warehouse_name
					area_short = self.name[:20] if len(self.name) > 20 else self.name
					frappe.log_error(
						f"Error clearing custom_geo_fence_area for {wh_short} from {area_short}: {str(e)[:50]}",
						"Geo Fencing Area Update Warehouse Geo Fence Area"
					)
		
		except Exception as e:
			area_short = self.name[:20] if len(self.name) > 20 else self.name
			frappe.log_error(
				f"Error in update_warehouse_geo_fence_area for {area_short}: {str(e)[:80]}",
				"Geo Fencing Area Update Warehouse Geo Fence Area"
			)
	
	def on_update(self):
		"""Update parent circle if auto-calculate is enabled, and update parent area if hierarchical calculation is needed.
		Also update warehouse parent_warehouse when parent_area changes.
		Update warehouse geo location values if they are 0 or empty.
		Update warehouse geo_fence_area field to sync with child table.
		Ensure Location record exists for this Geo Fencing Area if it has warehouses."""
		# Update warehouse geo location values if they are missing or 0
		self.update_warehouse_geo_location_if_needed()
		
		# Update warehouse geo_fence_area field to sync with warehouses child table
		# Call synchronously first, then also enqueue as backup to ensure it happens
		self.update_warehouse_geo_fence_area()
		
		# Also enqueue to ensure update happens after transaction commits (backup)
		if self.name:
			frappe.enqueue(
				"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.update_warehouse_geo_fence_area_for_area",
				area_name=self.name,
				queue="short",
				now=False
			)
		
		# Ensure Location record exists if this area has warehouses
		# This handles cases where warehouses are manually added later
		if self.has_warehouse and self.warehouses and len(self.warehouses) > 0:
			if self.name:
				frappe.enqueue(
					"f2c.farm_to_crop.location_sync.create_location_for_geo_area",
					geo_area_name=self.name,
					update_existing=False,
					queue="short",
					now=False
				)
		
		# Check if parent_area changed
		if hasattr(self, '_old_parent_area') and self._old_parent_area != self.parent_area:
			# Update warehouse parent_warehouse if this area has a warehouse
			self.update_warehouse_parent_if_needed()
			
			# Update old parent if it exists
			if self._old_parent_area:
				try:
					old_parent = frappe.get_doc("Geo Fencing Area", self._old_parent_area)
					# Update old parent circle if it's a circle
					if old_parent.shape_type == "Circle":
						frappe.enqueue(
							"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
							parent_name=old_parent.name,
							queue="short",
							now=False
						)
					# Update old parent area if it's a Cluster or Farm
					if old_parent.geo_fencing_type in ["Cluster", "Farm"]:
						frappe.enqueue(
							"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_area",
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
				# Update parent circle if it's a circle
				if parent.shape_type == "Circle":
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
						parent_name=parent.name,
						queue="short",
						now=False
					)
				# Update parent area if it's a Cluster or Farm
				if parent.geo_fencing_type in ["Cluster", "Farm"]:
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_area",
						parent_name=parent.name,
						queue="short",
						now=False
					)
			except Exception as e:
				frappe.log_error(f"Error scheduling parent recalculation on update: {str(e)}", "Geo Fencing Area Update")
		
		# If this is a Field or Cluster and its area changed, trigger parent area recalculation
		# This handles the case where area is recalculated in before_save
		if self.geo_fencing_type in ["Field", "Cluster"] and self.parent_area:
			try:
				parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
				if parent.geo_fencing_type in ["Cluster", "Farm"]:
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_area",
						parent_name=parent.name,
						queue="short",
						now=False
					)
			except Exception as e:
				frappe.log_error(f"Error scheduling parent area recalculation on update: {str(e)}", "Geo Fencing Area Update")
	
	def before_save(self):
		"""Set level sequence based on geo fencing type"""
		# Store old parent_area before save to detect changes
		if self.is_new():
			self._old_parent_area = None
			# Set has_warehouse based on geo_fencing_type
			self.set_has_warehouse_from_type()
		else:
			# Get old parent_area from database
			self._old_parent_area = frappe.db.get_value("Geo Fencing Area", self.name, "parent_area")
		
		self.set_level_sequence()
		self.fetch_shape_type()
		self.calculate_area()
		
		# Update warehouse geo location values if they are 0 or empty
		# Do this in before_save to ensure values are set before document is saved
		self.update_warehouse_geo_location_if_needed()
		
		# Create stock warehouses for newly linked Farm/Cluster warehouses
		self.create_stock_warehouses_for_linked_warehouses()
	
	def create_stock_warehouses_for_linked_warehouses(self):
		"""Create stock warehouses for newly linked Farm/Cluster group warehouses.
		
		When a Farm or Cluster warehouse is manually linked to a Geo Fencing Area,
		automatically create and add its child stock warehouse to the warehouses child table.
		"""
		try:
			if not self.has_warehouse or not self.warehouses:
				return
			
			# Get previous warehouses if document existed before
			previous_warehouses = set()
			if not self.is_new() and self.name:
				try:
					previous_warehouse_rows = frappe.get_all(
						"Geo Fencing Area Warehouse",
						filters={
							"parent": self.name,
							"parenttype": "Geo Fencing Area"
						},
						fields=["warehouse"]
					)
					for row in previous_warehouse_rows:
						if row.warehouse:
							previous_warehouses.add(row.warehouse)
				except Exception as e:
					frappe.log_error(f"Error getting previous warehouses: {str(e)}", "Geo Fencing Area Create Stock Warehouses")
			
			# Get current warehouses from child table
			current_warehouses = set()
			warehouse_row_map = {}  # Map warehouse name to row data
			current_warehouse_names = set()  # Track all warehouse names for duplicate checking
			for warehouse_row in self.warehouses:
				if warehouse_row.warehouse:
					current_warehouses.add(warehouse_row.warehouse)
					warehouse_row_map[warehouse_row.warehouse] = warehouse_row
					# Also track warehouse names (without company suffix) for duplicate detection
					wh_name = warehouse_row.warehouse
					# Remove company suffix if present
					if " - " in wh_name:
						parts = wh_name.split(" - ")
						if len(parts) > 1 and len(parts[-1]) <= 3:
							current_warehouse_names.add(" - ".join(parts[:-1]))
						else:
							current_warehouse_names.add(wh_name)
					else:
						current_warehouse_names.add(wh_name)
			
			# Find newly added warehouses
			new_warehouses = current_warehouses - previous_warehouses
			
			# For each new warehouse, check if it's a group warehouse (Farm/Cluster)
			for warehouse_name in new_warehouses:
				try:
					# Check if warehouse exists and is a group warehouse
					if not frappe.db.exists("Warehouse", warehouse_name):
						continue
					
					warehouse_doc = frappe.get_doc("Warehouse", warehouse_name)
					
					# Only create stock warehouse for group warehouses
					if not warehouse_doc.is_group:
						continue
					
					# Check if this warehouse is linked to a Farm or Cluster Geo Fencing Area
					# by checking the warehouse's custom_geo_fence_area or by checking if parent area is Farm/Cluster
					is_farm_or_cluster = False
					if self.geo_fencing_type in ["Farm", "Cluster"]:
						is_farm_or_cluster = True
					else:
						# Check if warehouse is linked to a Farm/Cluster area
						linked_area = warehouse_doc.get("custom_geo_fence_area")
						if linked_area:
							try:
								area_doc = frappe.get_doc("Geo Fencing Area", linked_area)
								if area_doc.geo_fencing_type in ["Farm", "Cluster"]:
									is_farm_or_cluster = True
							except:
								pass
					
					if not is_farm_or_cluster:
						continue
					
					# Check if stock warehouse already exists
					warehouse_name_base = warehouse_doc.warehouse_name
					# Remove company suffix if present (e.g., "Farm1 - O" -> "Farm1")
					if " - " in warehouse_name_base:
						parts = warehouse_name_base.split(" - ")
						if len(parts) > 1:
							# Check if last part is a company abbreviation (usually 1-3 chars)
							last_part = parts[-1]
							if len(last_part) <= 3:
								warehouse_name_base = " - ".join(parts[:-1])
					
					stock_warehouse_name = f"{warehouse_name_base} - Stock"
					
					# First check if stock warehouse is already in the current child table
					# This prevents duplicates when warehouse was created via create_warehouse_if_needed
					stock_warehouse_in_table = False
					existing_stock_warehouse_in_table = None
					for row in self.warehouses:
						if row.warehouse:
							# Check if this warehouse matches the stock warehouse name pattern
							row_wh_name = row.warehouse
							# Remove company suffix if present
							row_base_name = row_wh_name
							if " - " in row_wh_name:
								parts = row_wh_name.split(" - ")
								if len(parts) > 1 and len(parts[-1]) <= 3:
									row_base_name = " - ".join(parts[:-1])
							
							# Check if it matches stock warehouse name pattern
							if row_base_name == stock_warehouse_name or row_base_name.endswith(" - Stock"):
								# Verify it's a child of the parent warehouse
								try:
									row_wh_doc = frappe.get_doc("Warehouse", row.warehouse)
									if row_wh_doc.parent_warehouse == warehouse_name:
										stock_warehouse_in_table = True
										existing_stock_warehouse_in_table = row.warehouse
										break
								except:
									# If we can't verify, skip to avoid errors
									pass
					
					# If already in table, skip creating/adding
					if stock_warehouse_in_table:
						continue
					
					# Check if stock warehouse already exists as a child of this warehouse
					existing_stock_warehouse = existing_stock_warehouse_in_table
					company = warehouse_doc.company if hasattr(warehouse_doc, 'company') and warehouse_doc.company else None
					
					# First, try to find existing child warehouses of this parent warehouse
					existing_children = frappe.get_all(
						"Warehouse",
						filters={
							"parent_warehouse": warehouse_name,
							"is_group": 0
						},
						fields=["name", "warehouse_name", "company"]
					)
					
					# Check if any child matches the stock warehouse name pattern
					for child in existing_children:
						child_base_name = child.warehouse_name
						# Remove company suffix if present
						if " - " in child_base_name:
							parts = child_base_name.split(" - ")
							if len(parts) > 1 and len(parts[-1]) <= 3:
								child_base_name = " - ".join(parts[:-1])
						
						if child_base_name == stock_warehouse_name or child_base_name.endswith(" - Stock"):
							existing_stock_warehouse = child.name
							break
					
					# If not found in children, try direct lookup
					if not existing_stock_warehouse:
						if company:
							# Try to find with company suffix
							company_abbr = frappe.get_cached_value("Company", company, "abbr")
							stock_warehouse_name_with_suffix = f"{stock_warehouse_name} - {company_abbr}"
							if frappe.db.exists("Warehouse", stock_warehouse_name_with_suffix):
								# Verify it's a child of the parent warehouse
								wh_doc = frappe.get_doc("Warehouse", stock_warehouse_name_with_suffix)
								if wh_doc.parent_warehouse == warehouse_name:
									existing_stock_warehouse = stock_warehouse_name_with_suffix
						else:
							# Try without company suffix
							if frappe.db.exists("Warehouse", stock_warehouse_name):
								# Verify it's a child of the parent warehouse
								wh_doc = frappe.get_doc("Warehouse", stock_warehouse_name)
								if wh_doc.parent_warehouse == warehouse_name:
									existing_stock_warehouse = stock_warehouse_name
					
					# Create stock warehouse if it doesn't exist
					if not existing_stock_warehouse:
						# Get company from parent warehouse or default
						if not company:
							if warehouse_doc.parent_warehouse:
								company = frappe.db.get_value("Warehouse", warehouse_doc.parent_warehouse, "company")
						
						if not company:
							try:
								company = frappe.defaults.get_global_default("company")
							except:
								pass
							
							if not company:
								try:
									company = frappe.db.get_single_value("Global Defaults", "default_company")
								except:
									pass
						
						# Create child ledger warehouse
						child_warehouse_data = {
							"doctype": "Warehouse",
							"warehouse_name": stock_warehouse_name,
							"is_group": 0,  # Ledger warehouse
							"parent_warehouse": warehouse_name,
							"custom_geo_fence_area": self.name,
						}
						
						if company:
							child_warehouse_data["company"] = company
						
						child_warehouse = frappe.get_doc(child_warehouse_data)
						child_warehouse.insert(ignore_permissions=True)
						existing_stock_warehouse = child_warehouse.name
					
					# Add stock warehouse to child table if not already present
					# (stock_warehouse_in_table is already checked above, so this should be safe)
					if existing_stock_warehouse:
						# Get location values from parent warehouse row
						parent_row = warehouse_row_map.get(warehouse_name)
						stock_warehouse_row = {
							"warehouse": existing_stock_warehouse
						}
						
						if parent_row:
							if hasattr(parent_row, 'center_latitude') and parent_row.center_latitude is not None:
								stock_warehouse_row["center_latitude"] = parent_row.center_latitude
							if hasattr(parent_row, 'center_longitude') and parent_row.center_longitude is not None:
								stock_warehouse_row["center_longitude"] = parent_row.center_longitude
							if hasattr(parent_row, 'radius') and parent_row.radius is not None:
								stock_warehouse_row["radius"] = parent_row.radius
						
						self.append("warehouses", stock_warehouse_row)
					
				except Exception as e:
					wh_short = warehouse_name[:20] if len(warehouse_name) > 20 else warehouse_name
					frappe.log_error(
						f"Error creating stock warehouse for {wh_short}: {str(e)}",
						"Geo Fencing Area Create Stock Warehouses"
					)
		
		except Exception as e:
			area_short = self.name[:20] if len(self.name) > 20 else self.name if self.name else "New"
			frappe.log_error(
				f"Error in create_stock_warehouses_for_linked_warehouses for {area_short}: {str(e)}",
				"Geo Fencing Area Create Stock Warehouses"
			)
	
	def set_has_warehouse_from_type(self):
		"""Set has_warehouse based on geo_fencing_type"""
		if self.geo_fencing_type:
			try:
				geo_fencing_type_doc = frappe.get_doc("Geo Fencing Type", self.geo_fencing_type)
				if geo_fencing_type_doc.get("has_warehouse"):
					self.has_warehouse = 1
			except Exception as e:
				frappe.log_error(f"Error setting has_warehouse from type: {str(e)}", "Geo Fencing Area")
	
	def on_trash(self):
		"""Update parent circle and area when child is deleted"""
		if self.parent_area:
			try:
				parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
				# Recalculate parent circle after this child is deleted
				if parent.shape_type == "Circle":
					# Use enqueue to avoid issues during delete transaction
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_circle",
						parent_name=parent.name,
						queue="short",
						now=False
					)
				# Recalculate parent area after this child is deleted
				if parent.geo_fencing_type in ["Cluster", "Farm"]:
					frappe.enqueue(
						"f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.recalculate_parent_area",
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
		Farm (1) -> Cluster (2) -> Field (3) -> Block (4) -> Row (5)
		"""
		level_map = {
			"Farm": 1,
			"Cluster": 2,
			"Field": 3,
			"Block": 4,
			"Row": 5,
			"Plot": 99,  # Deprecated - not used in hierarchy
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
		"""Calculate area based on shape type and geo_fencing_type"""
		# For Cluster and Farm, calculate area as sum of children
		if self.geo_fencing_type == "Cluster":
			# Sum all child Fields' areas
			children = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": self.name, "geo_fencing_type": "Field"},
				fields=["name", "area"]
			)
			total_area = 0
			for child in children:
				child_area = child.get("area")
				if child_area is not None:
					try:
						total_area += float(child_area)
					except (ValueError, TypeError):
						# Skip invalid area values
						continue
			self.area = total_area
			return
		
		elif self.geo_fencing_type == "Farm":
			# Sum all child Clusters' areas
			children = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": self.name, "geo_fencing_type": "Cluster"},
				fields=["name", "area"]
			)
			total_area = 0
			for child in children:
				child_area = child.get("area")
				if child_area is not None:
					try:
						total_area += float(child_area)
					except (ValueError, TypeError):
						# Skip invalid area values
						continue
			self.area = total_area
			return
		
		# For Field and other types, calculate from shape (existing logic)
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

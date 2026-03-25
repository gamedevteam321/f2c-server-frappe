import frappe
from frappe import _
import json
import math


ALLOWED_TYPES = ("Farm", "Cluster", "Field", "Block")


def _build_location_name_from_area(area_name: str) -> str:
	"""
	Build a flat location_name like:
	- Farm
	- Farm-Cluster
	- Farm-Cluster-Field
	- Farm-Cluster-Field-Block

	from a starting Geo Fencing Area by walking parent_area up.
	"""
	seen = []
	cur = frappe.get_doc("Geo Fencing Area", area_name)

	# Walk up to root (safety cap)
	for _ in range(25):
		seen.append(cur)
		if not cur.parent_area:
			break
		cur = frappe.get_doc("Geo Fencing Area", cur.parent_area)

	# root -> leaf
	chain = list(reversed(seen))

	parts = []
	for d in chain:
		if d.geo_fencing_type in ALLOWED_TYPES and d.area_name:
			parts.append(d.area_name.strip())

	# De-dupe consecutive repeats, just in case
	clean = []
	for p in parts:
		if not clean or clean[-1] != p:
			clean.append(p)

	return "-".join(clean)

def _polygon_centroid_lon_lat(lon_lat_points):
	"""
	Compute centroid for a polygon given points as [(lon, lat), ...].
	Uses the standard centroid-of-polygon formula. Falls back to mean if degenerate.
	"""
	if not lon_lat_points or len(lon_lat_points) < 3:
		return None

	# Ensure closed ring for formula
	pts = lon_lat_points[:]
	if pts[0] != pts[-1]:
		pts.append(pts[0])

	area2 = 0.0
	cx = 0.0
	cy = 0.0
	for i in range(len(pts) - 1):
		x0, y0 = pts[i]
		x1, y1 = pts[i + 1]
		cross = x0 * y1 - x1 * y0
		area2 += cross
		cx += (x0 + x1) * cross
		cy += (y0 + y1) * cross

	if abs(area2) < 1e-12:
		# Degenerate: fallback to average
		lons = [p[0] for p in lon_lat_points]
		lats = [p[1] for p in lon_lat_points]
		return (sum(lons) / len(lons), sum(lats) / len(lats))

	area = area2 / 2.0
	cx = cx / (6.0 * area)
	cy = cy / (6.0 * area)
	return (cx, cy)


def _geojson_and_latlng_from_geo_area(geo_area_name: str):
	"""
	Return (geojson_str, latitude, longitude) for ERPNext Location from a Geo Fencing Area.
	- Circle: Point + properties.point_type=circle + properties.radius
	- Polygon: Polygon geometry
	latitude/longitude are filled as the circle center or polygon centroid.
	"""
	area = frappe.get_doc("Geo Fencing Area", geo_area_name)
	shape = (area.shape_type or "").strip()

	if shape == "Circle":
		lat = float(area.center_latitude) if area.center_latitude is not None else None
		lon = float(area.center_longitude) if area.center_longitude is not None else None
		radius = float(area.radius) if area.radius is not None else None
		if lat is None or lon is None:
			return (None, None, None)

		geojson = {
			"type": "FeatureCollection",
			"features": [
				{
					"type": "Feature",
					"properties": {"point_type": "circle", "radius": radius} if radius else {"point_type": "circle"},
					"geometry": {"type": "Point", "coordinates": [lon, lat]},  # geojson: [lon,lat]
				}
			],
		}
		return (json.dumps(geojson), lat, lon)

	# Polygon
	coords = area.get("geo_fencing_coordinates") or []
	if not coords or len(coords) < 3:
		return (None, None, None)

	# Sort by sequence if present
	coords_sorted = sorted(coords, key=lambda r: (r.sequence or 0))
	ring = []
	for r in coords_sorted:
		if r.longitude is None or r.latitude is None:
			continue
		ring.append([float(r.longitude), float(r.latitude)])

	if len(ring) < 3:
		return (None, None, None)

	# Close ring
	if ring[0] != ring[-1]:
		ring.append(ring[0])

	centroid = _polygon_centroid_lon_lat([(p[0], p[1]) for p in ring[:-1]])
	lat = centroid[1] if centroid else None
	lon = centroid[0] if centroid else None

	geojson = {
		"type": "FeatureCollection",
		"features": [
			{
				"type": "Feature",
				"properties": {},
				"geometry": {"type": "Polygon", "coordinates": [ring]},
			}
		],
	}
	return (json.dumps(geojson), lat, lon)


def create_location_for_geo_area(geo_area_name: str, update_existing: bool = False):
	"""
	Create or update a single Location record for a Geo Fencing Area.
	
	Args:
		geo_area_name: Name of the Geo Fencing Area
		update_existing: If True, update existing location; if False, skip if exists
		
	Returns:
		dict with keys: 'success' (bool), 'action' (str: 'created', 'updated', 'skipped', 'error'), 
		'location_name' (str), 'error' (str if failed)
	"""
	try:
		if not geo_area_name:
			return {
				"success": False,
				"action": "error",
				"location_name": None,
				"error": "geo_area_name is required"
			}
		
		# Build location name from area hierarchy
		location_name = _build_location_name_from_area(geo_area_name)
		if not location_name:
			return {
				"success": False,
				"action": "error",
				"location_name": None,
				"error": f"Could not build location name for {geo_area_name}"
			}
		
		# Get geojson and coordinates
		geojson_str, lat, lon = _geojson_and_latlng_from_geo_area(geo_area_name)
		
		# Check if location already exists
		existing_loc = frappe.db.get_value("Location", {"location_name": location_name}, "name")
		
		if existing_loc:
			if not update_existing:
				return {
					"success": True,
					"action": "skipped",
					"location_name": location_name,
					"error": None
				}
			
			# Update existing location
			loc = frappe.get_doc("Location", existing_loc)
			changed = False
			if geojson_str and not loc.location:
				loc.location = geojson_str
				changed = True
			if lat is not None and (loc.latitude is None or (loc.latitude is not None and math.isnan(float(loc.latitude)))):
				loc.latitude = lat
				changed = True
			if lon is not None and (loc.longitude is None or (loc.longitude is not None and math.isnan(float(loc.longitude)))):
				loc.longitude = lon
				changed = True
			
			if changed:
				loc.save(ignore_permissions=True)
				return {
					"success": True,
					"action": "updated",
					"location_name": location_name,
					"error": None
				}
			else:
				return {
					"success": True,
					"action": "skipped",
					"location_name": location_name,
					"error": None
				}
		
		# Create new location
		loc = frappe.get_doc(
			{
				"doctype": "Location",
				"location_name": location_name,
				"is_group": 0,
				"location": geojson_str,
				"latitude": lat,
				"longitude": lon,
			}
		)
		loc.insert(ignore_permissions=True)
		
		return {
			"success": True,
			"action": "created",
			"location_name": location_name,
			"error": None
		}
		
	except Exception as e:
		error_msg = f"Failed to create location for Geo Area {geo_area_name}: {str(e)}"
		frappe.log_error(error_msg, "Location Creation Error")
		return {
			"success": False,
			"action": "error",
			"location_name": None,
			"error": error_msg
		}


@frappe.whitelist()
def create_locations_from_geo_warehouses(update_existing: int = 0):
	"""
	Create ERPNext Location records for ALL Warehouses referenced in
	Geo Fencing Area → Warehouses mappings (Geo Fencing Area Warehouse child table).

	Flat naming based on Geo Fencing Area hierarchy:
	Farm[-Cluster[-Field[-Block]]]
	"""
	frappe.only_for(["System Manager"])

	# Fetch all mappings (warehouse + geo fencing area)
	# In child tables, parent == parent docname (Geo Fencing Area)
	rows = frappe.get_all(
		"Geo Fencing Area Warehouse",
		fields=["warehouse", "parent as geo_fencing_area"],
		filters={"warehouse": ["is", "set"]},
		limit_page_length=0,
	)

	# Distinct pairs to avoid repeated work
	seen_pairs = set()
	pairs = []
	for r in rows:
		key = (r.get("warehouse"), r.get("geo_fencing_area"))
		if not key[0] or not key[1] or key in seen_pairs:
			continue
		seen_pairs.add(key)
		pairs.append(key)

	# Existing location_name values (Location.location_name is unique)
	existing_names = set(
		n for (n,) in frappe.db.sql("select location_name from `tabLocation`", as_list=True) if n
	)
	# Case-insensitive set so we don't create duplicates when only casing differs
	existing_names_lower = set((n or "").lower() for n in existing_names)

	created = 0
	skipped_existing = 0
	updated_existing = 0
	errors = []

	def _get_location_docname_by_name(location_name_str):
		"""Get Location name (docname) by location_name, case-insensitive."""
		if not (location_name_str or "").strip():
			return None
		docname = frappe.db.get_value("Location", {"location_name": location_name_str}, "name")
		if docname:
			return docname
		result = frappe.db.sql(
			"SELECT name FROM `tabLocation` WHERE LOWER(TRIM(location_name)) = LOWER(TRIM(%s)) LIMIT 1",
			(location_name_str,),
			as_dict=False,
		)
		return result[0][0] if result else None

	for warehouse, geo_area in pairs:
		try:
			location_name = _build_location_name_from_area(geo_area)
			if not location_name:
				errors.append(_("Skipped {0}: could not build name").format(geo_area))
				continue

			geojson_str, lat, lon = _geojson_and_latlng_from_geo_area(geo_area)

			if location_name.lower() in existing_names_lower:
				if not int(update_existing):
					skipped_existing += 1
					continue

				# Backfill coords if possible (do not overwrite non-empty fields)
				docname = _get_location_docname_by_name(location_name)
				if not docname:
					skipped_existing += 1
					continue
				loc = frappe.get_doc("Location", docname)
				changed = False
				if geojson_str and not loc.location:
					loc.location = geojson_str
					changed = True
				if lat is not None and (loc.latitude is None or math.isnan(float(loc.latitude))):
					loc.latitude = lat
					changed = True
				if lon is not None and (loc.longitude is None or math.isnan(float(loc.longitude))):
					loc.longitude = lon
					changed = True
				if changed:
					loc.save(ignore_permissions=True)
					updated_existing += 1
				else:
					skipped_existing += 1
				continue

			loc = frappe.get_doc(
				{
					"doctype": "Location",
					"location_name": location_name,
					"is_group": 0,
					"location": geojson_str,
					"latitude": lat,
					"longitude": lon,
				}
			)
			loc.insert(ignore_permissions=True)
			existing_names.add(location_name)
			existing_names_lower.add(location_name.lower())
			created += 1

		except Exception as e:
			errors.append(
				_("Failed for Warehouse {0}, Geo Area {1}: {2}").format(
					warehouse, geo_area, str(e)
				)
			)

	return {
		"created": created,
		"skipped_existing": skipped_existing,
		"updated_existing": updated_existing,
		"errors": errors,
		"pairs_processed": len(pairs),
	}



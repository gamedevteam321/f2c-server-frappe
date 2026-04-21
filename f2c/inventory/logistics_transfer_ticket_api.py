import json
import math
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_datetime_str, now_datetime

from f2c.farm_report.doctype.farm_report_ticket.farm_report_ticket import create_report_and_mark_reported
from f2c.inventory.equipment_location_level import (
	location_warehouse_level_from_location_name as _location_warehouse_level_from_location_name,
)
from f2c.access.field_scope import (
	assert_field_supervisor_ltt_dropoff,
	assert_field_supervisor_ltt_pickup,
	assert_field_supervisor_ltt_read,
	field_supervisor_data_scope_active,
	get_user_scope_expanded_area_names,
)

# Include both Draft (0) and Submitted (1) Assets in inventory views; exclude Cancelled (2)
ASSET_DOCSTATUS_NOT_CANCELLED = [0, 1]


def _field_supervisor_ltt_gate(ticket_doc, *, pickup: bool = False, dropoff: bool = False, any_leg: bool = False) -> None:
	"""Enforce Field Supervisor scope on External LTT mutations (Internal is out of role)."""
	if any_leg:
		assert_field_supervisor_ltt_read(ticket_doc)
		return
	if pickup:
		assert_field_supervisor_ltt_pickup(ticket_doc)
	if dropoff:
		assert_field_supervisor_ltt_dropoff(ticket_doc)


def _location_docnames_under_path_prefix(canonical_location_name: str) -> list[str]:
	"""Location.name rows whose location_name equals the path or is a descendant (Farm → Farm-Cluster-Field)."""
	path = (canonical_location_name or "").strip()
	if not path:
		return []
	rows = frappe.db.sql(
		"""
		SELECT name FROM `tabLocation`
		WHERE location_name = %s OR location_name LIKE %s
		""",
		(path, f"{path}-%"),
	)
	return [r[0] for r in rows] if rows else []


def _f2c_enforce_logistics_location() -> bool:
	"""When F2C Settings disables enforcement, relax client/server gates (e.g. receive before Delivered)."""
	if not frappe.db.exists("DocType", "F2C Settings"):
		return True
	v = frappe.db.get_value("F2C Settings", "F2C Settings", "enforce_logistics_location_check")
	return cint(v) == 1


def _normalize_photo_urls(value):
	"""Accept single URL string, list of URLs, or JSON string; return JSON string of list of URL strings."""
	if value is None:
		return None
	if isinstance(value, list):
		urls = [str(u).strip() for u in value if u]
		return json.dumps(urls) if urls else None
	if isinstance(value, str):
		val = value.strip()
		if not val:
			return None
		try:
			parsed = json.loads(val)
			if isinstance(parsed, list):
				urls = [str(u).strip() for u in parsed if u]
				return json.dumps(urls) if urls else None
		except (json.JSONDecodeError, TypeError):
			pass
		return json.dumps([val])
	return None


def _dispatch_photo_field_to_url_list(raw) -> list[str]:
	"""Parse Logistics Transfer Ticket.dispatch_photo (JSON list or single URL) into URL strings."""
	if raw is None:
		return []
	if isinstance(raw, list):
		return [str(u).strip() for u in raw if str(u).strip()]
	s = str(raw).strip()
	if not s:
		return []
	try:
		parsed = json.loads(s)
		if isinstance(parsed, list):
			return [str(u).strip() for u in parsed if u and str(u).strip()]
	except (json.JSONDecodeError, TypeError):
		pass
	return [s]


def _merge_replacement_dispatch_photo_on_doc(new_doc, original_ltt, handoff_dispatch_image: str | None) -> bool:
	"""Copy parent dispatch_photo onto the replacement ticket, plus optional extra URL from resolve."""
	seen: set[str] = set()
	urls: list[str] = []
	for u in _dispatch_photo_field_to_url_list(getattr(original_ltt, "dispatch_photo", None)):
		if u and u not in seen:
			seen.add(u)
			urls.append(u)
	extra = (handoff_dispatch_image or "").strip()
	if extra and extra not in seen:
		urls.append(extra)
	merged = _normalize_photo_urls(urls)
	if merged:
		new_doc.dispatch_photo = merged
		return True
	return False


def _geo_area_path_names(geo_area_name: str) -> list[str]:
	"""Return ordered area_name path from root -> leaf by following parent_area."""
	if not geo_area_name:
		return []

	names: list[str] = []
	seen = set()
	current = geo_area_name
	while current and current not in seen:
		seen.add(current)
		area_name, parent = frappe.db.get_value(
			"Geo Fencing Area", current, ["area_name", "parent_area"], as_dict=False
		) or (None, None)
		if area_name:
			names.append(area_name)
		current = parent

	return list(reversed(names))


def _build_location_name_for_geo_area(geo_area_name: str) -> str:
	"""
	Build location_name that matches Location records created by create_locations_from_geo_warehouses.
	Must use the same logic as f2c.farm_to_crop.location_sync._build_location_name_from_area
	(Farm/Cluster/Field/Block only) so warehouse->location lookup finds the correct Location.
	"""
	from f2c.farm_to_crop.location_sync import _build_location_name_from_area
	return _build_location_name_from_area(geo_area_name)


def _get_location_by_name(location_name: str):
	"""
	Return Location docname for the given location_name.
	Tries exact match first, then case-insensitive match so that differing casing
	between Geo Fencing Area (area_name) and stored Location does not break lookup.
	"""
	if not (location_name or "").strip():
		return None
	# Exact match first (fast path)
	loc = frappe.db.get_value("Location", {"location_name": location_name}, "name")
	if loc:
		return loc
	# Case-insensitive fallback (e.g. prod DB collation or data entry differs from local)
	result = frappe.db.sql(
		"SELECT name FROM `tabLocation` WHERE LOWER(TRIM(location_name)) = LOWER(TRIM(%s)) LIMIT 1",
		(location_name,),
		as_dict=False,
	)
	return result[0][0] if result else None


@frappe.whitelist()
def get_location_for_warehouse(warehouse: str):
	"""
	Map Warehouse -> Location by looking up the Geo Fencing Area linked to this Warehouse
	and then matching Location.location_name to the derived path string.
	"""
	if not warehouse:
		frappe.throw(_("warehouse is required"))

	def _norm(s: str) -> str:
		return "".join(ch for ch in str(s or "").lower() if ch.isalnum())

	wh_name = frappe.db.get_value("Warehouse", warehouse, "warehouse_name") or ""
	norm_wh = _norm(wh_name) or _norm(warehouse)

	# Prefer Geo Area links, but when multiple exist pick the best match by area_name vs warehouse_name.
	geo_area = None
	links = frappe.get_all(
		"Geo Fencing Area Warehouse",
		fields=["parent"],
		filters={"warehouse": warehouse, "parent": ["is", "set"]},
		order_by="modified desc",
		limit_page_length=0,
		ignore_permissions=True,
	)
	parents = []
	seen = set()
	for l in links:
		p = l.get("parent")
		if p and p not in seen:
			seen.add(p)
			parents.append(p)

	if parents:
		# Default to the most recently modified, but override if we find a better name match.
		geo_area = parents[0]
		if norm_wh:
			best = None
			best_score = -1
			for p in parents:
				area_name = frappe.db.get_value("Geo Fencing Area", p, "area_name") or ""
				norm_area = _norm(area_name)
				score = 0
				if norm_area and norm_area == norm_wh:
					score = 100
				elif norm_area and (norm_area in norm_wh or norm_wh in norm_area):
					score = 50
				if score > best_score:
					best_score = score
					best = p
			if best is not None and best_score > 0:
				geo_area = best

	# Fallback: infer Geo Fencing Area by matching Warehouse.warehouse_name to Geo Fencing Area.area_name
	if not geo_area and norm_wh:
		for g in frappe.get_all(
			"Geo Fencing Area",
			fields=["name", "area_name"],
			limit_page_length=0,
			ignore_permissions=True,
		):
			norm_area = _norm(g.get("area_name") or "")
			if norm_area and norm_area == norm_wh:
				geo_area = g["name"]
				break

	if not geo_area:
		return {"warehouse": warehouse, "geo_area": None, "location": None, "location_name": None}

	location_name = _build_location_name_for_geo_area(geo_area)
	loc = _get_location_by_name(location_name)
	return {"warehouse": warehouse, "geo_area": geo_area, "location": loc, "location_name": location_name}


@frappe.whitelist()
def get_location_for_geo_area(geo_area: str):
	if not geo_area:
		frappe.throw(_("geo_area is required"))
	location_name = _build_location_name_for_geo_area(geo_area)
	loc = _get_location_by_name(location_name)
	return {"geo_area": geo_area, "location": loc, "location_name": location_name}


def _warehouse_lat_lng(warehouse: str | None) -> tuple[float, float] | None:
	"""Return (lat, lng) from Location linked to warehouse, or None if missing."""
	if not warehouse:
		return None
	try:
		location_name = get_location_for_warehouse(warehouse).get("location")
		if not location_name:
			return None
		location_doc = frappe.get_doc("Location", location_name)
		lat, lon = location_doc.latitude, location_doc.longitude
		if lat and lon:
			return (float(flt(lat)), float(flt(lon)))
	except Exception:
		return None
	return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
	"""Great-circle distance in km between two WGS84 points."""
	r = 6371.0
	p1, p2 = math.radians(lat1), math.radians(lat2)
	dlat = math.radians(lat2 - lat1)
	dlon = math.radians(lon2 - lon1)
	a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
	c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
	return r * c


def _get_f2c_ltt_timing_settings() -> dict:
	"""Read LTT timing fields from F2C Settings with safe defaults."""
	defaults = {
		"ltt_schedule_planned_times_enabled": 1,
		"ltt_dropoff_buffer_minutes": 10,
		"ltt_travel_avg_speed_kph": 35.0,
		"ltt_travel_road_factor": 1.25,
		"ltt_travel_min_minutes": 5,
		"ltt_travel_max_minutes": 480,
		"ltt_travel_fallback_minutes": 60,
	}
	if not frappe.db.exists("DocType", "F2C Settings"):
		return defaults
	if not frappe.db.exists("F2C Settings", "F2C Settings"):
		return defaults
	# Read from DB (not get_cached_doc) so toggling LTT timing on F2C Settings applies on the next request without stale cache.
	row = frappe.db.get_value(
		"F2C Settings",
		"F2C Settings",
		[
			"ltt_schedule_planned_times_enabled",
			"ltt_dropoff_buffer_minutes",
			"ltt_travel_avg_speed_kph",
			"ltt_travel_road_factor",
			"ltt_travel_min_minutes",
			"ltt_travel_max_minutes",
			"ltt_travel_fallback_minutes",
		],
		as_dict=True,
	)
	if not row:
		return defaults
	out = dict(defaults)
	# NULL after migrate / never saved: cint(None)==0 would wrongly disable the feature (DocType default is 1).
	_ltt_times_flag = row.get("ltt_schedule_planned_times_enabled")
	if _ltt_times_flag is None:
		out["ltt_schedule_planned_times_enabled"] = defaults["ltt_schedule_planned_times_enabled"]
	else:
		out["ltt_schedule_planned_times_enabled"] = cint(_ltt_times_flag)
	out["ltt_dropoff_buffer_minutes"] = cint(row.get("ltt_dropoff_buffer_minutes")) or 10
	speed = flt(row.get("ltt_travel_avg_speed_kph"))
	out["ltt_travel_avg_speed_kph"] = float(speed) if speed > 0 else 35.0
	factor = flt(row.get("ltt_travel_road_factor"))
	out["ltt_travel_road_factor"] = float(factor) if factor > 0 else 1.25
	tmin = cint(row.get("ltt_travel_min_minutes")) or 5
	tmax = cint(row.get("ltt_travel_max_minutes")) or 480
	if tmax < tmin:
		tmax = tmin
	out["ltt_travel_min_minutes"] = tmin
	out["ltt_travel_max_minutes"] = tmax
	out["ltt_travel_fallback_minutes"] = cint(row.get("ltt_travel_fallback_minutes")) or 60
	return out


def estimate_internal_ltt_travel_minutes(from_warehouse: str | None, to_warehouse: str | None) -> int:
	"""
	Estimated travel minutes between two warehouses from Location coordinates,
	or fallback minutes when either side has no usable lat/lng.
	"""
	settings = _get_f2c_ltt_timing_settings()
	fallback = int(settings["ltt_travel_fallback_minutes"])
	tmin = int(settings["ltt_travel_min_minutes"])
	tmax = int(settings["ltt_travel_max_minutes"])
	a = _warehouse_lat_lng(from_warehouse)
	b = _warehouse_lat_lng(to_warehouse)
	if not a or not b:
		return fallback
	km = _haversine_km(a[0], a[1], b[0], b[1]) * float(settings["ltt_travel_road_factor"])
	speed = float(settings["ltt_travel_avg_speed_kph"])
	if speed <= 0:
		speed = 35.0
	minutes = int(round((km / speed) * 60.0))
	return max(tmin, min(tmax, minutes))


def _min_lead_minutes_before_planned_drop() -> int:
	"""Planned pickup must be at least this many minutes before planned drop-off."""
	return 60


def clamp_planned_pickup_before_drop_str(
	pickup_in,
	drop_in,
	*,
	min_lead_minutes: int | None = None,
) -> tuple[str | None, str | None]:
	"""
	Ensure planned_pickup_on is strictly before planned_drop_off_on by at least min_lead_minutes
	(default 60). Fixes cases where pickup was defaulted to ticket creation after a scheduled drop.
	"""
	if min_lead_minutes is None:
		min_lead_minutes = _min_lead_minutes_before_planned_drop()
	if not drop_in:
		pu = get_datetime(pickup_in) if pickup_in else None
		return (get_datetime_str(pu) if pu else None, None)
	po = get_datetime(drop_in)
	if not po:
		pu = get_datetime(pickup_in) if pickup_in else None
		di = get_datetime(drop_in)
		return (get_datetime_str(pu) if pu else None, get_datetime_str(di) if di else None)
	lim = po - timedelta(minutes=min_lead_minutes)
	pu = get_datetime(pickup_in) if pickup_in else None
	if not pu or pu >= po:
		pu = lim
	elif pu > lim:
		pu = lim
	return get_datetime_str(pu), get_datetime_str(po)


def _planned_pickup_drop_from_activity_anchor(
	activity_start,
	from_warehouse: str | None,
	to_warehouse: str | None,
) -> tuple[str | None, str | None]:
	"""
	Core timing for schedule-linked legs: drop = anchor, pickup = anchor - travel - buffer (clamped).
	Does not consult ltt_schedule_planned_times_enabled (used for tractor↔implement round-trip leg 2
	and for callers that already gated on that flag).
	"""
	settings = _get_f2c_ltt_timing_settings()
	start = get_datetime(activity_start) if activity_start else None
	if not start:
		start = now_datetime()
	lead_m = int(settings["ltt_dropoff_buffer_minutes"])
	drop = start
	travel_m = estimate_internal_ltt_travel_minutes(from_warehouse, to_warehouse)
	pickup = start - timedelta(minutes=travel_m + lead_m)
	if pickup > drop:
		pickup = drop
	return clamp_planned_pickup_before_drop_str(pickup, drop)


def planned_pickup_drop_for_activity_start(
	activity_start,
	from_warehouse: str | None,
	to_warehouse: str | None,
) -> tuple[str, str] | None:
	"""
	Compute (planned_pickup_on, planned_drop_off_on) for internal LTTs tied to a scheduled activity.
	planned_drop_off_on equals the anchor (e.g. Crop Plan Schedule planned_start, or planned_end for returns).
	If the anchor is missing or not parseable, uses current server time so LTT still gets explicit planned times.
	planned_pickup_on is anchor minus estimated travel minus ltt_dropoff_buffer_minutes (pickup lead).
	Returns None when the feature is off (caller should omit datetimes; create_logistics_transfer_ticket uses creation defaults).
	Returns strings so callers can pass them directly to @whitelist functions.
	"""
	try:
		settings = _get_f2c_ltt_timing_settings()
		if not cint(settings.get("ltt_schedule_planned_times_enabled")):
			return None
		return _planned_pickup_drop_from_activity_anchor(activity_start, from_warehouse, to_warehouse)
	except Exception as e:
		frappe.log_error(
			f"planned_pickup_drop_for_activity_start failed\n"
			f"activity_start={activity_start!r}\n"
			f"from_warehouse={from_warehouse!r}\n"
			f"to_warehouse={to_warehouse!r}\n\n"
			f"{e!s}\n\n"
			f"{frappe.get_traceback()}",
			"LTT planned_pickup_drop_for_activity_start",
		)
		raise


def planned_pickup_drop_for_round_trip_leg2_from_schedule(
	activity_start,
	from_warehouse: str | None,
	to_warehouse: str | None,
) -> tuple[str | None, str | None]:
	"""
	Leg 2 of field↔cluster implement round-trip: same anchor math as :func:`planned_pickup_drop_for_activity_start`
	but **always** applied so leg 2 uses activity time even when ``ltt_schedule_planned_times_enabled`` is off.
	"""
	try:
		return _planned_pickup_drop_from_activity_anchor(activity_start, from_warehouse, to_warehouse)
	except Exception as e:
		frappe.log_error(
			f"planned_pickup_drop_for_round_trip_leg2_from_schedule failed\n"
			f"activity_start={activity_start!r}\n"
			f"from_warehouse={from_warehouse!r}\n"
			f"to_warehouse={to_warehouse!r}\n\n"
			f"{e!s}\n\n"
			f"{frappe.get_traceback()}",
			"LTT planned_pickup_drop_round_trip_leg2_from_schedule",
		)
		raise


def planned_pickup_drop_for_round_trip_leg1_immediate(
	from_warehouse: str | None,
	to_warehouse: str | None,
) -> tuple[str, str] | None:
	"""
	Planned times for round-trip **leg 1** (tractor + current implement → cluster): anchor to **now**
	so dispatch/receivable can treat this as the urgent return leg. Leg 2 stays on activity schedule.

	Always computed (ignores ``ltt_schedule_planned_times_enabled``) so the two-leg flow works regardless of that toggle.
	"""
	try:
		settings = _get_f2c_ltt_timing_settings()
		now = now_datetime()
		lead_m = int(settings["ltt_dropoff_buffer_minutes"])
		travel_m = estimate_internal_ltt_travel_minutes(from_warehouse, to_warehouse)
		pickup = now
		min_sep = _min_lead_minutes_before_planned_drop()
		drop_after = travel_m + max(lead_m, 1)
		if drop_after <= min_sep:
			drop_after = min_sep + 1
		drop = pickup + timedelta(minutes=drop_after)
		return clamp_planned_pickup_before_drop_str(pickup, drop)
	except Exception as e:
		frappe.log_error(
			f"planned_pickup_drop_for_round_trip_leg1_immediate failed\n"
			f"from_warehouse={from_warehouse!r}\n"
			f"to_warehouse={to_warehouse!r}\n\n"
			f"{e!s}\n\n"
			f"{frappe.get_traceback()}",
			"LTT planned_pickup_drop_round_trip_leg1_immediate",
		)
		raise


def schedule_ltt_planned_times_enabled() -> bool:
	"""True when F2C Settings says schedule/on-demand should set LTT planned pickup/drop."""
	return bool(cint(_get_f2c_ltt_timing_settings().get("ltt_schedule_planned_times_enabled")))


def planned_internal_ltt_kwargs_from_anchor(
	activity_anchor,
	from_warehouse: str | None,
	to_warehouse: str | None,
) -> dict:
	"""Build planned_pickup_on / planned_drop_off_on kwargs for create_logistics_transfer_ticket (empty if disabled or no anchor)."""
	pt = planned_pickup_drop_for_activity_start(activity_anchor, from_warehouse, to_warehouse)
	if not pt:
		return {}
	return {"planned_pickup_on": pt[0], "planned_drop_off_on": pt[1]}


def execution_anchor_datetime_for_ltt(execution_doc, *, anchor_kind: str):
	"""
	Datetime anchor from linked Crop Plan Schedule / On Demand Activity for execution-scoped internal LTTs.
	anchor_kind:
	  - activity_start: planned_start on the link, else actual_start on the execution.
	  - activity_end: planned_end on the link, else actual_end, else actual_start.
	"""
	if anchor_kind not in ("activity_start", "activity_end"):
		anchor_kind = "activity_start"
	schedule_ref = (getattr(execution_doc, "schedule_ref", None) or "").strip()
	oda_ref = (getattr(execution_doc, "on_demand_activity_ref", None) or "").strip()
	if anchor_kind == "activity_start":
		for doctype, ref, col in (
			("Crop Plan Schedule", schedule_ref, "planned_start"),
			("On Demand Activity", oda_ref, "planned_start"),
		):
			if not ref:
				continue
			v = frappe.db.get_value(doctype, ref, col)
			if v:
				return v
		return getattr(execution_doc, "actual_start", None)
	for doctype, ref, col in (
		("Crop Plan Schedule", schedule_ref, "planned_end"),
		("On Demand Activity", oda_ref, "planned_end"),
	):
		if not ref:
			continue
		v = frappe.db.get_value(doctype, ref, col)
		if v:
			return v
	return getattr(execution_doc, "actual_end", None) or getattr(execution_doc, "actual_start", None)


def update_ltt_planned_times_if_pending_pickup(
	ticket_name: str | None,
	planned_pickup_on,
	planned_drop_off_on,
) -> bool:
	"""
	Update planned pickup/drop on an LTT that is still Pending Pickup (not dispatched).
	Returns True if the document was saved with new times.
	"""
	if not ticket_name or planned_pickup_on is None or planned_drop_off_on is None:
		return False
	try:
		doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	except Exception:
		return False
	if (doc.status or "").strip() != "Pending Pickup":
		return False
	pu = get_datetime(planned_pickup_on)
	po = get_datetime(planned_drop_off_on)
	if not pu or not po:
		return False
	pu_s, po_s = clamp_planned_pickup_before_drop_str(pu, po)
	if not pu_s or not po_s:
		return False
	doc.planned_pickup_on = pu_s
	doc.planned_drop_off_on = po_s
	doc.save(ignore_permissions=True)
	return True


def _ledger_warehouse_names_for_geo_area(geo_area: str, strict_geo_area: int = 0) -> list[str]:
	"""Resolve Geo Fencing Area docname -> ledger Warehouse names (same rules as get_warehouses_for_geo_area)."""
	geo_area = (geo_area or "").strip()
	if not geo_area:
		return []

	warehouses = frappe.get_all(
		"Geo Fencing Area Warehouse",
		fields=["warehouse"],
		filters={"parent": geo_area, "parenttype": "Geo Fencing Area", "warehouse": ["is", "set"]},
		limit_page_length=0,
		ignore_permissions=True,
	)

	linked = [w["warehouse"] for w in warehouses if w.get("warehouse")]

	# Fallback: if Farm/Cluster/Field has a corresponding Warehouse record but isn't linked in the child table,
	# try to infer by matching Geo Fencing Area.area_name to Warehouse.warehouse_name (lenient normalization).
	area_name = frappe.db.get_value("Geo Fencing Area", geo_area, "area_name") or ""
	norm = "".join(ch for ch in area_name.lower() if ch.isalnum())
	fallback = []
	if norm:
		# Fetch non-group warehouses and match normalized warehouse_name or name prefix.
		for wh in frappe.get_all(
			"Warehouse",
			fields=["name", "warehouse_name", "is_group"],
			filters={"is_group": 0},
			limit_page_length=0,
			ignore_permissions=True,
		):
			wh_name = wh.get("warehouse_name") or ""
			wh_norm = "".join(ch for ch in wh_name.lower() if ch.isalnum())
			name_norm = "".join(ch for ch in (wh.get("name") or "").lower() if ch.isalnum())
			if wh_norm == norm or name_norm.startswith(norm):
				fallback.append(wh["name"])

	combined = []
	seen = set()
	for w in linked + fallback:
		if w and w not in seen:
			seen.add(w)
			combined.append(w)

	# If strict, only keep warehouses that truly map back to this geo_area (by our Warehouse->Geo mapping).
	# This prevents wrongly-linked child rows from polluting Farm-level selections (e.g., Cluster warehouse linked on Farm).
	if cint(strict_geo_area):
		filtered: list[str] = []
		for w in combined:
			try:
				res = get_location_for_warehouse(w) or {}
				if res.get("geo_area") == geo_area:
					filtered.append(w)
			except Exception:
				# best-effort filter; keep it out if it can't be resolved
				continue
		combined = filtered

	# Only ledger warehouses (is_group = 0) are valid for transfer; exclude group warehouses from the select list.
	if combined:
		ledger_names = set(
			frappe.get_all(
				"Warehouse",
				filters={"name": ["in", combined], "is_group": 0},
				pluck="name",
				limit_page_length=0,
				ignore_permissions=True,
			)
		)
		combined = [w for w in combined if w in ledger_names]

	return combined


@frappe.whitelist()
def get_warehouses_for_geo_area(geo_area: str, strict_geo_area: int = 0):
	if not geo_area:
		frappe.throw(_("geo_area is required"))
	combined = _ledger_warehouse_names_for_geo_area(geo_area, strict_geo_area)
	return {"geo_area": geo_area, "warehouses": combined}


@frappe.whitelist()
def get_field_supervisor_inventory_warehouse_rows():
	"""
	Ledger warehouses for Field Supervisor inventory UIs (dropdown), across expanded scope Geo areas.
	Ignores Warehouse DocPerm for the final read; names are derived only from scoped Geo Fencing Areas.
	"""
	if not field_supervisor_data_scope_active():
		return []
	areas = get_user_scope_expanded_area_names()
	if not areas:
		return []

	ordered_names: list[str] = []
	seen_wh: set[str] = set()
	for geo in sorted(areas):
		for w in _ledger_warehouse_names_for_geo_area(geo, strict_geo_area=0):
			if w and w not in seen_wh:
				seen_wh.add(w)
				ordered_names.append(w)

	if not ordered_names:
		return []

	rows = frappe.get_all(
		"Warehouse",
		filters={"name": ["in", ordered_names], "is_group": 0, "disabled": 0},
		fields=["name", "warehouse_name"],
		limit_page_length=0,
		ignore_permissions=True,
	)
	by_name = {r["name"]: r for r in rows}
	out: list[dict] = []
	for n in ordered_names:
		row = by_name.get(n)
		if row:
			out.append(row)
	return out


def _get_default_company():
	"""Return first company for tracking-only tickets when both ends are Other."""
	companies = frappe.get_all("Company", pluck="name", limit_page_length=1)
	return companies[0] if companies else None


def _implement_asset_for_tractor_transfer(tractor_asset: str, paired_implement: str | None) -> str | None:
	"""
	Asset name linked to the implement that should move with this tractor.
	Uses Implement doc name from paired_implement when provided; else Machinery.current_implement.
	"""
	if not tractor_asset:
		return None
	pi = (paired_implement or "").strip()
	if pi and frappe.db.exists("Implement", pi):
		return frappe.db.get_value("Implement", pi, "asset")
	cur_impl = frappe.db.get_value("Machinery", {"asset": tractor_asset}, "current_implement")
	if cur_impl and frappe.db.exists("Implement", cur_impl):
		return frappe.db.get_value("Implement", cur_impl, "asset")
	return None


def _append_co_moving_implement_assets(requests: list[dict]) -> list[dict]:
	"""Add implement Asset rows for each tractor row so Asset Movement moves both."""
	seen = {r.get("asset") for r in requests if r.get("asset")}
	out = list(requests)
	for req in requests:
		ta = req.get("asset")
		if not ta:
			continue
		ia = _implement_asset_for_tractor_transfer(ta, req.get("paired_implement"))
		if not ia or ia == ta or ia in seen:
			continue
		seen.add(ia)
		out.append({"asset": ia, "qty": 1})
	return out


def expand_machinery_transfer_asset_requests(
	primary_asset: str,
	paired_implement: str | None = None,
) -> list[dict]:
	"""Expand one primary machinery asset into request dicts including co-moving implement(s), matching create_logistics_transfer_ticket."""
	req: dict = {"asset": primary_asset, "qty": 1}
	pi = (paired_implement or "").strip()
	if pi:
		req["paired_implement"] = pi
	return _append_co_moving_implement_assets([req])


def expected_asset_names_for_machinery_unit(
	primary_asset: str,
	paired_implement: str | None = None,
) -> list[str]:
	"""Asset names (primary + co-moving implements) for duplicate detection and sync."""
	reqs = expand_machinery_transfer_asset_requests(primary_asset, paired_implement)
	return [r["asset"] for r in reqs if r.get("asset")]


def _sync_equipment_location_from_asset(asset_name: str) -> None:
	"""Copy Asset.location onto the linked Machinery / Implement / Hand Tool / Other Tool doc."""
	if not asset_name:
		return
	loc = frappe.db.get_value("Asset", asset_name, "location")
	if not loc:
		return
	for doctype in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		name = frappe.db.get_value(doctype, {"asset": asset_name}, "name")
		if name:
			frappe.db.set_value(doctype, name, "location", loc, update_modified=False)
			return


def _sync_equipment_locations_after_asset_movement(ticket) -> None:
	"""After Asset Movement submit/cancel, refresh equipment doc locations from Asset."""
	seen: set[str] = set()
	for row in getattr(ticket, "asset_items", None) or []:
		an = row.get("asset")
		if not an or an in seen:
			continue
		seen.add(an)
		_sync_equipment_location_from_asset(an)


def _cluster_path_key_for_machinery(machinery_name: str) -> str | None:
	"""Farm–Cluster identity from Asset → Location.location_name (first two path segments)."""
	asset = frappe.db.get_value("Machinery", machinery_name, "asset")
	if not asset:
		return None
	loc = frappe.db.get_value("Asset", asset, "location")
	if not loc:
		return None
	location_name = frappe.db.get_value("Location", loc, "location_name") or ""
	parts = [p.strip() for p in str(location_name).split("-") if p.strip()]
	if len(parts) >= 2:
		return f"{parts[0]}-{parts[1]}"
	return parts[0] if parts else None


def _tractors_in_same_cluster(machinery_a: str, machinery_b: str) -> bool:
	"""True when both tractors resolve to the same Farm–Cluster location key."""
	ka = _cluster_path_key_for_machinery(machinery_a)
	kb = _cluster_path_key_for_machinery(machinery_b)
	if not ka or not kb:
		return False
	return ka == kb


def _detach_implement_from_prior_tractor(implement_name: str, old_machinery_name: str) -> None:
	"""
	Clear Machinery.current_implement and Implement.attached_to_machinery before reassigning
	to another tractor (DB-only; skips Implement/Machinery validate that would block the swap).
	"""
	now = now_datetime()
	cur_on_old = frappe.db.get_value("Machinery", old_machinery_name, "current_implement")
	if cur_on_old == implement_name:
		frappe.db.set_value(
			"Machinery",
			old_machinery_name,
			{
				"current_implement": None,
				"attachment_status": "None",
				"attachment_updated_on": now,
			},
			update_modified=False,
		)
	frappe.db.set_value(
		"Implement",
		implement_name,
		{
			"attached_to_machinery": None,
			"attachment_status": "Detached",
			"attachment_updated_on": now,
		},
		update_modified=False,
	)


def _reapply_tractor_implement_links_after_transfer(ticket) -> None:
	"""
	Ensure Machinery.current_implement and Implement.attached_to_machinery stay consistent
	for ticket rows that record a paired implement (runs Machinery save → existing sync hooks).

	When the implement is still linked to another tractor, detach it from that tractor first
	only if both tractors share the same cluster (Farm–Cluster path). Otherwise skip re-linking
	for this row so cross-cluster swaps stay explicit / manual.
	"""
	frappe.flags.skip_cluster_attachment_validation = True
	try:
		for row in getattr(ticket, "asset_items", None) or []:
			pi = (getattr(row, "paired_implement", None) or "").strip()
			if not pi or not frappe.db.exists("Implement", pi):
				continue
			asset = row.get("asset")
			if not asset:
				continue
			mach_name = frappe.db.get_value("Machinery", {"asset": asset}, "name")
			if not mach_name:
				continue
			m = frappe.get_doc("Machinery", mach_name)
			if (m.machinery_type or "") != "Tractor":
				continue
			impl_owner = frappe.db.get_value("Implement", pi, "attached_to_machinery") or None
			prior_mach = impl_owner
			if not prior_mach:
				prior_mach = frappe.db.get_value("Machinery", {"current_implement": pi}, "name")
			if (m.current_implement or "") == pi and (impl_owner or prior_mach) == mach_name:
				continue
			if prior_mach and prior_mach != mach_name:
				if not _tractors_in_same_cluster(prior_mach, mach_name):
					frappe.logger().info(
						"[LTT] Skip paired implement reassignment %s → %s: tractors %s and %s not in same cluster",
						pi,
						mach_name,
						prior_mach,
						mach_name,
					)
					continue
				_detach_implement_from_prior_tractor(pi, prior_mach)
			m.current_implement = pi
			m.save(ignore_permissions=True)
	finally:
		frappe.flags.skip_cluster_attachment_validation = False


# Machinery types that may legally be both moved asset and transport_vehicle on the same LTT.
SELF_PROPELLED_MACHINERY_TYPES = frozenset(
	{
		"Tractor",
		"Harvester",
		"Combine Harvester",
		"Thresher",
		"Earthmoving",
		"Baler",
		"Tiller",
	}
)


def self_transport_machinery_name_for_asset(asset_name: str | None) -> str | None:
	"""Machinery `name` for self-propelled equipment linked to this Asset (excludes type Vehicle)."""
	if not asset_name:
		return None
	types = list(SELF_PROPELLED_MACHINERY_TYPES)
	names = frappe.get_all(
		"Machinery",
		filters={"asset": asset_name, "machinery_type": ["in", types]},
		pluck="name",
		order_by="modified desc",
		limit_page_length=1,
	)
	return names[0] if names else None


def is_valid_transport_vehicle_machinery_type(machinery_type: str | None) -> bool:
	"""Pickup Vehicle or self-propelled machinery may be LTT transport_vehicle."""
	t = (machinery_type or "").strip()
	if not t:
		return False
	if t == "Vehicle":
		return True
	return t in SELF_PROPELLED_MACHINERY_TYPES


def _default_transport_vehicle_from_assets(assets: list | None) -> str | None:
	"""
	Use moved self-propelled machinery as Transport vehicle when no vehicle was passed.
	Skips implements and other assets with no matching self-propelled Machinery row.
	"""
	if not assets:
		return None
	for a in assets:
		an = a if isinstance(a, str) else (a.get("asset") if isinstance(a, dict) else None)
		if not an:
			continue
		mname = self_transport_machinery_name_for_asset(an)
		if mname:
			return mname
	return None


@frappe.whitelist()
def create_logistics_transfer_ticket(
	from_warehouse: str | None = None,
	to_warehouse: str | None = None,
	stock_items: list[dict] | None = None,
	assets: list | None = None,
	transfer_type: str = "Internal",
	from_location_type: str = "Warehouse",
	to_location_type: str = "Warehouse",
	from_address: str | None = None,
	from_latitude: float | None = None,
	from_longitude: float | None = None,
	to_address: str | None = None,
	to_latitude: float | None = None,
	to_longitude: float | None = None,
	planned_pickup_on: str | None = None,
	planned_drop_off_on: str | None = None,
	purchase_order: str | None = None,
	purchase_invoice: str | None = None,
	sales_order: str | None = None,
	sales_invoice: str | None = None,
	transport_vehicle: str | None = None,
	skip_default_transport_vehicle: bool = False,
	schedule_ref: str | None = None,
	material_transfer_source_warehouse: str | None = None,
):
	"""
	Create ONE Logistics Transfer Ticket.
	When both From and To are Warehouse: creates draft Stock Entry and optionally Asset Movement.
	When either end is Other (external location): no Stock Entry/Asset Movement (tracking-only),
	except if ``material_transfer_source_warehouse`` is set with From=Other and To=Warehouse: then a
	draft Material Transfer Stock Entry is still created from that warehouse to ``to_warehouse`` (GPS
	pickup on the ticket; stock issues from the real warehouse).
	"""
	stock_items = stock_items or []
	assets = assets or []

	if purchase_order and not frappe.db.exists("Purchase Order", purchase_order):
		frappe.throw(_("Purchase Order {0} does not exist").format(purchase_order))
	if purchase_invoice and not frappe.db.exists("Purchase Invoice", purchase_invoice):
		frappe.throw(_("Purchase Invoice {0} does not exist").format(purchase_invoice))
	if sales_order and not frappe.db.exists("Sales Order", sales_order):
		frappe.throw(_("Sales Order {0} does not exist").format(sales_order))
	if sales_invoice and not frappe.db.exists("Sales Invoice", sales_invoice):
		frappe.throw(_("Sales Invoice {0} does not exist").format(sales_invoice))
	if purchase_order and purchase_invoice:
		frappe.throw(_("Set either Purchase Order or Purchase Invoice, not both."))
	if sales_order and sales_invoice:
		frappe.throw(_("Set either Sales Order or Sales Invoice, not both."))
	if (sales_order or sales_invoice) and (purchase_order or purchase_invoice):
		frappe.throw(
			_("Cannot combine Sales Order or Sales Invoice with Purchase Order or Purchase Invoice.")
		)

	# Normalize location types
	from_location_type = (from_location_type or "Warehouse").strip()
	to_location_type = (to_location_type or "Warehouse").strip()
	if from_location_type not in ("Warehouse", "Other"):
		from_location_type = "Warehouse"
	if to_location_type not in ("Warehouse", "Other"):
		to_location_type = "Warehouse"

	# Validation by location type
	if from_location_type == "Warehouse":
		if not from_warehouse:
			frappe.throw(_("From Warehouse is required when From Location Type is Warehouse"))
	else:
		from_warehouse = None
		if from_latitude is None and from_longitude is None:
			frappe.throw(_("From Latitude and Longitude are required when From Location Type is Other"))
		from_lat = flt(from_latitude)
		from_lng = flt(from_longitude)
		if from_lat == 0 and from_lng == 0:
			frappe.throw(_("From Latitude and Longitude are required when From Location Type is Other"))

	if to_location_type == "Warehouse":
		if not to_warehouse:
			frappe.throw(_("To Warehouse is required when To Location Type is Warehouse"))
	else:
		to_warehouse = None
		if to_latitude is None and to_longitude is None:
			frappe.throw(_("To Latitude and Longitude are required when To Location Type is Other"))
		to_lat = flt(to_latitude)
		to_lng = flt(to_longitude)
		if to_lat == 0 and to_lng == 0:
			frappe.throw(_("To Latitude and Longitude are required when To Location Type is Other"))

	if from_location_type == "Warehouse" and to_location_type == "Warehouse":
		if from_warehouse == to_warehouse and assets:
			frappe.throw(_("From and To Warehouse cannot be same"))
		if from_warehouse == to_warehouse and not stock_items:
			frappe.throw(_("From and To Warehouse cannot be same"))
		if not stock_items and not assets:
			frappe.throw(_("Select at least one stock item or asset"))

	# When either end is Other: tracking-only, no SE/AM (unless optional stock source warehouse below)
	both_warehouse = from_location_type == "Warehouse" and to_location_type == "Warehouse"
	mt_sw = (material_transfer_source_warehouse or frappe.form_dict.get("material_transfer_source_warehouse") or "").strip() or None

	stock_entry_source_warehouse: str | None = None
	stock_entry_target_warehouse: str | None = None
	if both_warehouse and from_warehouse and to_warehouse:
		stock_entry_source_warehouse = from_warehouse
		stock_entry_target_warehouse = to_warehouse
	elif (
		from_location_type == "Other"
		and to_location_type == "Warehouse"
		and to_warehouse
		and mt_sw
		and stock_items
	):
		if mt_sw == (to_warehouse or "").strip():
			frappe.throw(_("Material transfer source warehouse must differ from the destination warehouse."))
		if not frappe.db.exists("Warehouse", mt_sw):
			frappe.throw(_("Material transfer source warehouse {0} does not exist").format(mt_sw))
		stock_entry_source_warehouse = mt_sw
		stock_entry_target_warehouse = to_warehouse

	if both_warehouse:
		company = frappe.db.get_value("Warehouse", from_warehouse, "company")
		if not company:
			frappe.throw(_("From Warehouse has no Company"))
		from_loc = get_location_for_warehouse(from_warehouse).get("location")
		to_loc = get_location_for_warehouse(to_warehouse).get("location")
	else:
		company = None
		if from_warehouse:
			company = frappe.db.get_value("Warehouse", from_warehouse, "company")
		if not company and to_warehouse:
			company = frappe.db.get_value("Warehouse", to_warehouse, "company")
		if not company:
			company = _get_default_company()
		if not company:
			frappe.throw(_("Could not determine Company for transfer ticket"))
		from_loc = get_location_for_warehouse(from_warehouse).get("location") if from_warehouse else None
		to_loc = get_location_for_warehouse(to_warehouse).get("location") if to_warehouse else None

	stock_entry_name = None
	asset_movement_name = None
	asset_item_rows_for_ticket: list[dict] = []
	available_items_for_entry = []

	if stock_entry_source_warehouse and stock_entry_target_warehouse and stock_items:
		# Check availability for each item before creating Stock Entry
		# All items (available or not) will still be included in ticket.stock_items below
		for row in stock_items:
			item_code = row.get("item_code")
			qty = flt(row.get("qty"))
			if not item_code or qty <= 0:
				continue
			
			# Check if item is available at source warehouse
			try:
				from erpnext.stock.utils import get_stock_balance
				from erpnext.stock.stock_ledger import is_negative_stock_allowed
				
				# Get item details to check if it's a stock item
				is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")
				
				if is_stock_item:
					available_qty = get_stock_balance(item_code, stock_entry_source_warehouse)
					allow_negative = is_negative_stock_allowed(item_code=item_code)
					
					# Include in Stock Entry if:
					# - Item is available (qty > 0), OR
					# - Negative stock is allowed (even if qty is 0 or negative)
					if available_qty > 0 or allow_negative:
						available_items_for_entry.append({
							"doctype": "Stock Entry Detail",
							"item_code": item_code,
							"qty": min(qty, available_qty) if available_qty > 0 else qty,
							"s_warehouse": stock_entry_source_warehouse,
							"t_warehouse": stock_entry_target_warehouse,
						})
					# If item not available and negative stock not allowed, skip Stock Entry
					# but item will still be in ticket.stock_items below
				else:
					# Non-stock item - include in Stock Entry (no availability check needed)
					available_items_for_entry.append({
						"doctype": "Stock Entry Detail",
						"item_code": item_code,
						"qty": qty,
						"s_warehouse": stock_entry_source_warehouse,
						"t_warehouse": stock_entry_target_warehouse,
					})
			except Exception as e:
				# If stock check fails, log error but continue
				# Include item anyway - let Stock Entry validation handle it
				frappe.log_error(
					f"Error checking stock for item {item_code} in warehouse {stock_entry_source_warehouse}: {str(e)}",
					"Stock Check"
				)
				available_items_for_entry.append({
					"doctype": "Stock Entry Detail",
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": stock_entry_source_warehouse,
					"t_warehouse": stock_entry_target_warehouse,
				})
		
		# Only create Stock Entry if there are available items and from != to (same-warehouse transfer is invalid in ERPNext)
		# Note: All items (available or not) are still included in ticket.stock_items below
		if available_items_for_entry and stock_entry_source_warehouse != stock_entry_target_warehouse:
			se_company = frappe.db.get_value("Warehouse", stock_entry_source_warehouse, "company") or company
			if not se_company:
				frappe.throw(_("Source warehouse for stock entry has no Company"))
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"company": se_company,
					"purpose": "Material Transfer",
					"stock_entry_type": "Material Transfer",
					"from_warehouse": stock_entry_source_warehouse,
					"to_warehouse": stock_entry_target_warehouse,
					"posting_date": now_datetime().date(),
					"posting_time": now_datetime().time().replace(microsecond=0).isoformat(),
					"items": available_items_for_entry,
				}
			)
			se.insert(ignore_permissions=True)
			stock_entry_name = se.name

	if both_warehouse and assets:
		# For asset transfers, destination Location must exist. If it doesn't, ERPNext will treat
		# target_location as source_location and throw: "Source and Target Location cannot be same".
		if not to_loc:
			frappe.throw(
				_(
					"Destination warehouse has no mapped Location. Please run Location sync (Geo Warehouses → Location) for the destination area."
				)
			)

		# assets can be:
		# - ["ACC-ASS-00001", ...] (treated as qty=1 each)
		# - [{"asset":"ACC-ASS-00001","qty":2}, ...]
		requests: list[dict] = []
		for a in assets:
			if isinstance(a, str):
				requests.append({"asset": a, "qty": 1})
			elif isinstance(a, dict):
				pi = (a.get("paired_implement") or "").strip()
				requests.append(
					{
						"asset": a.get("asset"),
						"qty": a.get("qty"),
						**({"paired_implement": pi} if pi else {}),
					}
				)

		# Move implement assets together with tractors (same Asset Movement + destination Location).
		requests = _append_co_moving_implement_assets(requests)

		asset_rows = []
		first_company = None
		already_there: list[str] = []

		for req in requests:
			asset_name = req.get("asset")
			req_qty = flt(req.get("qty") or 0)
			if not asset_name or req_qty <= 0:
				continue

			asset_doc = frappe.get_doc("Asset", asset_name)
			if asset_doc.location and asset_doc.location == to_loc:
				already_there.append(asset_doc.name)
				# Skip from Asset Movement (no physical move needed) but still record
				# in ticket asset_items so the ticket reflects the full context and
				# duplicate detection (subset check) works correctly.
				ticket_asset_row_at = {"asset": asset_doc.name, "qty": req_qty}
				req_pi_at = (req.get("paired_implement") or "").strip()
				if req_pi_at and frappe.db.exists("Implement", req_pi_at):
					ticket_asset_row_at["paired_implement"] = req_pi_at
				asset_item_rows_for_ticket.append(ticket_asset_row_at)
				continue
			asset_qty = flt(getattr(asset_doc, "asset_quantity", 1) or 1)

			if req_qty > asset_qty:
				frappe.throw(_("Requested qty {0} exceeds asset qty {1} for Asset {2}").format(req_qty, asset_qty, asset_doc.name))

			move_asset = asset_doc
			move_qty = req_qty

			# If partial quantity requested, split the asset first.
			if asset_qty > 1 and req_qty < asset_qty:
				try:
					from erpnext.assets.doctype.asset.asset import split_asset  # type: ignore
				except Exception:
					frappe.throw(_("Cannot split asset quantity; ERPNext split_asset not available"))

				new_asset = split_asset(asset_doc.name, int(req_qty))
				move_asset = new_asset
				move_qty = flt(getattr(new_asset, "asset_quantity", req_qty) or req_qty)

			if not first_company:
				first_company = move_asset.company

			asset_rows.append(
				{
					"doctype": "Asset Movement Item",
					"asset": move_asset.name,
					"asset_name": move_asset.asset_name,
					"source_location": move_asset.location,
					"target_location": to_loc,
				}
			)
			ticket_asset_row = {"asset": move_asset.name, "qty": move_qty}
			req_pi = (req.get("paired_implement") or "").strip()
			if req_pi and frappe.db.exists("Implement", req_pi):
				ticket_asset_row["paired_implement"] = req_pi
			asset_item_rows_for_ticket.append(ticket_asset_row)

		if already_there and not asset_rows:
			frappe.throw(
				_("Selected asset(s) are already in the destination Location {0}: {1}").format(
					to_loc, ", ".join(already_there[:10])
				)
			)

		if asset_rows:
			am = frappe.get_doc(
				{
					"doctype": "Asset Movement",
					"company": first_company or company,
					"purpose": "Transfer",
					"transaction_date": now_datetime(),
					"assets": asset_rows,
				}
			)
			am.insert(ignore_permissions=True)
			asset_movement_name = am.name

	# External transfer: accept assets for ticket.asset_items (no Asset Movement)
	if not both_warehouse and assets:
		ext_reqs: list[dict] = []
		for a in assets:
			if isinstance(a, str):
				ext_reqs.append({"asset": a, "qty": 1})
			elif isinstance(a, dict):
				ext_pi = (a.get("paired_implement") or "").strip()
				ext_reqs.append(
					{
						"asset": a.get("asset"),
						"qty": max(1, flt(a.get("qty") or 1)),
						**({"paired_implement": ext_pi} if ext_pi else {}),
					}
				)
		for req in _append_co_moving_implement_assets(ext_reqs):
			asset_name = req.get("asset")
			qty = max(1, flt(req.get("qty") or 1))
			if asset_name and qty > 0:
				ext_row = {"asset": asset_name, "qty": qty}
				req_pi = (req.get("paired_implement") or "").strip()
				if req_pi and frappe.db.exists("Implement", req_pi):
					ext_row["paired_implement"] = req_pi
				asset_item_rows_for_ticket.append(ext_row)

	asset_items_payload = []
	for r in asset_item_rows_for_ticket:
		if not r.get("asset"):
			continue
		row = {"asset": r.get("asset"), "qty": r.get("qty") or 1}
		pi = (r.get("paired_implement") or "").strip()
		if pi and frappe.db.exists("Implement", pi):
			row["paired_implement"] = pi
		asset_items_payload.append(row)

	ticket_data = {
		"doctype": "Logistics Transfer Ticket",
		"from_warehouse": from_warehouse or None,
		"to_warehouse": to_warehouse or None,
		"from_location": from_loc,
		"to_location": to_loc,
		"transfer_type": transfer_type or "Internal",
		"from_location_type": from_location_type,
		"to_location_type": to_location_type,
		"stock_entry": stock_entry_name,
		"asset_movement": asset_movement_name,
		"status": "Pending Pickup",
		"pickup_phase": "Upcoming",
		"drop_off_phase": None,
		"stock_items": [
			{"item_code": r.get("item_code"), "qty": flt(r.get("qty"))} for r in (stock_items or []) if r.get("item_code")
		],
		"asset_items": asset_items_payload,
	}
	if purchase_order:
		ticket_data["purchase_order"] = purchase_order
	if purchase_invoice:
		ticket_data["purchase_invoice"] = purchase_invoice
	if sales_order:
		ticket_data["sales_order"] = sales_order
	if sales_invoice:
		ticket_data["sales_invoice"] = sales_invoice
	if from_location_type == "Other":
		ticket_data["from_address"] = (from_address or "").strip() or None
		ticket_data["from_latitude"] = flt(from_latitude)
		ticket_data["from_longitude"] = flt(from_longitude)
	if to_location_type == "Other":
		ticket_data["to_address"] = (to_address or "").strip() or None
		ticket_data["to_latitude"] = flt(to_latitude)
		ticket_data["to_longitude"] = flt(to_longitude)

	# Only set when parseable; store as string so Frappe's insert handles all versions uniformly.
	if planned_pickup_on is not None:
		_pu = get_datetime(planned_pickup_on)
		if _pu:
			ticket_data["planned_pickup_on"] = get_datetime_str(_pu)
	if planned_drop_off_on is not None:
		_po = get_datetime(planned_drop_off_on)
		if _po:
			ticket_data["planned_drop_off_on"] = get_datetime_str(_po)

	# Pickup must be strictly before drop (min 60 min lead). Fixes drop from schedule + pickup defaulting to creation.
	if ticket_data.get("planned_drop_off_on"):
		_pu_c, _po_c = clamp_planned_pickup_before_drop_str(
			ticket_data.get("planned_pickup_on"), ticket_data["planned_drop_off_on"]
		)
		if _pu_c:
			ticket_data["planned_pickup_on"] = _pu_c
		if _po_c:
			ticket_data["planned_drop_off_on"] = _po_c

	tv = (transport_vehicle or "").strip()
	if not tv and not skip_default_transport_vehicle:
		# Tickets that move stock (approved inputs) must use an explicit pickup Vehicle on the schedule.
		# Do not fall back to the tractor — that blurs consumables vs machinery moves.
		if stock_items:
			tv = ""
		else:
			tv = (_default_transport_vehicle_from_assets(assets) or "").strip()
	if tv:
		ticket_data["transport_vehicle"] = tv

	sr = (schedule_ref or "").strip()
	if sr:
		ticket_data["schedule_ref"] = sr

	# Follow-up save when both planned times are set (post-insert persistence for SE/AM paths).
	explicit_schedule_planned = bool(
		ticket_data.get("planned_pickup_on") and ticket_data.get("planned_drop_off_on")
	)

	ticket = frappe.get_doc(ticket_data)
	_field_supervisor_ltt_gate(ticket, any_leg=True)
	ticket.insert(ignore_permissions=True)

	# Default planned dates to creation when not provided (insert does not always persist in-doc patches).
	need_planned_save = False
	if ticket_data.get("planned_pickup_on") is None:
		ticket.planned_pickup_on = ticket.creation
		need_planned_save = True
	if ticket_data.get("planned_drop_off_on") is None:
		ticket.planned_drop_off_on = ticket.creation
		need_planned_save = True
	# When planned pickup/drop are on the ticket, re-apply clamp and save (mirrors historical creation-default path).
	if explicit_schedule_planned:
		pu = get_datetime(ticket_data.get("planned_pickup_on"))
		po = get_datetime(ticket_data.get("planned_drop_off_on"))
		if pu and po:
			pu2, po2 = clamp_planned_pickup_before_drop_str(pu, po)
			if pu2 and po2:
				ticket.planned_pickup_on = pu2
				ticket.planned_drop_off_on = po2
				need_planned_save = True
	if need_planned_save and (ticket.planned_pickup_on or ticket.planned_drop_off_on):
		try:
			ticket.save(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(
				f"LTT {ticket.name}: post-insert save for planned times failed (ticket and SE/AM may still exist): {e!s}",
				"Logistics Transfer Ticket",
			)

	return {
		"ticket": ticket.name,
		"status": ticket.status,
		"stock_entry": stock_entry_name,
		"asset_movement": asset_movement_name,
	}


@frappe.whitelist()
def mark_dispatched(ticket_name: str, dispatch_photo_url=None):
	"""dispatch_photo_url can be a single URL string, list of URLs, or JSON string of URLs.
	Allowed when pickup_phase is At Pickup Point or In Transit (backward compatible)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, pickup=True)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can be dispatched"))
	phase = getattr(ticket, "pickup_phase", None) or ""
	if phase not in ("At Pickup Point", "In Transit", "Not Started", "Upcoming"):
		frappe.throw(_("Pickup phase must be At Pickup Point, In Transit, Not Started, or Upcoming to dispatch"))
	ticket.status = "In Transit"
	ticket.pickup_phase = "In Transit"
	ticket.dispatched_on = now_datetime()
	photo_json = _normalize_photo_urls(dispatch_photo_url)
	if photo_json:
		ticket.dispatch_photo = photo_json
	ticket.save(ignore_permissions=True)
	ticket.reload()
	_reapply_tractor_implement_links_after_transfer(ticket)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_received(ticket_name: str, receive_photo_url=None):
	"""receive_photo_url can be a single URL string, list of URLs, or JSON string of URLs.
	Allowed when status=In Transit and drop_off_phase is Delivered (new flow) or In Transit (legacy).
	When F2C Settings disables location enforcement, At Drop Off Point is also allowed (receiver need not wait for driver Delivered)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, dropoff=True)
	if ticket.status != "In Transit":
		frappe.throw(_("Only In Transit tickets can be marked Received"))
	drop_phase = getattr(ticket, "drop_off_phase", None) or ""
	allowed_phases = {"Delivered", "In Transit"}
	if not _f2c_enforce_logistics_location():
		allowed_phases.add("At Drop Off Point")
	if drop_phase not in allowed_phases:
		frappe.throw(
			_("Drop off phase must be one of {0} to mark Received").format(", ".join(sorted(allowed_phases)))
		)

	if ticket.stock_entry:
		se = frappe.get_doc("Stock Entry", ticket.stock_entry)
		if se.docstatus == 0:
			se.submit()

	if ticket.asset_movement:
		am = frappe.get_doc("Asset Movement", ticket.asset_movement)
		if am.docstatus == 0:
			am.submit()

	_sync_equipment_locations_after_asset_movement(ticket)
	_reapply_tractor_implement_links_after_transfer(ticket)

	new_status = _get_equipment_status_for_destination_warehouse(ticket.to_warehouse)
	if new_status and getattr(ticket, "asset_items", None):
		for row in ticket.asset_items:
			if row.get("asset"):
				_set_equipment_status_for_asset(row.asset, new_status)

	ticket.status = "Received"
	if drop_phase != "Delivered":
		ticket.drop_off_phase = "Delivered"
	ticket.received_on = now_datetime()
	photo_json = _normalize_photo_urls(receive_photo_url)
	if photo_json:
		ticket.receive_photo = photo_json
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status, "stock_entry": ticket.stock_entry, "asset_movement": ticket.asset_movement}


@frappe.whitelist()
def revert_received(ticket_name: str):
	"""Restore a Received ticket back to In Transit by cancelling linked Stock Entry and Asset Movement."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, any_leg=True)
	if ticket.status != "Received":
		frappe.throw(_("Only Received tickets can be restored to In Transit"))

	if ticket.stock_entry:
		se = frappe.get_doc("Stock Entry", ticket.stock_entry)
		if se.docstatus == 1:
			se.cancel()

	if ticket.asset_movement:
		am = frappe.get_doc("Asset Movement", ticket.asset_movement)
		if am.docstatus == 1:
			am.cancel()

	_sync_equipment_locations_after_asset_movement(ticket)

	new_status = _get_equipment_status_for_destination_warehouse(ticket.from_warehouse)
	if new_status and getattr(ticket, "asset_items", None):
		for row in ticket.asset_items:
			if row.get("asset"):
				_set_equipment_status_for_asset(row.asset, new_status)

	ticket.status = "In Transit"
	ticket.drop_off_phase = "In Transit"
	ticket.received_on = None
	ticket.receive_photo = None
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def start_pickup(ticket_name: str):
	"""Set pickup phase to Not Started when user opens Start Pickup (e.g. driver accepted)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, pickup=True)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can start pickup"))
	if getattr(ticket, "pickup_phase", None) != "Upcoming":
		frappe.throw(_("Pickup already started or in progress"))
	ticket.pickup_phase = "Not Started"
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "pickup_phase": ticket.pickup_phase}


@frappe.whitelist()
def mark_en_route_to_pickup(ticket_name: str):
	"""Driver left for pickup; set pickup phase to In Transit (status stays Pending Pickup)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, pickup=True)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can be marked en route to pickup"))
	if getattr(ticket, "pickup_phase", None) != "Not Started":
		frappe.throw(_("Pickup phase must be Not Started to mark en route to pickup"))
	ticket.pickup_phase = "In Transit"
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "pickup_phase": ticket.pickup_phase}


@frappe.whitelist()
def mark_reached_pickup_point(ticket_name: str):
	"""Driver at pickup location; set pickup phase to At Pickup Point."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, pickup=True)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can mark reached pickup point"))
	if getattr(ticket, "pickup_phase", None) != "In Transit":
		frappe.throw(_("Pickup phase must be In Transit to mark reached pickup point"))
	ticket.pickup_phase = "At Pickup Point"
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "pickup_phase": ticket.pickup_phase}


@frappe.whitelist()
def mark_picked_up(ticket_name: str):
	"""Mark pickup complete; start drop-off phase (Not Started)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, pickup=True)
	if getattr(ticket, "pickup_phase", None) != "In Transit":
		frappe.throw(_("Pickup must be In Transit before marking Picked Up"))
	if ticket.status != "In Transit":
		frappe.throw(_("Ticket must be In Transit to mark Picked Up"))
	ticket.pickup_phase = "Picked Up"
	ticket.drop_off_phase = "Not Started"
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "pickup_phase": ticket.pickup_phase, "drop_off_phase": ticket.drop_off_phase, "status": ticket.status}


@frappe.whitelist()
def start_drop_off(ticket_name: str):
	"""Set drop-off phase to In Transit (driver en route to destination)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, dropoff=True)
	if getattr(ticket, "pickup_phase", None) != "Picked Up":
		frappe.throw(_("Pickup must be Picked Up before starting drop off"))
	if getattr(ticket, "drop_off_phase", None) != "Not Started":
		frappe.throw(_("Drop off already started or in progress"))
	ticket.drop_off_phase = "In Transit"
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "drop_off_phase": ticket.drop_off_phase}


@frappe.whitelist()
def mark_reached_drop_off_point(ticket_name: str):
	"""Driver at drop-off location; set drop-off phase to At Drop Off Point."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, dropoff=True)
	if getattr(ticket, "pickup_phase", None) != "Picked Up":
		frappe.throw(_("Pickup must be Picked Up before marking reached drop off point"))
	if getattr(ticket, "drop_off_phase", None) != "In Transit":
		frappe.throw(_("Drop off phase must be In Transit to mark reached drop off point"))
	ticket.drop_off_phase = "At Drop Off Point"
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "drop_off_phase": ticket.drop_off_phase}


@frappe.whitelist()
def mark_delivered(ticket_name: str, deliver_photo_url=None):
	"""Driver hands over goods; set drop_off_phase to Delivered (no status change, no Stock Entry/Asset submit)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, dropoff=True)
	if ticket.status != "In Transit":
		frappe.throw(_("Only In Transit tickets can be marked Delivered"))
	if getattr(ticket, "drop_off_phase", None) != "At Drop Off Point":
		frappe.throw(_("Drop off phase must be At Drop Off Point to mark Delivered"))
	ticket.drop_off_phase = "Delivered"
	ticket.delivered_on = now_datetime()
	# Optional: store deliver_photo if doctype has the field (future)
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "drop_off_phase": ticket.drop_off_phase}


@frappe.whitelist()
def mark_reported(ticket_name: str, reason: str = "", report_image: str = "", block: str = "", activity: str = ""):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, any_leg=True)
	if ticket.status in ("Received", "Cancelled"):
		frappe.throw(_("Cannot report a Received/Cancelled ticket"))
	if ticket.status == "Reported":
		frappe.throw(_("Ticket is already reported"))
	ticket.previous_status_before_report = ticket.status
	ticket.status = "Reported"
	ticket.report_reason = (reason or "").strip() or ticket.report_reason
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def resolve_reported(ticket_name: str, resolution_note: str = ""):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, any_leg=True)
	if ticket.status != "Reported":
		frappe.throw(_("Only Reported tickets can be resolved"))
	resolution_note = (resolution_note or "").strip()
	if not resolution_note:
		frappe.throw(_("Resolution note is required"))
	previous_status = (ticket.previous_status_before_report or "").strip()
	if not previous_status:
		frappe.throw(_("Previous status is missing for this reported ticket"))
	ticket.status = previous_status
	ticket.resolution_note = resolution_note
	ticket.previous_status_before_report = None
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


def _logistics_farm_report_resolve_phase(ltt) -> str | None:
	"""
	Return case key for enhanced logistics Farm Report resolve, or None for legacy (remark-only) resolve.
	- case2_dropoff_in_transit: picked up, en route to drop off.
	- case1_pickup_leg: active pickup leg (not case 2).
	"""
	pickup = (getattr(ltt, "pickup_phase", None) or "").strip()
	drop = (getattr(ltt, "drop_off_phase", None) or "").strip()
	status = (getattr(ltt, "status", None) or "").strip()
	if pickup == "Picked Up" and drop == "In Transit":
		return "case2_dropoff_in_transit"
	if pickup in ("In Transit", "At Pickup Point"):
		return "case1_pickup_leg"
	if status == "In Transit" and pickup != "Picked Up":
		return "case1_pickup_leg"
	return None


def _ltt_has_tractor_asset(ltt) -> bool:
	"""True if any moved asset is self-propelled machinery (tractor, thresher, etc.)."""
	for row in ltt.get("asset_items") or []:
		an = (getattr(row, "asset", None) or "").strip()
		if not an:
			continue
		if self_transport_machinery_name_for_asset(an):
			return True
	return False


def _ltt_replacement_cargo_allowed(ltt) -> bool:
	"""
	Replacement LTT is only allowed when cargo is stock and/or Hand Tool / Other Tool assets only
	(no Machinery, Implement, or unknown equipment on asset lines).
	"""
	has_stock_line = False
	for row in ltt.get("stock_items") or []:
		ic = (getattr(row, "item_code", None) or "").strip()
		if ic and flt(getattr(row, "qty", None) or 0) > 0:
			has_stock_line = True
			break
	assets = list(ltt.get("asset_items") or [])
	if not has_stock_line and not assets:
		return False
	for row in assets:
		an = (getattr(row, "asset", None) or "").strip()
		if not an:
			continue
		if frappe.db.exists("Machinery", {"asset": an}):
			return False
		if frappe.db.exists("Implement", {"asset": an}):
			return False
		if frappe.db.exists("Hand Tool", {"asset": an}):
			continue
		if frappe.db.exists("Other Tool", {"asset": an}):
			continue
		return False
	return True


@frappe.whitelist()
def get_logistics_farm_report_resolve_context(ltt_name: str | None = None):
	"""Return phase and whether replacement (new LTT) is allowed for logistics farm-report resolve UI."""
	name = (ltt_name or frappe.form_dict.get("ltt_name") or "").strip()
	if not name:
		frappe.throw(_("ltt_name is required"))
	ltt = frappe.get_doc("Logistics Transfer Ticket", name)
	_field_supervisor_ltt_gate(ltt, any_leg=True)
	phase = _logistics_farm_report_resolve_phase(ltt)
	has_tractor = _ltt_has_tractor_asset(ltt)
	cargo_ok = _ltt_replacement_cargo_allowed(ltt)
	# Replacement: light cargo (no tractor) in either phase, or tractor/self-propelled on pickup or drop-off leg.
	replacement_allowed = bool(
		phase
		and (
			(cargo_ok and not has_tractor)
			or (
				has_tractor
				and phase in ("case1_pickup_leg", "case2_dropoff_in_transit")
			)
		)
	)
	return {
		"phase": phase,
		"has_tractor": has_tractor,
		"cargo_replacement_eligible": cargo_ok,
		"replacement_allowed": replacement_allowed,
	}


def _ltt_stock_items_payload(ltt) -> list[dict]:
	out: list[dict] = []
	for row in ltt.get("stock_items") or []:
		ic = (getattr(row, "item_code", None) or "").strip()
		if not ic:
			continue
		out.append({"item_code": ic, "qty": flt(getattr(row, "qty", None) or 0)})
	return out


def _ltt_assets_payload(ltt) -> list[dict]:
	out: list[dict] = []
	for row in ltt.get("asset_items") or []:
		an = (getattr(row, "asset", None) or "").strip()
		if not an:
			continue
		d: dict = {"asset": an, "qty": max(1, flt(getattr(row, "qty", None) or 1))}
		pi = (getattr(row, "paired_implement", None) or "").strip()
		if pi and frappe.db.exists("Implement", pi):
			d["paired_implement"] = pi
		out.append(d)
	return out


def _planned_dt_str(val):
	if val is None:
		return None
	if hasattr(val, "strftime"):
		return val.strftime("%Y-%m-%d %H:%M:%S")
	s = str(val).strip()
	return s or None


@frappe.whitelist()
def resolve_logistics_farm_report_ticket(
	farm_report_ticket: str | None = None,
	resolution_kind: str | None = None,
	replacement_vehicle: str | None = None,
	new_from_warehouse: str | None = None,
	help_person_name: str | None = None,
	help_person_contact: str | None = None,
	resolve_remark: str | None = None,
	resolve_image: str | None = None,
	handoff_latitude: str | None = None,
	handoff_longitude: str | None = None,
	handoff_address: str | None = None,
	handoff_dispatch_image: str | None = None,
):
	"""
	Resolve a Farm Report Ticket (Logistics module) with optional replacement LTT or help contact details.
	Only for linked LTT phases that qualify (see _logistics_farm_report_resolve_phase); otherwise use standard client resolve.
	"""
	name = (farm_report_ticket or frappe.form_dict.get("farm_report_ticket") or "").strip()
	kind = (resolution_kind or frappe.form_dict.get("resolution_kind") or "").strip().lower()
	if not name:
		frappe.throw(_("farm_report_ticket is required"))
	if kind not in ("replacement", "help"):
		frappe.throw(_("resolution_kind must be replacement or help"))

	rpt = frappe.get_doc("Farm Report Ticket", name)
	if (getattr(rpt, "report_module", None) or "").strip() != "Logistics":
		frappe.throw(_("This action is only for Logistics farm report tickets"))
	if (rpt.status or "").strip() == "Resolved":
		frappe.throw(_("Farm Report Ticket is already resolved"))
	ltt_name = (getattr(rpt, "logistics_transfer_ticket", None) or "").strip()
	if not ltt_name:
		frappe.throw(_("Farm Report Ticket has no linked Logistics Transfer Ticket"))

	ltt = frappe.get_doc("Logistics Transfer Ticket", ltt_name)
	_field_supervisor_ltt_gate(ltt, any_leg=True)
	phase = _logistics_farm_report_resolve_phase(ltt)
	if not phase:
		frappe.throw(
			_("This logistics ticket is not in a pickup/dropoff phase that supports replacement/help resolve. Use standard resolve.")
		)

	resolve_remark = (resolve_remark or frappe.form_dict.get("resolve_remark") or "").strip() or None
	resolve_image = (resolve_image or frappe.form_dict.get("resolve_image") or "").strip() or None
	replacement_vehicle = (replacement_vehicle or frappe.form_dict.get("replacement_vehicle") or "").strip() or None
	new_from_warehouse = (new_from_warehouse or frappe.form_dict.get("new_from_warehouse") or "").strip() or None
	help_person_name = (help_person_name or frappe.form_dict.get("help_person_name") or "").strip() or None
	help_person_contact = (help_person_contact or frappe.form_dict.get("help_person_contact") or "").strip() or None
	handoff_latitude = (handoff_latitude or frappe.form_dict.get("handoff_latitude") or "").strip() or None
	handoff_longitude = (handoff_longitude or frappe.form_dict.get("handoff_longitude") or "").strip() or None
	handoff_address = (handoff_address or frappe.form_dict.get("handoff_address") or "").strip() or None
	handoff_dispatch_image = (handoff_dispatch_image or frappe.form_dict.get("handoff_dispatch_image") or "").strip() or None

	new_ltt_name: str | None = None

	if kind == "help":
		if not help_person_name or not help_person_contact:
			frappe.throw(_("Help person name and contact are required"))
		lines = [f"Help: {help_person_name} ({help_person_contact})"]
		if resolve_remark:
			lines.append(resolve_remark)
		rpt.resolve_remark = "\n".join(lines)
		if resolve_image:
			rpt.resolve_image = resolve_image
		rpt.logistics_resolve_kind = "Help"
		rpt.logistics_help_name = help_person_name
		rpt.logistics_help_contact = help_person_contact
		rpt.logistics_replacement_ltt = None
		rpt.status = "Resolved"
		rpt.save(ignore_permissions=True)
		return {"farm_report_ticket": rpt.name, "status": rpt.status, "new_logistics_ticket": None}

	# replacement
	has_tractor = _ltt_has_tractor_asset(ltt)
	if not _ltt_replacement_cargo_allowed(ltt) and not (
		has_tractor and phase in ("case1_pickup_leg", "case2_dropoff_in_transit")
	):
		frappe.throw(
			_(
				"Replacement is only available when the transfer has stock and/or hand tool or other tool assets only, with no tractor on the ticket except on the pickup or drop-off leg. Use Send help or standard resolve."
			)
		)
	if not replacement_vehicle or not frappe.db.exists("Machinery", replacement_vehicle):
		frappe.throw(
			_("A valid replacement transport tractor (Machinery) is required")
			if has_tractor
			else _("A valid replacement transport vehicle (Machinery) is required")
		)
	if has_tractor:
		repl_type = (frappe.db.get_value("Machinery", replacement_vehicle, "machinery_type") or "").strip()
		if repl_type != "Tractor":
			frappe.throw(_("For tractor transfers, replacement machinery must be of type Tractor."))
	current_transport = (getattr(ltt, "transport_vehicle", None) or "").strip()
	if current_transport and replacement_vehicle == current_transport:
		frappe.throw(
			_("Choose a different tractor than the one already assigned to this transfer ticket.")
			if has_tractor
			else _("Choose a different vehicle than the one already assigned to this transfer ticket.")
		)

	stock_items = _ltt_stock_items_payload(ltt)
	assets = _ltt_assets_payload(ltt)
	if not stock_items and not assets:
		frappe.throw(_("Linked logistics ticket has no stock or asset lines to copy"))

	from_location_type = (getattr(ltt, "from_location_type", None) or "Warehouse").strip() or "Warehouse"
	to_location_type = (getattr(ltt, "to_location_type", None) or "Warehouse").strip() or "Warehouse"

	from_wh = (getattr(ltt, "from_warehouse", None) or "").strip() or None
	to_wh = (getattr(ltt, "to_warehouse", None) or "").strip() or None
	from_address = getattr(ltt, "from_address", None)
	from_latitude = getattr(ltt, "from_latitude", None)
	from_longitude = getattr(ltt, "from_longitude", None)
	to_address = getattr(ltt, "to_address", None)
	to_latitude = getattr(ltt, "to_latitude", None)
	to_longitude = getattr(ltt, "to_longitude", None)
	if from_location_type == "Warehouse":
		from_address = None
		from_latitude = None
		from_longitude = None
	if to_location_type == "Warehouse":
		to_address = None
		to_latitude = None
		to_longitude = None

	if phase == "case2_dropoff_in_transit":
		if from_location_type != "Warehouse":
			frappe.throw(_("Drop-off leg replacement is only supported when the source From is a Warehouse"))
		if not from_wh:
			frappe.throw(_("Original ticket has no pickup warehouse; cannot create replacement transfer."))
		# Same pickup warehouse as the original ticket (replacement transport only).
		nfw = (new_from_warehouse or "").strip()
		if nfw and nfw != (from_wh or "").strip():
			frappe.throw(_("new_from_warehouse is not used; replacement uses the same pickup warehouse as the original ticket."))
	elif phase == "case1_pickup_leg":
		if new_from_warehouse:
			frappe.throw(_("new_from_warehouse must not be set for pickup-leg replacement"))
	else:
		frappe.throw(_("Unsupported resolve phase"))

	transfer_type = (getattr(ltt, "transfer_type", None) or "Internal").strip() or "Internal"

	# Vehicle drop-off replacement: optional map handoff becomes the new ticket's pickup (Other + GPS),
	# not the original cluster warehouse. Omit handoff to keep the same pickup warehouse as the parent.
	use_handoff_gps_pickup = (
		phase == "case2_dropoff_in_transit"
		and not has_tractor
		and (handoff_latitude or "").strip()
		and (handoff_longitude or "").strip()
	)
	handoff_pick_lat = None
	handoff_pick_lng = None
	handoff_pick_addr = None
	if use_handoff_gps_pickup:
		_lat_s = (handoff_latitude or "").strip()
		_lng_s = (handoff_longitude or "").strip()
		_addr_s = (handoff_address or "").strip()
		if not _lat_s or not _lng_s:
			frappe.throw(_("Handoff latitude and longitude are both required when entering reported vehicle location."))
		handoff_pick_lat = flt(_lat_s)
		handoff_pick_lng = flt(_lng_s)
		if handoff_pick_lat < -90 or handoff_pick_lat > 90 or handoff_pick_lng < -180 or handoff_pick_lng > 180:
			frappe.throw(_("Handoff latitude and longitude must be within valid ranges."))
		handoff_pick_addr = _addr_s[:500] if _addr_s else None

	if use_handoff_gps_pickup:
		result = create_logistics_transfer_ticket(
			from_warehouse=None,
			to_warehouse=to_wh,
			stock_items=stock_items,
			assets=assets,
			transfer_type=transfer_type,
			from_location_type="Other",
			to_location_type=to_location_type,
			from_address=handoff_pick_addr,
			from_latitude=handoff_pick_lat,
			from_longitude=handoff_pick_lng,
			to_address=to_address,
			to_latitude=to_latitude,
			to_longitude=to_longitude,
			planned_pickup_on=_planned_dt_str(getattr(ltt, "planned_pickup_on", None)),
			planned_drop_off_on=_planned_dt_str(getattr(ltt, "planned_drop_off_on", None)),
			purchase_order=getattr(ltt, "purchase_order", None),
			purchase_invoice=getattr(ltt, "purchase_invoice", None),
			sales_order=getattr(ltt, "sales_order", None),
			sales_invoice=getattr(ltt, "sales_invoice", None),
			transport_vehicle=replacement_vehicle,
			material_transfer_source_warehouse=from_wh,
		)
	else:
		result = create_logistics_transfer_ticket(
			from_warehouse=from_wh,
			to_warehouse=to_wh,
			stock_items=stock_items,
			assets=assets,
			transfer_type=transfer_type,
			from_location_type=from_location_type,
			to_location_type=to_location_type,
			from_address=from_address,
			from_latitude=from_latitude,
			from_longitude=from_longitude,
			to_address=to_address,
			to_latitude=to_latitude,
			to_longitude=to_longitude,
			planned_pickup_on=_planned_dt_str(getattr(ltt, "planned_pickup_on", None)),
			planned_drop_off_on=_planned_dt_str(getattr(ltt, "planned_drop_off_on", None)),
			purchase_order=getattr(ltt, "purchase_order", None),
			purchase_invoice=getattr(ltt, "purchase_invoice", None),
			sales_order=getattr(ltt, "sales_order", None),
			sales_invoice=getattr(ltt, "sales_invoice", None),
			transport_vehicle=replacement_vehicle,
		)
	new_ltt_name = (result or {}).get("ticket")
	if new_ltt_name:
		new_doc = frappe.get_doc("Logistics Transfer Ticket", new_ltt_name)
		if getattr(ltt, "farm_task_execution", None):
			new_doc.farm_task_execution = ltt.farm_task_execution
		patched = False
		if phase == "case2_dropoff_in_transit" and not has_tractor:
			patched = _merge_replacement_dispatch_photo_on_doc(new_doc, ltt, handoff_dispatch_image)
		if getattr(ltt, "farm_task_execution", None) or patched:
			new_doc.save(ignore_permissions=True)

	rpt.logistics_resolve_kind = "Replacement"
	rpt.logistics_replacement_ltt = new_ltt_name
	rpt.logistics_help_name = None
	rpt.logistics_help_contact = None
	if resolve_remark:
		rpt.resolve_remark = resolve_remark
	if resolve_image:
		rpt.resolve_image = resolve_image
	rpt.status = "Resolved"
	rpt.save(ignore_permissions=True)

	return {
		"farm_report_ticket": rpt.name,
		"status": rpt.status,
		"new_logistics_ticket": new_ltt_name,
	}


@frappe.whitelist()
def mark_cancelled(ticket_name: str, reason: str = ""):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	_field_supervisor_ltt_gate(ticket, any_leg=True)
	if ticket.status == "Received":
		frappe.throw(_("Cannot cancel a Received ticket"))
	ticket.status = "Cancelled"
	if reason:
		ticket.report_reason = reason
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


def _get_equipment_status_for_asset(asset_name: str) -> str | None:
	"""
	Resolve equipment status (Available, In Use, Maintenance, Retired) from the
	source equipment doc (Machinery, Implement, Hand Tool, Other Tool) linked to this Asset.
	"""
	if not asset_name:
		return None
	for doctype in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		status = frappe.db.get_value(doctype, {"asset": asset_name}, "status")
		if status:
			return status
	return None


def _get_equipment_hero_image_for_asset(asset_name: str) -> str | None:
	"""
	Return hero_image from the equipment doc (Machinery, Implement, Hand Tool, Other Tool)
	linked to this Asset, so warehouse inventory can show the same image as Equipment List.
	"""
	if not asset_name:
		return None
	for doctype in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		hero = frappe.db.get_value(doctype, {"asset": asset_name}, "hero_image")
		if hero:
			return hero
	return None


def _get_equipment_doc_for_asset(asset_name: str) -> tuple[str, str] | None:
	"""Return (doctype, name) of the equipment document linked to this Asset, or None."""
	if not asset_name:
		return None
	for doctype in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		name = frappe.db.get_value(doctype, {"asset": asset_name}, "name")
		if name:
			return (doctype, name)
	return None


def _shorten_asset_caption_for_display(asset_name: str) -> str:
	"""
	Strip trailing serial / chassis tokens often appended to Asset.asset_name
	(e.g. 'Brand - Model - Tractor - CH:XXXXXXXX') for UI labels.
	"""
	import re

	s = str(asset_name or "").strip()
	if not s:
		return ""
	out = re.sub(r"\s*-\s*CH:[A-Z0-9]+\s*$", "", s, flags=re.IGNORECASE)
	out = re.sub(r"\s*-\s*VIN:[A-Z0-9]+\s*$", "", out, flags=re.IGNORECASE)
	out = re.sub(r"\s*-\s*[A-Z]{2,4}:[A-Z0-9]{6,}\s*$", "", out, flags=re.IGNORECASE)
	out = re.sub(r"\s*-\s*Tractor\s*$", "", out, flags=re.IGNORECASE)
	out = out.strip()
	return out or s


def _enrich_equipment_display_names(assets: list[dict]) -> None:
	"""
	Set equipment_display_name from linked equipment docs (machinery_name, implement_name, tool_name).
	Preferred over Asset.asset_name, which may mirror item description (e.g. Brand - Model - Type).
	If no equipment doc label, derive a shorter caption from asset_name (strip CH:/VIN: style suffixes).
	"""
	names = [str(a.get("name") or "").strip() for a in assets if (a.get("name") or "").strip()]
	if not names:
		return
	disp: dict[str, str] = {}
	for doctype, field in (
		("Machinery", "machinery_name"),
		("Implement", "implement_name"),
		("Hand Tool", "tool_name"),
		("Other Tool", "tool_name"),
	):
		rows = frappe.get_all(
			doctype,
			filters=[["asset", "in", names]],
			fields=["asset", "name", field],
			limit=len(names) + 50,
			ignore_permissions=True,
		)
		for r in rows:
			an = str(r.get("asset") or "").strip()
			v = str(r.get(field) or "").strip() or str(r.get("name") or "").strip()
			if an and v and an not in disp:
				disp[an] = v
	for a in assets:
		an = str(a.get("name") or "").strip()
		if not an:
			continue
		if an in disp:
			a["equipment_display_name"] = disp[an]
		elif not (a.get("equipment_display_name") or "").strip():
			base = str(a.get("asset_name") or "").strip()
			if base:
				a["equipment_display_name"] = _shorten_asset_caption_for_display(base)


@frappe.whitelist()
def enrich_asset_rows_with_equipment_display_names(assets=None):
	"""
	For each row with Asset.name, set equipment_display_name from Machinery / Implement / Hand Tool / Other Tool
	(same as warehouse inventory). Used when the client loads Asset via resource API without enrichment.
	"""
	import json

	if assets is None:
		return []
	if isinstance(assets, str):
		try:
			assets = json.loads(assets)
		except Exception:
			return []
	if not isinstance(assets, list) or not assets:
		return []
	rows = [dict(x) for x in assets if isinstance(x, dict) and str(x.get("name") or "").strip()]
	if not rows:
		return []
	_enrich_equipment_display_names(rows)
	return rows


def _enrich_attachment_from_equipment(row: dict, equipment: tuple[str, str]) -> None:
	"""Optional tractor↔implement labels for warehouse inventory UI."""
	doctype, eqname = equipment
	if doctype == "Machinery":
		mtype = frappe.db.get_value("Machinery", eqname, "machinery_type")
		if (mtype or "").strip() != "Tractor":
			return
		impl = frappe.db.get_value("Machinery", eqname, "current_implement")
		if not impl:
			return
		row["attached_implement_id"] = impl
		row["attached_implement_label"] = frappe.db.get_value("Implement", impl, "implement_name") or impl
	elif doctype == "Implement":
		mach = frappe.db.get_value("Implement", eqname, "attached_to_machinery")
		if not mach:
			return
		row["attached_tractor_machinery_id"] = mach
		row["attached_tractor_machinery_label"] = frappe.db.get_value("Machinery", mach, "machinery_name") or mach


def _location_warehouse_level_for_warehouse_name(warehouse: str) -> str | None:
	"""Resolve Warehouse -> Location -> path depth (same rule as asset rows)."""
	if not warehouse:
		return None
	try:
		res = get_location_for_warehouse(warehouse)
	except Exception:
		return None
	loc = (res or {}).get("location")
	if not loc:
		return None
	try:
		location_name = frappe.db.get_value("Location", loc, "location_name")
	except Exception:
		location_name = None
	return _location_warehouse_level_from_location_name(location_name)


def _enrich_location_geo_labels(row: dict) -> None:
	"""Set location_warehouse_level from Asset.location -> Location.location_name."""
	loc = row.get("location")
	if not loc:
		return
	try:
		location_name = frappe.db.get_value("Location", loc, "location_name")
	except Exception:
		location_name = None
	lvl = _location_warehouse_level_from_location_name(location_name)
	if lvl:
		row["location_warehouse_level"] = lvl


@frappe.whitelist()
def get_location_warehouse_levels_for_warehouses(warehouses=None):
	"""
	Map each Warehouse name to farm | cluster | field (or null if unmapped).
	warehouses: JSON array string, e.g. '["WH-A","WH-B"]', or a list.
	"""
	import json

	if warehouses is None:
		names = []
	elif isinstance(warehouses, str):
		try:
			names = json.loads(warehouses)
		except Exception:
			s = warehouses.strip()
			names = [s] if s else []
	else:
		names = list(warehouses) if isinstance(warehouses, (list, tuple)) else []

	out = {}
	for wh in names:
		if not wh:
			continue
		try:
			out[wh] = _location_warehouse_level_for_warehouse_name(wh)
		except Exception:
			out[wh] = None
	return out


def _field_area_label_for_warehouse(warehouse: str) -> str | None:
	"""
	Resolve a short geo label for inventory dropdowns: prefer Geo Fencing Area with Field type
	on the walk from the warehouse-linked geo area up through parent_area; else linked leaf area_name.
	"""
	if not warehouse:
		return None
	try:
		res = get_location_for_warehouse(warehouse)
	except Exception:
		return None
	geo = (res or {}).get("geo_area")
	if not geo:
		return None
	visited: set[str] = set()
	cur = str(geo).strip()
	while cur and cur not in visited:
		visited.add(cur)
		type_link = frappe.db.get_value("Geo Fencing Area", cur, "geo_fencing_type")
		type_name = None
		if type_link:
			type_name = frappe.db.get_value("Geo Fencing Type", type_link, "geo_fencing_type_name") or type_link
		area_name = frappe.db.get_value("Geo Fencing Area", cur, "area_name")
		if (type_name or "").strip().lower() == "field":
			n = (area_name or "").strip()
			return n or None
		parent = frappe.db.get_value("Geo Fencing Area", cur, "parent_area")
		cur = (parent or "").strip()
	leaf = str((res or {}).get("geo_area") or "").strip()
	if leaf:
		n = frappe.db.get_value("Geo Fencing Area", leaf, "area_name")
		out = (n or "").strip()
		return out or None
	return None


@frappe.whitelist()
def get_warehouse_field_area_labels_for_warehouses(warehouses=None):
	"""
	Batch map each Warehouse name to a Geo Fencing field (or leaf) area_name for UI option tags.
	warehouses: JSON array string, e.g. '["WH-A","WH-B"]', or a list.
	"""
	import json

	if warehouses is None:
		names = []
	elif isinstance(warehouses, str):
		try:
			names = json.loads(warehouses)
		except Exception:
			s = warehouses.strip()
			names = [s] if s else []
	else:
		names = list(warehouses) if isinstance(warehouses, (list, tuple)) else []

	out = {}
	for wh in names:
		if not wh:
			continue
		try:
			lbl = _field_area_label_for_warehouse(wh)
		except Exception:
			lbl = None
		if lbl:
			out[wh] = lbl
	return out


def _get_equipment_status_for_destination_warehouse(warehouse: str) -> str | None:
	"""
	Return equipment status to set based on warehouse's geo type.
	Field -> In Use; Cluster or Farm -> Available; else None (do not change).
	"""
	if not warehouse:
		return None
	try:
		location_result = get_location_for_warehouse(warehouse)
	except Exception:
		return None
	geo_area = location_result.get("geo_area") if location_result else None
	if not geo_area:
		return None
	area_type = frappe.db.get_value("Geo Fencing Area", geo_area, "geo_fencing_type")
	if area_type == "Field":
		return "In Use"
	if area_type in ("Cluster", "Farm"):
		return "Available"
	return None


def _set_equipment_status_for_asset(asset_name: str, status: str) -> None:
	"""
	Set status on the equipment doc (Machinery, Implement, Hand Tool, Other Tool) linked to this Asset.
	"""
	if not asset_name or not status:
		return
	for doctype in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		name = frappe.db.get_value(doctype, {"asset": asset_name}, "name")
		if name:
			frappe.db.set_value(doctype, name, "status", status)
			break


@frappe.whitelist()
def get_assets_for_warehouse(warehouse: str):
	"""
	Get assets for a warehouse using location mapping, with fallback methods.
	
	Returns assets even if location mapping isn't perfect, using pattern matching
	on location names that might be related to the warehouse or geo area.
	Each asset includes equipment_status (Available, In Use, Maintenance, Retired) when available.
	"""
	if not warehouse:
		frappe.throw(_("warehouse is required"))
	
	def _norm(s: str) -> str:
		return "".join(ch for ch in str(s or "").lower() if ch.isalnum())
	
	# Method 1: Location subtree under canonical path (Farm / Field warehouses include child Location nodes)
	location_result = get_location_for_warehouse(warehouse)
	location = location_result.get("location") if location_result else None
	geo_area = location_result.get("geo_area") if location_result else None

	canonical_path = None
	if location:
		canonical_path = frappe.db.get_value("Location", location, "location_name")
	if not (canonical_path or "").strip() and geo_area:
		canonical_path = _build_location_name_for_geo_area(geo_area)

	location_scope: list[str] = []
	if (canonical_path or "").strip():
		location_scope = _location_docnames_under_path_prefix(canonical_path)
	if location and location not in location_scope:
		location_scope = list(location_scope) + [location]

	assets: list[dict] = []
	seen_names: set[str] = set()

	def _add_asset_rows(rows: list[dict]) -> None:
		for row in rows:
			n = row.get("name")
			if not n or n in seen_names:
				continue
			seen_names.add(n)
			assets.append(row)

	if location_scope:
		chunk_size = 300
		for i in range(0, len(location_scope), chunk_size):
			chunk = location_scope[i : i + chunk_size]
			batch = frappe.get_all(
				"Asset",
				fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
				filters=[["location", "in", chunk], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
				limit=1000,
				ignore_permissions=True,
			)
			_add_asset_rows(batch)

	# Exact node only if subtree query missed (e.g. path mismatch) but warehouse has a resolved Location
	if not assets and location:
		batch = frappe.get_all(
			"Asset",
			fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
			filters=[["location", "=", location], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
			limit=1000,
			ignore_permissions=True,
		)
		_add_asset_rows(batch)

	# Method 2: Fallback - try to find assets by location name pattern matching
	if not assets and geo_area:
		# Get location name that should exist for this geo area
		expected_location_name = _build_location_name_for_geo_area(geo_area)
		if expected_location_name:
			# Try to find locations with similar names
			all_locations = frappe.get_all(
				"Location",
				fields=["name", "location_name"],
				limit=1000
			)

			# Find locations whose names contain parts of the expected location name
			norm_expected = _norm(expected_location_name)
			matching_locations = []
			for loc in all_locations:
				loc_name = loc.get("location_name") or ""
				norm_loc = _norm(loc_name)
				# Check if location name contains key parts of expected name
				if norm_expected and norm_loc:
					# Split expected name into parts and check if any part matches
					expected_parts = [p for p in expected_location_name.split("-") if p.strip()]
					loc_parts = [p for p in loc_name.split("-") if p.strip()]
					# If at least one part matches, consider it a match
					if any(_norm(ep) in norm_loc or _norm(lp) in norm_expected for ep in expected_parts for lp in loc_parts):
						matching_locations.append(loc["name"])

			if matching_locations:
				batch = frappe.get_all(
					"Asset",
					fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
					filters=[["location", "in", matching_locations], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
					limit=1000,
					ignore_permissions=True,
				)
				_add_asset_rows(batch)

	# Method 3: Last resort - try matching by warehouse name in location names
	if not assets:
		wh_name = frappe.db.get_value("Warehouse", warehouse, "warehouse_name") or ""
		norm_wh = _norm(wh_name) or _norm(warehouse)

		if norm_wh:
			# Find locations whose names might contain the warehouse name
			all_locations = frappe.get_all(
				"Location",
				fields=["name", "location_name"],
				limit=1000
			)

			matching_locations = []
			for loc in all_locations:
				loc_name = loc.get("location_name") or ""
				norm_loc = _norm(loc_name)
				# Check if location name contains warehouse name or vice versa
				if norm_wh in norm_loc or norm_loc in norm_wh:
					matching_locations.append(loc["name"])

			if matching_locations:
				batch = frappe.get_all(
					"Asset",
					fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
					filters=[["location", "in", matching_locations], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
					limit=1000,
					ignore_permissions=True,
				)
				_add_asset_rows(batch)
	
	# When equipment location is a Field-type warehouse, show status as In Use
	location_is_field = False
	if geo_area:
		area_type = frappe.db.get_value("Geo Fencing Area", geo_area, "geo_fencing_type")
		location_is_field = (area_type == "Field")
	_enrich_equipment_display_names(assets)
	# Enrich each asset with equipment status, image, and equipment doc ref (for View modal)
	for a in assets:
		if location_is_field:
			a["equipment_status"] = "In Use"
		else:
			a["equipment_status"] = _get_equipment_status_for_asset(a.get("name"))
		# Use equipment hero_image when Asset has no image, so warehouse matches Equipment List
		if not (a.get("image") or "").strip():
			hero = _get_equipment_hero_image_for_asset(a.get("name"))
			if hero:
				a["image"] = hero
		# Equipment doc (doctype, name) so warehouse can open Equipment View modal like Equipment List
		equipment = _get_equipment_doc_for_asset(a.get("name"))
		if equipment:
			a["equipment_doctype"], a["equipment_name"] = equipment
			_enrich_attachment_from_equipment(a, equipment)
		_enrich_location_geo_labels(a)

	# Every returned asset location is in scope so clients never over-filter fallback rows
	location_scope_response = list(dict.fromkeys(location_scope))
	for a in assets:
		lid = a.get("location")
		if lid:
			location_scope_response.append(lid)
	location_scope_response = list(dict.fromkeys(location_scope_response))

	has_location_mapping = bool(location or geo_area or len(assets) > 0)

	return {
		"warehouse": warehouse,
		"location": location,
		"geo_area": geo_area,
		"assets": assets,
		"count": len(assets),
		"has_location_mapping": has_location_mapping,
		"location_scope": location_scope_response,
	}


@frappe.whitelist()
def get_all_assets():
	"""
	Return all assets (draft and submitted, not cancelled) for use when
	"All" is selected in Warehouse Inventory Equipments tab.
	"""
	assets = frappe.get_all(
		"Asset",
		fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
		filters=[["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
		limit=5000,
		order_by="modified desc",
		ignore_permissions=True,
	)
	# When equipment location is a Field-type warehouse, show status as In Use
	# Build location -> geo_fencing_type once for all assets (skip areas that fail so one bad area doesn't break response)
	areas = frappe.get_all(
		"Geo Fencing Area",
		fields=["name", "geo_fencing_type"],
		limit=2000,
		ignore_permissions=True,
	)
	loc_to_type = {}
	for area in areas:
		try:
			res = get_location_for_geo_area(area["name"])
			loc = res.get("location") if res else None
			if loc and area.get("geo_fencing_type"):
				loc_to_type[loc] = area["geo_fencing_type"]
		except Exception:
			continue
	# Use location as warehouse display when no warehouse mapping (for "all" view)
	# Enrich with equipment status and image (hero_image from equipment doc when Asset.image missing)
	out = []
	for a in assets:
		row = dict(a)
		row["warehouse"] = row.get("location") or ""
		if loc_to_type.get(row.get("location")) == "Field":
			row["equipment_status"] = "In Use"
		else:
			row["equipment_status"] = _get_equipment_status_for_asset(row.get("name"))
		if not (row.get("image") or "").strip():
			hero = _get_equipment_hero_image_for_asset(row.get("name"))
			if hero:
				row["image"] = hero
		equipment = _get_equipment_doc_for_asset(row.get("name"))
		if equipment:
			row["equipment_doctype"], row["equipment_name"] = equipment
			_enrich_attachment_from_equipment(row, equipment)
		_enrich_location_geo_labels(row)
		out.append(row)
	_enrich_equipment_display_names(out)
	return {"assets": out, "count": len(out)}


@frappe.whitelist()
def get_available_balance(item_code: str, warehouse: str):
	"""
	Get available stock balance for an item in a warehouse.
	Returns balance as string, or "Not Available" if item is not a stock item or has no balance.
	"""
	if not item_code or not warehouse:
		return "Not Available"
	
	try:
		# Check if item is stock item
		is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")
		
		if not is_stock_item:
			return "Not Available"
		
		# Get stock balance
		from erpnext.stock.utils import get_stock_balance
		balance = get_stock_balance(item_code, warehouse)
		
		if balance is not None and balance > 0:
			return str(balance)
		else:
			return "0"
	except Exception:
		return "Not Available"


@frappe.whitelist()
def get_stock_balance_for_items(warehouse: str, item_codes: str) -> dict:
	"""
	Get stock balance for multiple items in a warehouse.
	item_codes: JSON list of item codes, e.g. '["Item A", "Item B"]'
	Returns dict mapping item_code -> qty (float). Non-stock or missing items get 0.
	"""
	if not warehouse:
		return {}
	try:
		codes = frappe.parse_json(item_codes) if isinstance(item_codes, str) else item_codes
	except Exception:
		return {}
	if not codes or not isinstance(codes, (list, tuple)):
		return {}
	from erpnext.stock.utils import get_stock_balance
	result = {}
	for item_code in codes:
		if not item_code:
			continue
		try:
			is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")
			if not is_stock_item:
				result[item_code] = 0.0
				continue
			balance = get_stock_balance(item_code, warehouse)
			result[item_code] = flt(balance, 3) if balance is not None else 0.0
		except Exception:
			result[item_code] = 0.0
	return result


@frappe.whitelist()
def get_asset_availability(asset: str, warehouse: str):
	"""
	Check if an asset is available at the warehouse's location.
	Returns "Available" if asset is at the warehouse location, "Not Available" otherwise.
	"""
	if not asset or not warehouse:
		return "Not Available"
	
	try:
		# Get warehouse location
		location_result = get_location_for_warehouse(warehouse)
		warehouse_location = location_result.get("location") if location_result else None
		
		if not warehouse_location:
			return "Not Available"
		
		# Get asset location
		asset_location = frappe.db.get_value("Asset", asset, "location")
		
		if asset_location == warehouse_location:
			return "Available"
		else:
			return "Not Available"
	except Exception:
		return "Not Available"


@frappe.whitelist()
def get_equipment_location_geo_level(doctype: str | None = None, name: str | None = None):
	"""Return farm | cluster | field | null for Machinery / Implement equipment doc (for UI gates)."""
	doctype = (doctype or "").strip()
	name = (name or "").strip()
	if not doctype or not name:
		frappe.throw(_("doctype and name are required"))
	if doctype not in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		frappe.throw(_("Unsupported doctype"))
	from f2c.inventory.equipment_location_level import location_level_for_equipment_doc

	return {"level": location_level_for_equipment_doc(doctype, name)}



import json
import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

# Include both Draft (0) and Submitted (1) Assets in inventory views; exclude Cancelled (2)
ASSET_DOCSTATUS_NOT_CANCELLED = [0, 1]


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


@frappe.whitelist()
def get_warehouses_for_geo_area(geo_area: str, strict_geo_area: int = 0):
	if not geo_area:
		frappe.throw(_("geo_area is required"))
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
			)
		)
		combined = [w for w in combined if w in ledger_names]

	return {"geo_area": geo_area, "warehouses": combined}


@frappe.whitelist()
def create_logistics_transfer_ticket(
	from_warehouse: str,
	to_warehouse: str,
	stock_items: list[dict] | None = None,
	assets: list | None = None,
):
	"""
	Create ONE Logistics Transfer Ticket that links:
	- draft Stock Entry (Material Transfer) with multiple lines
	- draft Asset Movement (Transfer) with multiple assets
	"""
	if not from_warehouse or not to_warehouse:
		frappe.throw(_("from_warehouse and to_warehouse are required"))
	# Allow same warehouse for input-only (stock_items, no assets) so pickable/receivable entries are always created for approved inputs
	stock_items = stock_items or []
	assets = assets or []
	if from_warehouse == to_warehouse and assets:
		frappe.throw(_("From and To Warehouse cannot be same"))
	if from_warehouse == to_warehouse and not stock_items:
		frappe.throw(_("From and To Warehouse cannot be same"))

	if not stock_items and not assets:
		frappe.throw(_("Select at least one stock item or asset"))

	company = frappe.db.get_value("Warehouse", from_warehouse, "company")
	if not company:
		frappe.throw(_("From Warehouse has no Company"))

	# Resolve from/to locations (best effort)
	from_loc = get_location_for_warehouse(from_warehouse).get("location")
	to_loc = get_location_for_warehouse(to_warehouse).get("location")

	stock_entry_name = None
	available_items_for_entry = []
	if stock_items:
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
					available_qty = get_stock_balance(item_code, from_warehouse)
					allow_negative = is_negative_stock_allowed(item_code=item_code)
					
					# Include in Stock Entry if:
					# - Item is available (qty > 0), OR
					# - Negative stock is allowed (even if qty is 0 or negative)
					if available_qty > 0 or allow_negative:
						available_items_for_entry.append({
							"doctype": "Stock Entry Detail",
							"item_code": item_code,
							"qty": min(qty, available_qty) if available_qty > 0 else qty,
							"s_warehouse": from_warehouse,
							"t_warehouse": to_warehouse,
						})
					# If item not available and negative stock not allowed, skip Stock Entry
					# but item will still be in ticket.stock_items below
				else:
					# Non-stock item - include in Stock Entry (no availability check needed)
					available_items_for_entry.append({
						"doctype": "Stock Entry Detail",
						"item_code": item_code,
						"qty": qty,
						"s_warehouse": from_warehouse,
						"t_warehouse": to_warehouse,
					})
			except Exception as e:
				# If stock check fails, log error but continue
				# Include item anyway - let Stock Entry validation handle it
				frappe.log_error(
					f"Error checking stock for item {item_code} in warehouse {from_warehouse}: {str(e)}",
					"Stock Check"
				)
				available_items_for_entry.append({
					"doctype": "Stock Entry Detail",
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": from_warehouse,
					"t_warehouse": to_warehouse,
				})
		
		# Only create Stock Entry if there are available items and from != to (same-warehouse transfer is invalid in ERPNext)
		# Note: All items (available or not) are still included in ticket.stock_items below
		if available_items_for_entry and from_warehouse != to_warehouse:
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"company": company,
					"purpose": "Material Transfer",
					"stock_entry_type": "Material Transfer",
					"from_warehouse": from_warehouse,
					"to_warehouse": to_warehouse,
					"posting_date": now_datetime().date(),
					"posting_time": now_datetime().time().replace(microsecond=0).isoformat(),
					"items": available_items_for_entry,
				}
			)
			se.insert(ignore_permissions=True)
			stock_entry_name = se.name

	asset_movement_name = None
	asset_item_rows_for_ticket: list[dict] = []
	if assets:
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
				requests.append({"asset": a.get("asset"), "qty": a.get("qty")})

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
			asset_item_rows_for_ticket.append({"asset": move_asset.name, "qty": move_qty})

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

	ticket = frappe.get_doc(
		{
			"doctype": "Logistics Transfer Ticket",
			"from_warehouse": from_warehouse,
			"to_warehouse": to_warehouse,
			"from_location": from_loc,
			"to_location": to_loc,
			"stock_entry": stock_entry_name,
			"asset_movement": asset_movement_name,
			"status": "Pending Pickup",
			"stock_items": [
				{"item_code": r.get("item_code"), "qty": flt(r.get("qty"))} for r in (stock_items or []) if r.get("item_code")
			],
			"asset_items": [{"asset": r.get("asset"), "qty": r.get("qty") or 1} for r in asset_item_rows_for_ticket if r.get("asset")],
		}
	)
	ticket.insert(ignore_permissions=True)

	return {
		"ticket": ticket.name,
		"status": ticket.status,
		"stock_entry": stock_entry_name,
		"asset_movement": asset_movement_name,
	}


@frappe.whitelist()
def mark_dispatched(ticket_name: str, dispatch_photo_url=None):
	"""dispatch_photo_url can be a single URL string, list of URLs, or JSON string of URLs."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can be dispatched"))
	ticket.status = "In Transit"
	ticket.dispatched_on = now_datetime()
	photo_json = _normalize_photo_urls(dispatch_photo_url)
	if photo_json:
		ticket.dispatch_photo = photo_json
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_received(ticket_name: str, receive_photo_url=None):
	"""receive_photo_url can be a single URL string, list of URLs, or JSON string of URLs."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	if ticket.status != "In Transit":
		frappe.throw(_("Only In Transit tickets can be marked Received"))

	if ticket.stock_entry:
		se = frappe.get_doc("Stock Entry", ticket.stock_entry)
		if se.docstatus == 0:
			se.submit()

	if ticket.asset_movement:
		am = frappe.get_doc("Asset Movement", ticket.asset_movement)
		if am.docstatus == 0:
			am.submit()

	new_status = _get_equipment_status_for_destination_warehouse(ticket.to_warehouse)
	if new_status and getattr(ticket, "asset_items", None):
		for row in ticket.asset_items:
			if row.get("asset"):
				_set_equipment_status_for_asset(row.asset, new_status)

	ticket.status = "Received"
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

	new_status = _get_equipment_status_for_destination_warehouse(ticket.from_warehouse)
	if new_status and getattr(ticket, "asset_items", None):
		for row in ticket.asset_items:
			if row.get("asset"):
				_set_equipment_status_for_asset(row.asset, new_status)

	ticket.status = "In Transit"
	ticket.received_on = None
	ticket.receive_photo = None
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_reported(ticket_name: str, reason: str = ""):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	if ticket.status in ("Received", "Cancelled"):
		frappe.throw(_("Cannot report a Received/Cancelled ticket"))
	ticket.status = "Reported"
	ticket.report_reason = reason or ticket.report_reason
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_cancelled(ticket_name: str, reason: str = ""):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
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
	
	# Method 1: Try normal location-based lookup
	location_result = get_location_for_warehouse(warehouse)
	location = location_result.get("location") if location_result else None
	geo_area = location_result.get("geo_area") if location_result else None
	
	assets = []
	
	# If we have a location, use it directly
	if location:
		assets = frappe.get_all(
			"Asset",
			fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
			filters=[["location", "=", location], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
			limit=1000,
			ignore_permissions=True,
		)
	
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
				assets = frappe.get_all(
					"Asset",
					fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
					filters=[["location", "in", matching_locations], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
					limit=1000,
					ignore_permissions=True,
				)
	
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
				assets = frappe.get_all(
					"Asset",
					fields=["name", "asset_name", "item_code", "asset_category", "location", "status", "asset_quantity", "image"],
					filters=[["location", "in", matching_locations], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
					limit=1000,
					ignore_permissions=True,
				)
	
	# When equipment location is a Field-type warehouse, show status as In Use
	location_is_field = False
	if geo_area:
		area_type = frappe.db.get_value("Geo Fencing Area", geo_area, "geo_fencing_type")
		location_is_field = (area_type == "Field")
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
	
	return {
		"warehouse": warehouse,
		"location": location,
		"geo_area": geo_area,
		"assets": assets,
		"count": len(assets),
		"has_location_mapping": bool(location)
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
		out.append(row)
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



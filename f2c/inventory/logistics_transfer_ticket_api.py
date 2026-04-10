import json
import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime

from f2c.farm_report.doctype.farm_report_ticket.farm_report_ticket import create_report_and_mark_reported

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


def _reapply_tractor_implement_links_after_transfer(ticket) -> None:
	"""
	Ensure Machinery.current_implement and Implement.attached_to_machinery stay consistent
	for ticket rows that record a paired implement (runs Machinery save → existing sync hooks).
	"""
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
		if (m.current_implement or "") == pi and impl_owner == mach_name:
			continue
		m.current_implement = pi
		m.save(ignore_permissions=True)


def _tractor_machinery_name_for_moved_asset(asset_name: str | None) -> str | None:
	"""If this Asset is linked to a Machinery row with type Tractor, return that Machinery name."""
	if not asset_name:
		return None
	row = frappe.db.get_value(
		"Machinery",
		{"asset": asset_name},
		["name", "machinery_type"],
		as_dict=True,
	)
	if not row:
		return None
	if (row.get("machinery_type") or "").strip() == "Tractor":
		return row.get("name")
	return None


def _default_transport_vehicle_from_assets(assets: list | None) -> str | None:
	"""
	Use the moved tractor as Transport vehicle on the LTT when no vehicle was passed.
	Skips non-tractor assets (e.g. implements co-moved with the tractor).
	"""
	if not assets:
		return None
	for a in assets:
		an = a if isinstance(a, str) else (a.get("asset") if isinstance(a, dict) else None)
		if not an:
			continue
		mname = _tractor_machinery_name_for_moved_asset(an)
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
):
	"""
	Create ONE Logistics Transfer Ticket.
	When both From and To are Warehouse: creates draft Stock Entry and optionally Asset Movement.
	When either end is Other (external location): no Stock Entry/Asset Movement (tracking-only).
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

	# When either end is Other: tracking-only, no SE/AM
	both_warehouse = from_location_type == "Warehouse" and to_location_type == "Warehouse"
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

	if both_warehouse and stock_items:
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

	if planned_pickup_on:
		ticket_data["planned_pickup_on"] = get_datetime(planned_pickup_on)
	if planned_drop_off_on:
		ticket_data["planned_drop_off_on"] = get_datetime(planned_drop_off_on)

	tv = (transport_vehicle or "").strip()
	if not tv:
		tv = (_default_transport_vehicle_from_assets(assets) or "").strip()
	if tv:
		ticket_data["transport_vehicle"] = tv

	ticket = frappe.get_doc(ticket_data)
	ticket.insert(ignore_permissions=True)

	# Default planned dates to creation when not provided
	if not ticket_data.get("planned_pickup_on"):
		ticket.planned_pickup_on = ticket.creation
	if not ticket_data.get("planned_drop_off_on"):
		ticket.planned_drop_off_on = ticket.creation
	if ticket.planned_pickup_on or ticket.planned_drop_off_on:
		ticket.save(ignore_permissions=True)

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
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_received(ticket_name: str, receive_photo_url=None):
	"""receive_photo_url can be a single URL string, list of URLs, or JSON string of URLs.
	Allowed when status=In Transit and drop_off_phase is Delivered (new flow) or In Transit (legacy)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	if ticket.status != "In Transit":
		frappe.throw(_("Only In Transit tickets can be marked Received"))
	drop_phase = getattr(ticket, "drop_off_phase", None) or ""
	if drop_phase not in ("Delivered", "In Transit"):
		frappe.throw(_("Drop off phase must be Delivered or In Transit to mark Received"))

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
			_enrich_attachment_from_equipment(a, equipment)
	
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
			_enrich_attachment_from_equipment(row, equipment)
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



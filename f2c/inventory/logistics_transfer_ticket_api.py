import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime


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
	# naming convention: Farm-Cluster-Field-Block (based on area_name hierarchy)
	parts = _geo_area_path_names(geo_area_name)
	return "-".join([p.strip() for p in parts if p and str(p).strip()])


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
	loc = frappe.db.get_value("Location", {"location_name": location_name}, "name")
	return {"warehouse": warehouse, "geo_area": geo_area, "location": loc, "location_name": location_name}


@frappe.whitelist()
def get_location_for_geo_area(geo_area: str):
	if not geo_area:
		frappe.throw(_("geo_area is required"))
	location_name = _build_location_name_for_geo_area(geo_area)
	loc = frappe.db.get_value("Location", {"location_name": location_name}, "name")
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
	if from_warehouse == to_warehouse:
		frappe.throw(_("From and To Warehouse cannot be same"))

	stock_items = stock_items or []
	assets = assets or []

	if not stock_items and not assets:
		frappe.throw(_("Select at least one stock item or asset"))

	company = frappe.db.get_value("Warehouse", from_warehouse, "company")
	if not company:
		frappe.throw(_("From Warehouse has no Company"))

	# Resolve from/to locations (best effort)
	from_loc = get_location_for_warehouse(from_warehouse).get("location")
	to_loc = get_location_for_warehouse(to_warehouse).get("location")

	stock_entry_name = None
	if stock_items:
		items = []
		for row in stock_items:
			item_code = row.get("item_code")
			qty = flt(row.get("qty"))
			if not item_code or qty <= 0:
				continue
			items.append(
				{
					"doctype": "Stock Entry Detail",
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": from_warehouse,
					"t_warehouse": to_warehouse,
				}
			)
		if not items:
			frappe.throw(_("No valid stock items (qty > 0)"))

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
				"items": items,
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
def mark_dispatched(ticket_name: str):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can be dispatched"))
	ticket.status = "In Transit"
	ticket.dispatched_on = now_datetime()
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_received(ticket_name: str):
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

	ticket.status = "Received"
	ticket.received_on = now_datetime()
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status, "stock_entry": ticket.stock_entry, "asset_movement": ticket.asset_movement}


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



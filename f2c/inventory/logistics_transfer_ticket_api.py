import frappe
from frappe import _
from frappe.utils import flt, now_datetime


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

	geo_area = frappe.db.get_value(
		"Geo Fencing Area Warehouse", {"warehouse": warehouse}, "parent", order_by="modified desc"
	)
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
def get_warehouses_for_geo_area(geo_area: str):
	if not geo_area:
		frappe.throw(_("geo_area is required"))
	warehouses = frappe.get_all(
		"Geo Fencing Area Warehouse",
		fields=["warehouse"],
		filters={"parent": geo_area, "parenttype": "Geo Fencing Area", "warehouse": ["is", "set"]},
		limit_page_length=0,
	)
	return {"geo_area": geo_area, "warehouses": [w["warehouse"] for w in warehouses if w.get("warehouse")]}


@frappe.whitelist()
def create_logistics_transfer_ticket(
	from_warehouse: str,
	to_warehouse: str,
	stock_items: list[dict] | None = None,
	assets: list[str] | None = None,
):
	"""
	Create ONE Logistics Transfer Ticket that links:
	- draft Stock Entry (Material Transfer) with multiple lines
	- draft Asset Movement (Transfer) with multiple assets
	"""
	if not from_warehouse or not to_warehouse:
		frappe.throw(_("from_warehouse and to_warehouse are required"))

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
	if assets:
		asset_rows = []
		first_company = None
		for asset in assets:
			asset_doc = frappe.get_doc("Asset", asset)
			if not first_company:
				first_company = asset_doc.company
			asset_rows.append(
				{
					"doctype": "Asset Movement Item",
					"asset": asset_doc.name,
					"asset_name": asset_doc.asset_name,
					"source_location": asset_doc.location,
					"target_location": to_loc or asset_doc.location,
				}
			)
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
			"asset_items": [{"asset": a} for a in (assets or []) if a],
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



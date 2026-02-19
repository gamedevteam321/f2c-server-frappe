import frappe
from frappe import _
from frappe.utils import flt, now_datetime


@frappe.whitelist()
def create_stock_transfer_ticket(from_warehouse: str, to_warehouse: str, item_code: str, qty: float):
	"""
	Create a draft Stock Entry (Material Transfer) and a Stock Transfer Ticket (Pending Pickup).
	Returns {ticket, stock_entry}.
	"""
	if not from_warehouse or not to_warehouse or not item_code:
		frappe.throw(_("from_warehouse, to_warehouse and item_code are required"))

	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("qty must be > 0"))

	company = frappe.db.get_value("Warehouse", from_warehouse, "company")
	if not company:
		frappe.throw(_("From Warehouse has no Company"))

	stock_entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"company": company,
			"purpose": "Material Transfer",
			"stock_entry_type": "Material Transfer",
			"from_warehouse": from_warehouse,
			"to_warehouse": to_warehouse,
			"posting_date": now_datetime().date(),
			"posting_time": now_datetime().time().replace(microsecond=0).isoformat(),
			"items": [
				{
					"doctype": "Stock Entry Detail",
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": from_warehouse,
					"t_warehouse": to_warehouse,
				}
			],
		}
	)
	stock_entry.insert(ignore_permissions=True)

	ticket = frappe.get_doc(
		{
			"doctype": "Stock Transfer Ticket",
			"from_warehouse": from_warehouse,
			"to_warehouse": to_warehouse,
			"item_code": item_code,
			"qty": qty,
			"stock_entry": stock_entry.name,
			"status": "Pending Pickup",
		}
	)
	ticket.insert(ignore_permissions=True)

	return {"ticket": ticket.name, "stock_entry": stock_entry.name}


@frappe.whitelist()
def mark_dispatched(ticket_name: str):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Stock Transfer Ticket", ticket_name)
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
	ticket = frappe.get_doc("Stock Transfer Ticket", ticket_name)
	if ticket.status != "In Transit":
		frappe.throw(_("Only In Transit tickets can be marked Received"))
	if not ticket.stock_entry:
		frappe.throw(_("Ticket has no linked Stock Entry"))

	se = frappe.get_doc("Stock Entry", ticket.stock_entry)
	if se.docstatus == 0:
		se.submit()

	ticket.status = "Received"
	ticket.received_on = now_datetime()
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status, "stock_entry": se.name}


@frappe.whitelist()
def revert_received(ticket_name: str):
	"""Restore a Received ticket back to In Transit by cancelling the linked Stock Entry."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Stock Transfer Ticket", ticket_name)
	if ticket.status != "Received":
		frappe.throw(_("Only Received tickets can be restored to In Transit"))

	if ticket.stock_entry:
		se = frappe.get_doc("Stock Entry", ticket.stock_entry)
		if se.docstatus == 1:
			se.cancel()

	ticket.status = "In Transit"
	ticket.received_on = None
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_reported(ticket_name: str, reason: str = ""):
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Stock Transfer Ticket", ticket_name)
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
	ticket = frappe.get_doc("Stock Transfer Ticket", ticket_name)
	if ticket.status == "Received":
		frappe.throw(_("Cannot cancel a Received ticket"))
	ticket.status = "Cancelled"
	if reason:
		ticket.report_reason = reason
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}



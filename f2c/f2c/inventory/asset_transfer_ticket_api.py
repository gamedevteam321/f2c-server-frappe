import frappe
from frappe import _
from frappe.utils import now_datetime


@frappe.whitelist()
def create_asset_transfer_ticket(asset: str, target_location: str):
	"""
	Create a draft Asset Movement (purpose Transfer) and an Asset Transfer Ticket (Pending Pickup).
	Returns {ticket, asset_movement}.
	"""
	if not asset or not target_location:
		frappe.throw(_("asset and target_location are required"))

	asset_doc = frappe.get_doc("Asset", asset)
	if not asset_doc.location:
		frappe.throw(_("Asset {0} has no current Location").format(asset_doc.name))

	movement = frappe.get_doc(
		{
			"doctype": "Asset Movement",
			"company": asset_doc.company,
			"purpose": "Transfer",
			"transaction_date": now_datetime(),
			"assets": [
				{
					"doctype": "Asset Movement Item",
					"asset": asset_doc.name,
					"asset_name": asset_doc.asset_name,
					"source_location": asset_doc.location,
					"target_location": target_location,
				}
			],
		}
	)
	movement.insert(ignore_permissions=True)

	ticket = frappe.get_doc(
		{
			"doctype": "Asset Transfer Ticket",
			"asset": asset_doc.name,
			"asset_movement": movement.name,
			"source_location": asset_doc.location,
			"target_location": target_location,
			"status": "Pending Pickup",
		}
	)
	ticket.insert(ignore_permissions=True)

	return {"ticket": ticket.name, "asset_movement": movement.name}


@frappe.whitelist()
def mark_picked_up(ticket_name: str):
	"""Move ticket from Pending Pickup -> In Transit."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Asset Transfer Ticket", ticket_name)
	if ticket.status != "Pending Pickup":
		frappe.throw(_("Only Pending Pickup tickets can be marked Picked Up"))
	ticket.status = "In Transit"
	ticket.picked_up_on = now_datetime()
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_received(ticket_name: str):
	"""
	Mark ticket Received and submit the linked Asset Movement.
	This is the point where the transfer is finalized.
	"""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Asset Transfer Ticket", ticket_name)
	if ticket.status != "In Transit":
		frappe.throw(_("Only In Transit tickets can be marked Received"))

	if not ticket.asset_movement:
		frappe.throw(_("Ticket has no linked Asset Movement"))

	movement = frappe.get_doc("Asset Movement", ticket.asset_movement)
	if movement.docstatus == 0:
		movement.submit()

	ticket.status = "Received"
	ticket.received_on = now_datetime()
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status, "asset_movement": movement.name}


@frappe.whitelist()
def revert_received(ticket_name: str):
	"""Restore a Received ticket back to In Transit by cancelling the linked Asset Movement."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Asset Transfer Ticket", ticket_name)
	if ticket.status != "Received":
		frappe.throw(_("Only Received tickets can be restored to In Transit"))

	if ticket.asset_movement:
		movement = frappe.get_doc("Asset Movement", ticket.asset_movement)
		if movement.docstatus == 1:
			movement.cancel()

	ticket.status = "In Transit"
	ticket.received_on = None
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_reported(ticket_name: str, reason: str = ""):
	"""Mark ticket as Reported with an optional reason (allowed from any non-Received state)."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Asset Transfer Ticket", ticket_name)
	if ticket.status in ("Received", "Cancelled"):
		frappe.throw(_("Cannot report a Received ticket"))
	ticket.status = "Reported"
	ticket.report_reason = reason or ticket.report_reason
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}


@frappe.whitelist()
def mark_cancelled(ticket_name: str, reason: str = ""):
	"""Cancel a ticket (prototype). Does not cancel the linked Asset Movement, only blocks workflow actions."""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	ticket = frappe.get_doc("Asset Transfer Ticket", ticket_name)
	if ticket.status == "Received":
		frappe.throw(_("Cannot cancel a Received ticket"))
	ticket.status = "Cancelled"
	if reason:
		ticket.report_reason = reason
	ticket.save(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status}



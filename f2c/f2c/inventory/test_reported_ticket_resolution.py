import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.inventory.logistics_transfer_ticket_api import mark_reported as report_logistics_ticket
from f2c.inventory.stock_transfer_ticket_api import mark_reported as report_stock_ticket


def _first_two_warehouses() -> tuple[str, str]:
	rows = frappe.get_all("Warehouse", fields=["name"], limit=2)
	if len(rows) < 2:
		raise unittest.SkipTest("At least two warehouses are required for transfer ticket tests.")
	return rows[0]["name"], rows[1]["name"]


def _first_item_code() -> str:
	rows = frappe.get_all("Item", fields=["name"], limit=1)
	if not rows:
		raise unittest.SkipTest("At least one item is required for stock transfer ticket tests.")
	return rows[0]["name"]


class TestReportedTicketResolution(FrappeTestCase):
	def tearDown(self):
		for doctype in ("Stock Transfer Ticket", "Logistics Transfer Ticket"):
			for name in frappe.get_all(doctype, pluck="name", filters={"owner": frappe.session.user}) or []:
				if frappe.db.exists(doctype, name):
					frappe.delete_doc(doctype, name, ignore_permissions=True, force=1)

	def test_stock_ticket_resolve_restores_previous_status(self):
		from_warehouse, to_warehouse = _first_two_warehouses()
		item_code = _first_item_code()

		ticket = frappe.get_doc(
			{
				"doctype": "Stock Transfer Ticket",
				"from_warehouse": from_warehouse,
				"to_warehouse": to_warehouse,
				"item_code": item_code,
				"qty": 1,
				"status": "In Transit",
			}
		).insert(ignore_permissions=True)

		report_stock_ticket(ticket.name, "Damaged package")
		ticket.reload()

		self.assertEqual(ticket.status, "Reported")
		self.assertEqual(ticket.previous_status_before_report, "In Transit")

		from f2c.inventory.stock_transfer_ticket_api import resolve_reported as resolve_stock_ticket

		resolve_stock_ticket(ticket.name, "Repacked and cleared")
		ticket.reload()

		self.assertEqual(ticket.status, "In Transit")
		self.assertEqual(ticket.resolution_note, "Repacked and cleared")

	def test_logistics_ticket_resolve_restores_previous_status(self):
		from_warehouse, to_warehouse = _first_two_warehouses()

		ticket = frappe.get_doc(
			{
				"doctype": "Logistics Transfer Ticket",
				"from_warehouse": from_warehouse,
				"to_warehouse": to_warehouse,
				"status": "Pending Pickup",
			}
		).insert(ignore_permissions=True)

		report_logistics_ticket(ticket.name, "Mismatch reported")
		ticket.reload()

		self.assertEqual(ticket.status, "Reported")
		self.assertEqual(ticket.previous_status_before_report, "Pending Pickup")

		from f2c.inventory.logistics_transfer_ticket_api import resolve_reported as resolve_logistics_ticket

		resolve_logistics_ticket(ticket.name, "Driver issue cleared")
		ticket.reload()

		self.assertEqual(ticket.status, "Pending Pickup")
		self.assertEqual(ticket.resolution_note, "Driver issue cleared")

	def test_resolve_requires_resolution_note(self):
		from_warehouse, to_warehouse = _first_two_warehouses()
		item_code = _first_item_code()

		ticket = frappe.get_doc(
			{
				"doctype": "Stock Transfer Ticket",
				"from_warehouse": from_warehouse,
				"to_warehouse": to_warehouse,
				"item_code": item_code,
				"qty": 1,
				"status": "In Transit",
			}
		).insert(ignore_permissions=True)

		report_stock_ticket(ticket.name, "Damaged package")

		from f2c.inventory.stock_transfer_ticket_api import resolve_reported as resolve_stock_ticket

		with self.assertRaises(frappe.ValidationError):
			resolve_stock_ticket(ticket.name, "")

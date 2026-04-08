# Copyright (c) 2025, Orgatek and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import (
	get_machinery_schedule_details,
	get_transfer_tickets_for_schedule,
)


class TestCropPlanSchedule(FrappeTestCase):
	def test_get_machinery_schedule_details_returns_current_implement_for_tractor(self):
		def fake_get_value(doctype, filters=None, fieldname=None, as_dict=False):
			if doctype == "Machinery":
				self.assertEqual(filters, {"asset": "AST-TRACTOR-001"})
				return {
					"name": "MACH-TRACTOR-001",
					"machinery_type": "Tractor",
					"current_implement": "IMP-ROTAVATOR-001",
				}
			if doctype == "Implement" and filters == "IMP-ROTAVATOR-001":
				self.assertEqual(fieldname, "implement_name")
				return "Rotavator 01"
			raise AssertionError(f"Unexpected get_value call: {doctype}, {filters}, {fieldname}, {as_dict}")

		with patch("f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.get_value", side_effect=fake_get_value):
			result = get_machinery_schedule_details("AST-TRACTOR-001")

		self.assertEqual(result["asset"], "AST-TRACTOR-001")
		self.assertEqual(result["machinery"], "MACH-TRACTOR-001")
		self.assertTrue(result["is_tractor"])
		self.assertEqual(result["current_implement"], "IMP-ROTAVATOR-001")
		self.assertEqual(result["current_implement_name"], "Rotavator 01")

	def test_get_machinery_schedule_details_returns_empty_when_asset_has_no_machinery(self):
		with patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.get_value",
			return_value=None,
		):
			result = get_machinery_schedule_details("AST-UNKNOWN-001")

		self.assertEqual(
			result,
			{
				"asset": "AST-UNKNOWN-001",
				"machinery": None,
				"machinery_type": None,
				"is_tractor": False,
				"current_implement": None,
				"current_implement_name": None,
			},
		)

	def test_get_transfer_tickets_for_schedule_prefers_explicit_batch_links(self):
		schedule = frappe._dict(
			{
				"name": "CPS-TEST-0001",
				"field": "FIELD-001",
			}
		)
		schedule._get_target_warehouse_for_field = lambda field: "FIELD-WH-001"
		schedule._collect_equipment_assets = lambda: ["AST-TRACTOR-001"]

		def fake_get_doc(doctype, name=None):
			if doctype == "Crop Plan Schedule":
				return schedule
			raise AssertionError(f"Unexpected get_doc call: {doctype}, {name}")

		def fake_get_all(doctype, filters=None, fields=None, order_by=None, limit=None, pluck=None, limit_page_length=None):
			if doctype == "Logistics Batch":
				self.assertEqual(filters, {"source_doctype": "Crop Plan Schedule", "source_name": "CPS-TEST-0001"})
				return [frappe._dict({"name": "LB-TEST-0001"})]
			if doctype == "Logistics Transfer Ticket":
				self.assertEqual(filters, {"logistics_batch": "LB-TEST-0001", "status": ["!=", "Cancelled"]})
				return [
					frappe._dict(
						{
							"name": "LTT-TEST-0001",
							"status": "Pending Pickup",
							"creation": "2026-04-08 08:00:00",
							"stock_entry": "STE-TEST-0001",
						}
					)
				]
			if doctype == "Stock Entry":
				self.assertEqual(filters, {"name": ["in", ["STE-TEST-0001"]], "docstatus": ["<", 2]})
				return [frappe._dict({"name": "STE-TEST-0001", "docstatus": 0, "posting_date": "2026-04-08"})]
			raise AssertionError(f"Unexpected get_all call: {doctype}, {filters}, {fields}, {order_by}, {limit}, {pluck}, {limit_page_length}")

		with patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.get_doc",
			side_effect=fake_get_doc,
		), patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.get_all",
			side_effect=fake_get_all,
		):
			result = get_transfer_tickets_for_schedule("CPS-TEST-0001")

		self.assertEqual(
			result,
			{
				"logistics_tickets": [
					{"name": "LTT-TEST-0001", "status": "Pending Pickup", "creation": "2026-04-08 08:00:00"}
				],
				"stock_entries": [
					{"name": "STE-TEST-0001", "docstatus": 0, "posting_date": "2026-04-08"}
				],
			},
		)

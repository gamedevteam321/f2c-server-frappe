# Copyright (c) 2025, Orgatek and Contributors
# See license.txt

from unittest.mock import patch

# import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import (
	get_machinery_schedule_details,
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

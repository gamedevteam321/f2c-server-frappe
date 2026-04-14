# Copyright (c) 2025, Orgatek and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

# import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import (
	get_available_implements_for_cluster,
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
		self.assertTrue(result["requires_diesel_planning"])

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
				"requires_diesel_planning": False,
				"current_implement": None,
				"current_implement_name": None,
			},
		)

	def test_get_machinery_schedule_details_thresher_requires_diesel_planning(self):
		def fake_get_value(doctype, filters=None, fieldname=None, as_dict=False):
			if doctype == "Machinery":
				self.assertEqual(filters, {"asset": "AST-THRESHER-001"})
				return {
					"name": "MACH-THRESHER-001",
					"machinery_type": "Thresher",
					"current_implement": None,
				}
			raise AssertionError(f"Unexpected get_value call: {doctype}, {filters}, {fieldname}, {as_dict}")

		with patch("f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.get_value", side_effect=fake_get_value):
			result = get_machinery_schedule_details("AST-THRESHER-001")

		self.assertFalse(result["is_tractor"])
		self.assertTrue(result["requires_diesel_planning"])

	@patch("f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.resolve_cluster_warehouses_locations")
	def test_get_available_implements_for_cluster_physical_only(self, mock_resolve):
		mock_resolve.return_value = {
			"cluster": "CLU-TEST",
			"warehouse_list": ["WH-CLUSTER"],
			"location_list": ["LOC-1"],
		}
		with patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.sql",
			return_value=[{"name": "IMP-PHYS", "implement_name": "Physical Imp"}],
		), patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.get_all",
			return_value=[],
		):
			out = get_available_implements_for_cluster(
				field="FIELD-1",
				block=None,
				planned_start=None,
			)
		self.assertEqual(len(out), 1)
		self.assertEqual(out[0]["name"], "IMP-PHYS")
		self.assertEqual(out[0]["availability"], "at_cluster")

	@patch("f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.resolve_cluster_warehouses_locations")
	def test_get_available_implements_for_cluster_in_transit_ltt(self, mock_resolve):
		mock_resolve.return_value = {
			"cluster": "CLU-TEST",
			"warehouse_list": ["WH-CLUSTER"],
			"location_list": [],
		}

		class FakeRow:
			def get(self, key, default=None):
				data = {"paired_implement": "IMP-LTT", "asset": None}
				return data.get(key, default)

		class FakeDoc:
			planned_drop_off_on = "2026-06-01 08:00:00"

			def get(self, key, default=None):
				if key == "asset_items":
					return [FakeRow()]
				if key == "planned_drop_off_on":
					return self.planned_drop_off_on
				return default

		def fake_get_value(doctype, *args, **kwargs):
			if doctype == "Implement" and args and args[0] == "IMP-LTT" and kwargs.get("as_dict"):
				return SimpleNamespace(implement_name="Inbound Roto", docstatus=0)
			return None

		with patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.sql",
			return_value=[],
		), patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.get_all",
			return_value=[SimpleNamespace(name="LTT-00001")],
		), patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.get_doc",
			return_value=FakeDoc(),
		), patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.get_value",
			side_effect=fake_get_value,
		):
			out = get_available_implements_for_cluster(
				field="FIELD-1",
				planned_start="2026-06-01 12:00:00",
			)
		self.assertEqual(len(out), 1)
		self.assertEqual(out[0]["name"], "IMP-LTT")
		self.assertEqual(out[0]["availability"], "in_transit")
		self.assertIn("planned_drop_off_on", out[0])

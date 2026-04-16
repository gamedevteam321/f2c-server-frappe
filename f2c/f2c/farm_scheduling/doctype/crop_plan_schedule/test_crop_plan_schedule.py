# Copyright (c) 2025, Orgatek and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

# import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import (
	CropPlanSchedule,
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

	def test_create_equipment_transfer_tickets_runs_vehicle_then_machinery(self):
		"""Forward LTT split: orchestration calls vehicle/consumables path before machinery path."""
		order: list[str] = []

		def track_vehicle(self):
			order.append("vehicle")

		def track_machinery(self):
			order.append("machinery")

		with patch.object(
			CropPlanSchedule, "_create_vehicle_consumables_transfer_tickets", track_vehicle
		), patch.object(CropPlanSchedule, "_create_machinery_transfer_tickets", track_machinery):
			doc = CropPlanSchedule({})
			doc._create_equipment_transfer_tickets()

		self.assertEqual(order, ["vehicle", "machinery"])

	def test_machinery_transfer_unit_payloads_one_unit_per_machinery_row(self):
		"""Each machinery child row is a separate transfer unit (same source warehouse is allowed)."""
		from types import SimpleNamespace

		def fake_expand(primary: str, paired_implement=None):
			return [{"asset": primary, "qty": 1}]

		with patch(
			"f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests",
			side_effect=fake_expand,
		), patch(
			"f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.frappe.db.get_value",
			return_value="MACH-DOC",
		):
			doc = CropPlanSchedule({"field": "F1"})
			doc.machinery = [
				SimpleNamespace(asset="TRACTOR-A"),
				SimpleNamespace(asset="TRACTOR-B"),
			]
			doc.implements = []
			doc.hand_tools = []
			doc.other_tools = []
			payloads = doc._machinery_transfer_unit_payloads()

		self.assertEqual(len(payloads), 2)
		self.assertEqual(payloads[0][0][0]["asset"], "TRACTOR-A")
		self.assertEqual(payloads[1][0][0]["asset"], "TRACTOR-B")

	def test_create_machinery_transfer_tickets_calls_create_per_unit(self):
		"""Two machinery rows at the same from_warehouse produce two create_logistics_transfer_ticket calls."""
		from types import SimpleNamespace

		calls: list[tuple] = []

		def fake_expand(primary: str, paired_implement=None):
			return [{"asset": primary, "qty": 1}]

		def fake_create(**kwargs):
			calls.append((kwargs.get("from_warehouse"), kwargs.get("assets")))
			return {"ticket": f"LTT-{len(calls)}"}

		def fake_get_location(_wh):
			return {"location": "LOC-1"}

		with patch(
			"f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests",
			side_effect=fake_expand,
		), patch(
			"f2c.inventory.logistics_transfer_ticket_api.get_location_for_warehouse",
			side_effect=fake_get_location,
		), patch(
			"f2c.inventory.logistics_transfer_ticket_api.create_logistics_transfer_ticket",
			side_effect=fake_create,
		), patch(
			"f2c.inventory.logistics_transfer_ticket_api.planned_pickup_drop_for_activity_start",
			return_value=None,
		), patch.object(CropPlanSchedule, "status", "Scheduled"), patch.object(
			CropPlanSchedule, "field", "FIELD-1"
		), patch.object(
			CropPlanSchedule, "_machinery_transfer_unit_payloads",
			return_value=[
				([{"asset": "T1", "qty": 1}], "MACH-1"),
				([{"asset": "T2", "qty": 1}], "MACH-2"),
			],
		), patch.object(
			CropPlanSchedule, "_get_target_warehouse_for_field", return_value="WH-FIELD"
		), patch.object(
			CropPlanSchedule, "_get_source_warehouse_for_equipment_asset", return_value="WH-CLUSTER"
		), patch.object(CropPlanSchedule, "_find_open_equipment_ltt_duplicate", return_value=None):
			doc = CropPlanSchedule({})
			doc._create_machinery_transfer_tickets()

		self.assertEqual(len(calls), 2)
		self.assertEqual(calls[0][0], "WH-CLUSTER")
		self.assertEqual(calls[1][0], "WH-CLUSTER")

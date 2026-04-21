from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.farm_execution.doctype.farm_task_execution import farm_task_execution as fte_module


class DummyExecDoc:
	def __init__(self):
		self.name = None
		self.blocks = []
		self.equipment = []
		self.inputs = []
		self.labour_attendance = []

	def append(self, fieldname, value):
		getattr(self, fieldname).append(frappe._dict(value))

	def save(self, ignore_permissions=True):
		return None


class DummyDb:
	def __init__(self):
		self.set_value_calls = []

	def get_value(self, doctype, filters, fieldname, as_dict=False):
		return None

	def begin(self):
		return None

	def sql(self, query, values=None, as_dict=False):
		return [{}]

	def set_value(self, doctype, name, fieldname, value, update_modified=False):
		self.set_value_calls.append((doctype, name, fieldname, value, update_modified))

	def commit(self):
		return None

	def rollback(self):
		return None


class TestFarmTaskExecution(FrappeTestCase):
	def _make_schedule_doc(self):
		doc = frappe._dict(
			{
				"name": "CPS-TEST-0001",
				"execution_ref": None,
				"planned_start": "2026-04-07 08:00:00",
				"planned_end": "2026-04-07 10:00:00",
				"farm_activity": "FA-TEST-0001",
				"activity_name": "Rotavation",
				"sequence": 1,
				"approved_input_mix": None,
				"male_count": 0,
				"female_count": 0,
				"block": None,
				"block_name": None,
				"block_area_acres": 0,
				"no_of_seedlings": 0,
				"is_spray": 0,
				"machinery": [
					frappe._dict(
						{
							"asset": "AST-TRACTOR-001",
							"asset_name": "Tractor T1",
							"planned_hours": 2,
							"return_type": "Non Returnable",
							"paired_implement": "IMP-ROTAVATOR-001",
						}
					)
				],
				"implements": [],
				"hand_tools": [],
				"other_tools": [],
				"inputs": [],
			}
		)
		doc.reload = lambda: None
		return doc

	def _make_on_demand_doc(self):
		doc = frappe._dict(
			{
				"name": "ODA-TEST-0001",
				"execution_ref": None,
				"planned_start": "2026-04-07 08:00:00",
				"planned_end": "2026-04-07 10:00:00",
				"activity": "FA-TEST-0001",
				"activity_name": "Rotavation",
				"approved_input_mix": None,
				"male_count": 0,
				"female_count": 0,
				"field": "FIELD-001",
				"is_spray": 0,
				"water_to_be_used_liters": 0,
				"blocks": [],
				"machinery": [
					frappe._dict(
						{
							"asset": "AST-TRACTOR-001",
							"asset_name": "Tractor T1",
							"planned_hours": 2,
							"return_type": "Non Returnable",
							"paired_implement": "IMP-ROTAVATOR-001",
						}
					)
				],
				"implements": [],
				"hand_tools": [],
				"other_tools": [],
				"inputs": [],
			}
		)
		doc.db_set = lambda *args, **kwargs: None
		return doc

	def test_create_from_schedule_copies_paired_implement_to_equipment(self):
		schedule = self._make_schedule_doc()
		exec_doc = DummyExecDoc()
		fake_db = DummyDb()

		def fake_get_doc(arg1, arg2=None):
			if isinstance(arg1, dict):
				return exec_doc
			if arg1 == "Crop Plan Schedule":
				return schedule
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		def fake_insert_with_retry(doc, max_retries=3):
			doc.name = "FTE-TEST-0001"
			return doc.name

		with patch.object(fte_module.frappe, "db", fake_db), patch.object(
			fte_module.frappe, "get_doc", side_effect=fake_get_doc
		), patch.object(fte_module, "_insert_with_retry", side_effect=fake_insert_with_retry), patch.object(
			fte_module, "now_datetime", return_value="2026-04-07 08:00:00"
		):
			fte_name = fte_module.create_from_schedule(schedule.name)

		self.assertEqual(fte_name, "FTE-TEST-0001")
		self.assertEqual(exec_doc.equipment[0].asset, "AST-TRACTOR-001")
		self.assertEqual(exec_doc.equipment[0].paired_implement, "IMP-ROTAVATOR-001")

	def test_create_from_on_demand_activity_copies_paired_implement_to_equipment(self):
		activity = self._make_on_demand_doc()
		exec_doc = DummyExecDoc()

		def fake_get_doc(arg1, arg2=None):
			if isinstance(arg1, dict):
				return exec_doc
			if arg1 == "On Demand Activity":
				return activity
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		def fake_insert_with_retry(doc, max_retries=3):
			doc.name = "FTE-TEST-0002"
			return doc.name

		with patch.object(fte_module.frappe, "get_doc", side_effect=fake_get_doc), patch.object(
			fte_module, "_insert_with_retry", side_effect=fake_insert_with_retry
		), patch.object(fte_module, "now_datetime", return_value="2026-04-07 08:00:00"):
			fte_name = fte_module.create_from_on_demand_activity(activity.name)

		self.assertEqual(fte_name, "FTE-TEST-0002")
		self.assertEqual(exec_doc.equipment[0].asset, "AST-TRACTOR-001")
		self.assertEqual(exec_doc.equipment[0].paired_implement, "IMP-ROTAVATOR-001")

	def test_sync_day_equipment_to_parent_fte_appends_new_asset(self):
		"""Ad-hoc day equipment rows should be copied to parent FTE so new days inherit them."""
		appended: list = []
		save_called = {"n": 0}

		class FakeFTE:
			def __init__(self):
				self.equipment = [frappe._dict(asset="AST-OLD", asset_name="Old Tool")]

			def append(self, fieldname, row):
				appended.append((fieldname, dict(row)))

			def save(self, ignore_permissions=True):
				save_called["n"] += 1

		fte = FakeFTE()
		day = frappe._dict(
			equipment=[
				frappe._dict(
					asset="AST-OLD",
					asset_name="Old Tool",
					planned_hours=1,
					actual_hours=1,
					return_type="Non Returnable",
				),
				frappe._dict(
					asset="AST-SHOVEL-001",
					asset_name="Shovel 1",
					planned_hours=0,
					actual_hours=2,
					return_type="Non Returnable",
				),
			]
		)

		def fake_get_doc(doctype, name):
			if doctype == "Farm Task Execution" and name == "FTE-SYNC-TEST":
				return fte
			raise AssertionError(f"Unexpected get_doc: {doctype} {name}")

		with patch.object(fte_module.frappe, "get_doc", side_effect=fake_get_doc), patch.object(
			fte_module, "_asset_eligible_for_execution_field_warehouse", return_value=True
		):
			fte_module._sync_day_equipment_to_parent_fte("FTE-SYNC-TEST", day)

		self.assertEqual(len(appended), 1)
		self.assertEqual(appended[0][0], "equipment")
		self.assertEqual(appended[0][1]["asset"], "AST-SHOVEL-001")
		self.assertEqual(appended[0][1]["asset_name"], "Shovel 1")
		self.assertEqual(save_called["n"], 1)

	def test_copy_fte_child_to_day_copies_all_equipment_rows(self):
		fte = frappe._dict(
			remark="",
			actual_spray_water_liters=0,
			actual_irrigation_water_liters=0,
			labour_attendance=[],
			inputs=[],
			equipment=[
				frappe._dict(asset="A1", asset_name="Tool 1", planned_hours=1, actual_hours=0, return_type="Non Returnable"),
				frappe._dict(asset="A2", asset_name="Tool 2", planned_hours=0, actual_hours=0, return_type="Non Returnable"),
			],
			progress_images=[],
		)
		day_doc = frappe._dict(
			remark=None,
			actual_spray_water_liters=None,
			actual_irrigation_water_liters=None,
			labour=[],
			inputs=[],
			equipment=[],
			progress_images=[],
		)

		def append(field, row):
			(day_doc.setdefault(field, [])).append(frappe._dict(row))

		day_doc.append = append
		fte_module._copy_fte_child_to_day(fte, day_doc)
		self.assertEqual(len(day_doc.equipment), 2)
		self.assertEqual(day_doc.equipment[0].asset, "A1")
		self.assertEqual(day_doc.equipment[1].asset, "A2")

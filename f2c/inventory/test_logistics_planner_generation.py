from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.inventory import logistics_planner_api as planner_module


class DummyBatchDoc:
	def __init__(self, values=None):
		self.doctype = "Logistics Batch"
		self.name = (values or {}).get("name")
		self.inserted = False
		self.saved = False
		self.planner_rows = []
		if values:
			for key, value in values.items():
				setattr(self, key, value)

	def update(self, values):
		for key, value in values.items():
			setattr(self, key, value)

	def append(self, fieldname, value):
		getattr(self, fieldname).append(frappe._dict(value))

	def set(self, fieldname, value):
		setattr(self, fieldname, value)

	def insert(self, ignore_permissions=True):
		self.inserted = True
		if not self.name:
			self.name = "LB-TEST-0001"
		return self

	def save(self, ignore_permissions=True):
		self.saved = True
		return self


class TestLogisticsPlannerGeneration(FrappeTestCase):
	def _make_schedule_doc(self):
		doc = frappe._dict(
			{
				"doctype": "Crop Plan Schedule",
				"name": "CPS-TEST-0001",
				"field": "FIELD-001",
				"machinery": [
					frappe._dict(
						{
							"asset": "AST-TRACTOR-001",
							"asset_name": "Tractor T1",
							"paired_implement": "IMP-ROTAVATOR-001",
							"planned_hours": 2,
						}
					)
				],
				"implements": [],
				"hand_tools": [frappe._dict({"asset": "AST-HAND-001", "asset_name": "Hoe"})],
				"other_tools": [frappe._dict({"asset": "AST-OTHER-001", "asset_name": "Trailer"})],
				"inputs": [frappe._dict({"item": "ITEM-001", "total_quantity_to_use": 5})],
			}
		)
		doc._get_target_warehouse_for_field = lambda field: "FIELD-WH-001"
		doc._get_cluster_for_field = lambda field: "CLUSTER-001"
		doc._get_cluster_warehouse_for_field = lambda field: "CLUSTER-WH-001"
		doc._get_warehouse_from_location = lambda location: {
			"LOC-TRACTOR": "TRACTOR-WH-001",
			"LOC-HAND": "HAND-WH-001",
			"LOC-OTHER": "OTHER-WH-001",
		}.get(location)
		return doc

	def _make_on_demand_doc(self):
		doc = self._make_schedule_doc()
		doc.doctype = "On Demand Activity"
		doc.name = "ODA-TEST-0001"
		return doc

	def test_create_or_refresh_draft_logistics_batch_builds_rows_for_schedule(self):
		source_doc = self._make_schedule_doc()
		created_batch = DummyBatchDoc()

		def fake_get_doc(arg1, arg2=None):
			if isinstance(arg1, dict):
				created_batch.update(arg1)
				return created_batch
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		def fake_get_value(doctype, filters=None, fieldname=None, as_dict=False):
			if doctype == "Logistics Batch":
				return None
			if doctype == "Machinery":
				return {
					"name": "MACH-001",
					"machinery_type": "Tractor",
					"current_implement": "IMP-CURRENT-001",
				}
			if doctype == "Implement" and filters == "IMP-CURRENT-001":
				return "Current Rotavator"
			if doctype == "Asset" and filters == "AST-TRACTOR-001":
				return "LOC-TRACTOR"
			if doctype == "Asset" and filters == "AST-HAND-001":
				return "LOC-HAND"
			if doctype == "Asset" and filters == "AST-OTHER-001":
				return "LOC-OTHER"
			raise AssertionError(f"Unexpected get_value call: {doctype}, {filters}, {fieldname}, {as_dict}")

		with patch.object(planner_module.frappe, "get_doc", side_effect=fake_get_doc), patch.object(
			planner_module.frappe.db, "get_value", side_effect=fake_get_value
		):
			batch_name = planner_module.create_or_refresh_draft_logistics_batch(source_doc)

		self.assertEqual(batch_name, "LB-TEST-0001")
		self.assertTrue(created_batch.inserted)
		self.assertEqual(created_batch.source_doctype, "Crop Plan Schedule")
		self.assertEqual(created_batch.source_name, "CPS-TEST-0001")
		self.assertEqual(created_batch.batch_type, "Crop Plan Schedule")
		self.assertEqual(created_batch.planning_status, "Draft")
		self.assertEqual(created_batch.target_warehouse, "FIELD-WH-001")

		rows_by_category = {}
		for row in created_batch.planner_rows:
			rows_by_category.setdefault(row.transfer_category, []).append(row)

		self.assertIn("machinery", rows_by_category)
		self.assertIn("stocks", rows_by_category)
		self.assertIn("hand_tools", rows_by_category)
		self.assertIn("other_tools", rows_by_category)
		self.assertEqual(rows_by_category["machinery"][0].paired_implement, "IMP-ROTAVATOR-001")
		self.assertEqual(rows_by_category["machinery"][0].current_implement, "IMP-CURRENT-001")
		self.assertEqual(rows_by_category["machinery"][0].source_warehouse, "TRACTOR-WH-001")

	def test_create_or_refresh_draft_logistics_batch_replaces_rows_on_existing_draft(self):
		source_doc = self._make_on_demand_doc()
		existing_batch = DummyBatchDoc({"name": "LB-EXIST-0001"})
		existing_batch.planner_rows = [frappe._dict({"transfer_category": "legacy"})]

		def fake_get_doc(arg1, arg2=None):
			if arg1 == "Logistics Batch":
				return existing_batch
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		def fake_get_value(doctype, filters=None, fieldname=None, as_dict=False):
			if doctype == "Logistics Batch":
				return "LB-EXIST-0001"
			if doctype == "Machinery":
				return {
					"name": "MACH-001",
					"machinery_type": "Tractor",
					"current_implement": None,
				}
			if doctype == "Asset":
				return None
			raise AssertionError(f"Unexpected get_value call: {doctype}, {filters}, {fieldname}, {as_dict}")

		with patch.object(planner_module.frappe, "get_doc", side_effect=fake_get_doc), patch.object(
			planner_module.frappe.db, "get_value", side_effect=fake_get_value
		):
			batch_name = planner_module.create_or_refresh_draft_logistics_batch(source_doc)

		self.assertEqual(batch_name, "LB-EXIST-0001")
		self.assertTrue(existing_batch.saved)
		self.assertEqual(existing_batch.source_doctype, "On Demand Activity")
		self.assertNotEqual(existing_batch.planner_rows[0].transfer_category, "legacy")

	def test_create_or_refresh_draft_logistics_batch_groups_non_machinery_rows_by_route(self):
		source_doc = self._make_schedule_doc()
		source_doc.hand_tools = [frappe._dict({"asset": "AST-HAND-CLUSTER-001", "asset_name": "Hoe"})]
		source_doc.other_tools = [frappe._dict({"asset": "AST-OTHER-CLUSTER-001", "asset_name": "Trailer"})]
		created_batch = DummyBatchDoc()

		def fake_get_doc(arg1, arg2=None):
			if isinstance(arg1, dict):
				created_batch.update(arg1)
				return created_batch
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		def fake_get_value(doctype, filters=None, fieldname=None, as_dict=False):
			if doctype == "Logistics Batch":
				return None
			if doctype == "Machinery":
				return {
					"name": "MACH-001",
					"machinery_type": "Tractor",
					"current_implement": "IMP-CURRENT-001",
				}
			if doctype == "Implement" and filters == "IMP-CURRENT-001":
				return "Current Rotavator"
			if doctype == "Asset" and filters == "AST-TRACTOR-001":
				return "LOC-TRACTOR"
			if doctype == "Asset" and filters == "AST-HAND-CLUSTER-001":
				return "LOC-CLUSTER"
			if doctype == "Asset" and filters == "AST-OTHER-CLUSTER-001":
				return "LOC-CLUSTER"
			raise AssertionError(f"Unexpected get_value call: {doctype}, {filters}, {fieldname}, {as_dict}")

		source_doc._get_warehouse_from_location = lambda location: {
			"LOC-TRACTOR": "TRACTOR-WH-001",
			"LOC-CLUSTER": "CLUSTER-WH-001",
		}.get(location)

		with patch.object(planner_module.frappe, "get_doc", side_effect=fake_get_doc), patch.object(
			planner_module.frappe.db, "get_value", side_effect=fake_get_value
		):
			planner_module.create_or_refresh_draft_logistics_batch(source_doc)

		non_machinery_rows = [
			row for row in created_batch.planner_rows if row.transfer_category in ("stocks", "hand_tools", "other_tools")
		]
		self.assertEqual(len(non_machinery_rows), 3)
		self.assertEqual(
			{row.group_key for row in non_machinery_rows},
			{"non_machinery::CLUSTER-WH-001::FIELD-WH-001"},
		)
		self.assertEqual(created_batch.planner_rows[0].group_key, "machinery::AST-TRACTOR-001")

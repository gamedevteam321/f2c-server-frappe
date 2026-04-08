from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.inventory import logistics_planner_api as planner_module


class DummyBatchDoc:
	def __init__(self):
		self.doctype = "Logistics Batch"
		self.name = "LB-TEST-0001"
		self.planning_status = "Draft"
		self.planner_rows = []
		self.saved = False

	def append(self, fieldname, value):
		getattr(self, fieldname).append(frappe._dict(value))

	def save(self, ignore_permissions=True):
		self.saved = True
		return self


class DummyPlannerRow:
	def __init__(self, values):
		self._values = dict(values)

	def get(self, key, default=None):
		return self._values.get(key, default)

	def set(self, key, value):
		self._values[key] = value

	def as_dict(self):
		return dict(self._values)

	def __getattr__(self, item):
		try:
			return self._values[item]
		except KeyError as error:
			raise AttributeError(item) from error


class TestLogisticsPlannerMutations(FrappeTestCase):
	def test_add_manual_logistics_batch_row_appends_manual_action(self):
		batch_doc = DummyBatchDoc()

		with patch.object(planner_module.frappe, "get_doc", return_value=batch_doc):
			row = planner_module.add_manual_logistics_batch_row(
				batch_doc.name,
				{
					"transfer_category": "other_tools",
					"asset": "AST-OTHER-009",
					"source_warehouse": "CLUSTER-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"qty": 1,
				},
			)

		self.assertEqual(row["transfer_category"], "other_tools")
		self.assertEqual(row["action_type"], "manual_add")
		self.assertEqual(batch_doc.planner_rows[0].asset, "AST-OTHER-009")
		self.assertTrue(batch_doc.saved)

	def test_request_implement_recall_appends_recall_row(self):
		batch_doc = DummyBatchDoc()

		with patch.object(planner_module.frappe, "get_doc", return_value=batch_doc):
			row = planner_module.request_implement_recall(
				batch_doc.name,
				implement="IMP-ROTAVATOR-001",
				from_asset="AST-TRACTOR-OLD",
				source_warehouse="FIELD-WH-001",
				target_warehouse="CLUSTER-WH-001",
			)

		self.assertEqual(row["transfer_category"], "implement_recall")
		self.assertEqual(row["action_type"], "recall_to_cluster")
		self.assertEqual(row["paired_implement"], "IMP-ROTAVATOR-001")
		self.assertEqual(row["primary_asset"], "AST-TRACTOR-OLD")
		self.assertEqual(batch_doc.planner_rows[0].target_warehouse, "CLUSTER-WH-001")
		self.assertTrue(batch_doc.saved)

	def test_add_replacement_logistics_batch_row_appends_replacement_row(self):
		batch_doc = DummyBatchDoc()

		with patch.object(planner_module.frappe, "get_doc", return_value=batch_doc):
			row = planner_module.add_replacement_logistics_batch_row(
				batch_doc.name,
				asset="AST-TRACTOR-NEW",
				paired_implement="IMP-ROTAVATOR-001",
				source_warehouse="CLUSTER-WH-001",
				target_warehouse="FIELD-WH-001",
			)

		self.assertEqual(row["transfer_category"], "replacement")
		self.assertEqual(row["action_type"], "replace")
		self.assertEqual(row["asset"], "AST-TRACTOR-NEW")
		self.assertEqual(row["paired_implement"], "IMP-ROTAVATOR-001")
		self.assertTrue(batch_doc.saved)

	def test_update_logistics_batch_planner_row_recomputes_group_key_for_non_machinery(self):
		batch_doc = DummyBatchDoc()
		batch_doc.planner_rows = [
			DummyPlannerRow(
				{
					"name": "ROW-TOOLS-1",
					"transfer_category": "hand_tools",
					"source_warehouse": "CLUSTER-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"group_key": "non_machinery::CLUSTER-WH-001::FIELD-WH-001",
					"asset": "AST-HAND-001",
				}
			)
		]

		with patch.object(planner_module.frappe, "get_doc", return_value=batch_doc):
			row = planner_module.update_logistics_batch_planner_row(
				batch_doc.name,
				"ROW-TOOLS-1",
				{
					"source_warehouse": "ALT-WH-001",
					"target_warehouse": "FIELD-WH-002",
				},
			)

		self.assertEqual(row["source_warehouse"], "ALT-WH-001")
		self.assertEqual(row["target_warehouse"], "FIELD-WH-002")
		self.assertEqual(row["group_key"], "non_machinery::ALT-WH-001::FIELD-WH-002")
		self.assertTrue(batch_doc.saved)

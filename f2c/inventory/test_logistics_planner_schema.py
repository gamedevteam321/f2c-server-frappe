import json
from pathlib import Path

from frappe.tests.utils import FrappeTestCase


APP_ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = APP_ROOT / "inventory" / "doctype"


def load_doctype_json(doctype_folder: str, filename: str | None = None) -> dict:
	doctype_file = filename or doctype_folder
	path = DOCTYPE_ROOT / doctype_folder / f"{doctype_file}.json"
	with path.open() as handle:
		return json.load(handle)


def field_map(doc: dict) -> dict:
	return {field["fieldname"]: field for field in doc.get("fields", []) if field.get("fieldname")}


class TestLogisticsPlannerSchema(FrappeTestCase):
	def test_logistics_batch_doctype_has_planner_fields(self):
		doc = load_doctype_json("logistics_batch")
		fields = field_map(doc)

		self.assertEqual(doc["name"], "Logistics Batch")
		self.assertIn("source_doctype", fields)
		self.assertIn("source_name", fields)
		self.assertIn("planning_status", fields)
		self.assertIn("field", fields)
		self.assertIn("cluster", fields)
		self.assertIn("farm", fields)
		self.assertIn("target_warehouse", fields)
		self.assertIn("planner_rows", fields)
		self.assertEqual(fields["planner_rows"]["fieldtype"], "Table")
		self.assertEqual(fields["planner_rows"]["options"], "Logistics Batch Planner Row")

	def test_logistics_batch_planner_row_has_transfer_and_pairing_fields(self):
		doc = load_doctype_json("logistics_batch_planner_row")
		fields = field_map(doc)

		self.assertEqual(doc["name"], "Logistics Batch Planner Row")
		self.assertIn("transfer_category", fields)
		self.assertIn("source_warehouse", fields)
		self.assertIn("target_warehouse", fields)
		self.assertIn("asset", fields)
		self.assertIn("stock_item", fields)
		self.assertIn("qty", fields)
		self.assertIn("paired_implement", fields)
		self.assertIn("current_implement", fields)
		self.assertIn("assigned_transport_asset", fields)
		self.assertIn("assigned_driver", fields)
		self.assertIn("action_type", fields)
		self.assertIn("group_key", fields)

	def test_ltt_and_asset_rows_have_batch_planner_link_fields(self):
		ltt_doc = load_doctype_json("logistics_transfer_ticket")
		ltt_fields = field_map(ltt_doc)

		self.assertIn("logistics_batch", ltt_fields)
		self.assertIn("source_doctype", ltt_fields)
		self.assertIn("source_name", ltt_fields)
		self.assertIn("transfer_category", ltt_fields)
		self.assertIn("planner_row_ref", ltt_fields)

		asset_doc = load_doctype_json("logistics_transfer_asset")
		asset_fields = field_map(asset_doc)

		self.assertIn("paired_implement", asset_fields)
		self.assertIn("current_implement", asset_fields)
		self.assertIn("action_type", asset_fields)

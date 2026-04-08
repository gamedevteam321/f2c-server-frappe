from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.inventory import logistics_planner_api as planner_module


class DummyTicketDoc:
	def __init__(self, name, assets=None, stock_items=None):
		self.doctype = "Logistics Transfer Ticket"
		self.name = name
		self.asset_items = [frappe._dict(item) for item in (assets or [])]
		self.stock_items = [frappe._dict(item) for item in (stock_items or [])]
		self.saved = False

	def save(self, ignore_permissions=True):
		self.saved = True
		return self


class DummyBatchDoc:
	def __init__(self):
		self.doctype = "Logistics Batch"
		self.name = "LB-TEST-0001"
		self.source_doctype = "Crop Plan Schedule"
		self.source_name = "CPS-TEST-0001"
		self.planning_status = "Draft"
		self.planner_rows = [
			frappe._dict(
				{
					"name": "ROW-MACH-1",
					"transfer_category": "machinery",
					"source_warehouse": "TRACTOR-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"asset": "AST-TRACTOR-001",
					"qty": 1,
					"paired_implement": "IMP-ROTAVATOR-001",
					"current_implement": "IMP-CURRENT-001",
					"action_type": "move",
					"group_key": "machinery::AST-TRACTOR-001",
					"planned_pickup_on": "2026-05-01 08:00:00",
					"planned_drop_off_on": "2026-05-01 18:00:00",
				}
			),
			frappe._dict(
				{
					"name": "ROW-STOCK-1",
					"transfer_category": "stocks",
					"source_warehouse": "CLUSTER-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"stock_item": "ITEM-001",
					"qty": 5,
					"action_type": "move",
					"group_key": "non_machinery::CLUSTER-WH-001::FIELD-WH-001",
				}
			),
			frappe._dict(
				{
					"name": "ROW-HAND-1",
					"transfer_category": "hand_tools",
					"source_warehouse": "HAND-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"asset": "AST-HAND-001",
					"qty": 1,
					"action_type": "move",
					"group_key": "non_machinery::HAND-WH-001::FIELD-WH-001",
				}
			),
		]
		self.saved = False

	def save(self, ignore_permissions=True):
		self.saved = True
		return self


class TestLogisticsPlannerApproval(FrappeTestCase):
	def test_approve_logistics_batch_creates_child_ltts_and_stamps_metadata(self):
		batch_doc = DummyBatchDoc()
		created_tickets = {}
		create_calls = []

		def fake_create_ticket(**kwargs):
			ticket_name = f"LTT-TEST-{len(create_calls) + 1:04d}"
			create_calls.append(kwargs)
			created_tickets[ticket_name] = DummyTicketDoc(
				ticket_name,
				assets=kwargs.get("assets"),
				stock_items=kwargs.get("stock_items"),
			)
			return {"ticket": ticket_name}

		def fake_get_doc(arg1, arg2=None):
			if arg1 == "Logistics Batch":
				return batch_doc
			if arg1 == "Logistics Transfer Ticket":
				return created_tickets[arg2]
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		with patch.object(planner_module, "create_logistics_transfer_ticket", side_effect=fake_create_ticket), patch.object(
			planner_module.frappe, "get_doc", side_effect=fake_get_doc
		), patch.object(planner_module, "now_datetime", return_value="2026-04-08 09:00:00"):
			ticket_names = planner_module.approve_logistics_batch(batch_doc.name)

		self.assertEqual(ticket_names, ["LTT-TEST-0001", "LTT-TEST-0002", "LTT-TEST-0003"])
		self.assertEqual(len(create_calls), 3)

		machinery_call = create_calls[0]
		self.assertEqual(machinery_call["from_warehouse"], "TRACTOR-WH-001")
		self.assertEqual(machinery_call["to_warehouse"], "FIELD-WH-001")
		self.assertEqual(machinery_call["assets"][0]["asset"], "AST-TRACTOR-001")
		self.assertEqual(machinery_call.get("planned_pickup_on"), "2026-05-01 08:00:00")
		self.assertEqual(machinery_call.get("planned_drop_off_on"), "2026-05-01 18:00:00")

		stock_call = create_calls[1]
		self.assertEqual(stock_call["stock_items"][0]["item_code"], "ITEM-001")

		machinery_ticket = created_tickets["LTT-TEST-0001"]
		self.assertEqual(machinery_ticket.logistics_batch, "LB-TEST-0001")
		self.assertEqual(machinery_ticket.source_doctype, "Crop Plan Schedule")
		self.assertEqual(machinery_ticket.source_name, "CPS-TEST-0001")
		self.assertEqual(machinery_ticket.transfer_category, "machinery")
		self.assertEqual(machinery_ticket.planner_row_ref, "ROW-MACH-1")
		self.assertEqual(machinery_ticket.asset_items[0].paired_implement, "IMP-ROTAVATOR-001")
		self.assertEqual(machinery_ticket.asset_items[0].current_implement, "IMP-CURRENT-001")
		self.assertEqual(machinery_ticket.asset_items[0].action_type, "move")
		self.assertTrue(machinery_ticket.saved)

		self.assertEqual(batch_doc.planning_status, "Approved")
		self.assertEqual(batch_doc.approved_on, "2026-04-08 09:00:00")
		self.assertTrue(batch_doc.saved)

	def test_approve_logistics_batch_skips_rows_already_in_target_warehouse(self):
		batch_doc = DummyBatchDoc()
		batch_doc.planner_rows.append(
			frappe._dict(
				{
					"name": "ROW-STOCK-NO-MOVE",
					"transfer_category": "stocks",
					"source_warehouse": "FIELD-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"stock_item": "ITEM-READY",
					"qty": 2,
					"action_type": "move",
				}
			)
		)
		batch_doc.planner_rows.append(
			frappe._dict(
				{
					"name": "ROW-ASSET-NO-MOVE",
					"transfer_category": "other_tools",
					"source_warehouse": "FIELD-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"asset": "AST-READY-001",
					"qty": 1,
					"action_type": "move",
				}
			)
		)
		created_tickets = {}
		create_calls = []

		def fake_create_ticket(**kwargs):
			ticket_name = f"LTT-TEST-{len(create_calls) + 1:04d}"
			create_calls.append(kwargs)
			created_tickets[ticket_name] = DummyTicketDoc(
				ticket_name,
				assets=kwargs.get("assets"),
				stock_items=kwargs.get("stock_items"),
			)
			return {"ticket": ticket_name}

		def fake_get_doc(arg1, arg2=None):
			if arg1 == "Logistics Batch":
				return batch_doc
			if arg1 == "Logistics Transfer Ticket":
				return created_tickets[arg2]
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		with patch.object(planner_module, "create_logistics_transfer_ticket", side_effect=fake_create_ticket), patch.object(
			planner_module.frappe, "get_doc", side_effect=fake_get_doc
		), patch.object(planner_module, "now_datetime", return_value="2026-04-08 09:00:00"):
			ticket_names = planner_module.approve_logistics_batch(batch_doc.name)

		self.assertEqual(ticket_names, ["LTT-TEST-0001", "LTT-TEST-0002", "LTT-TEST-0003"])
		self.assertEqual(len(create_calls), 3)
		self.assertFalse(any(call.get("stock_items", [{}])[0].get("item_code") == "ITEM-READY" for call in create_calls if call.get("stock_items")))
		self.assertFalse(any(call.get("assets", [{}])[0].get("asset") == "AST-READY-001" for call in create_calls if call.get("assets")))
		self.assertEqual(batch_doc.planning_status, "Approved")
		self.assertTrue(batch_doc.saved)

	def test_approve_logistics_batch_groups_non_machinery_rows_by_route(self):
		batch_doc = DummyBatchDoc()
		batch_doc.planner_rows[1].source_warehouse = "CLUSTER-WH-001"
		batch_doc.planner_rows[1].group_key = "non_machinery::CLUSTER-WH-001::FIELD-WH-001"
		batch_doc.planner_rows[2].source_warehouse = "CLUSTER-WH-001"
		batch_doc.planner_rows[2].asset = "AST-HAND-CLUSTER-001"
		batch_doc.planner_rows[2].group_key = "non_machinery::CLUSTER-WH-001::FIELD-WH-001"
		batch_doc.planner_rows.append(
			frappe._dict(
				{
					"name": "ROW-OTHER-1",
					"transfer_category": "other_tools",
					"source_warehouse": "CLUSTER-WH-001",
					"target_warehouse": "FIELD-WH-001",
					"asset": "AST-OTHER-CLUSTER-001",
					"qty": 1,
					"action_type": "move",
					"group_key": "non_machinery::CLUSTER-WH-001::FIELD-WH-001",
				}
			)
		)
		created_tickets = {}
		create_calls = []

		def fake_create_ticket(**kwargs):
			ticket_name = f"LTT-TEST-{len(create_calls) + 1:04d}"
			create_calls.append(kwargs)
			created_tickets[ticket_name] = DummyTicketDoc(
				ticket_name,
				assets=kwargs.get("assets"),
				stock_items=kwargs.get("stock_items"),
			)
			return {"ticket": ticket_name}

		def fake_get_doc(arg1, arg2=None):
			if arg1 == "Logistics Batch":
				return batch_doc
			if arg1 == "Logistics Transfer Ticket":
				return created_tickets[arg2]
			raise AssertionError(f"Unexpected get_doc call: {arg1}, {arg2}")

		with patch.object(planner_module, "create_logistics_transfer_ticket", side_effect=fake_create_ticket), patch.object(
			planner_module.frappe, "get_doc", side_effect=fake_get_doc
		), patch.object(planner_module, "now_datetime", return_value="2026-04-08 09:00:00"):
			ticket_names = planner_module.approve_logistics_batch(batch_doc.name)

		self.assertEqual(ticket_names, ["LTT-TEST-0001", "LTT-TEST-0002"])
		self.assertEqual(len(create_calls), 2)
		self.assertEqual(create_calls[0]["assets"], [{"asset": "AST-TRACTOR-001", "qty": 1.0}])

		non_machinery_call = create_calls[1]
		self.assertEqual(non_machinery_call["from_warehouse"], "CLUSTER-WH-001")
		self.assertEqual(non_machinery_call["to_warehouse"], "FIELD-WH-001")
		self.assertEqual(non_machinery_call["stock_items"], [{"item_code": "ITEM-001", "qty": 5.0}])
		self.assertEqual(
			non_machinery_call["assets"],
			[
				{"asset": "AST-HAND-CLUSTER-001", "qty": 1.0},
				{"asset": "AST-OTHER-CLUSTER-001", "qty": 1.0},
			],
		)

		mixed_ticket = created_tickets["LTT-TEST-0002"]
		self.assertEqual(mixed_ticket.logistics_batch, "LB-TEST-0001")
		self.assertEqual(mixed_ticket.source_doctype, "Crop Plan Schedule")
		self.assertEqual(mixed_ticket.source_name, "CPS-TEST-0001")
		self.assertIsNone(getattr(mixed_ticket, "planner_row_ref", None))

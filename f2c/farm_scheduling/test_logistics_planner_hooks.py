from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from f2c.farm_scheduling.doctype.crop_plan_schedule import crop_plan_schedule as cps_module
from f2c.farm_scheduling.doctype.on_demand_activity import on_demand_activity as oda_module


class TestLogisticsPlannerHooks(FrappeTestCase):
	def _make_scheduled_doc(self, doctype: str, name: str):
		doc = frappe._dict(
			{
				"doctype": doctype,
				"name": name,
				"status": "Scheduled",
				"field": "FIELD-001",
			}
		)
		doc._create_equipment_transfer_tickets = lambda: (_ for _ in ()).throw(
			AssertionError("direct equipment transfer creation should not run")
		)
		doc._create_input_transfer_tickets = lambda: (_ for _ in ()).throw(
			AssertionError("direct input transfer creation should not run")
		)
		return doc

	def test_schedule_after_insert_uses_draft_batch_generation(self):
		schedule = self._make_scheduled_doc("Crop Plan Schedule", "CPS-TEST-0001")

		with patch.object(cps_module, "create_or_refresh_draft_logistics_batch", return_value="LB-TEST-0001") as create_batch:
			cps_module.CropPlanSchedule.after_insert(schedule)

		create_batch.assert_called_once_with(schedule)

	def test_schedule_manual_trigger_uses_draft_batch_generation(self):
		schedule = self._make_scheduled_doc("Crop Plan Schedule", "CPS-TEST-0001")

		with patch.object(cps_module.frappe, "get_doc", return_value=schedule), patch.object(
			cps_module, "create_or_refresh_draft_logistics_batch", return_value="LB-TEST-0001"
		) as create_batch:
			result = cps_module.create_transfer_tickets_for_schedule(schedule.name)

		create_batch.assert_called_once_with(schedule)
		self.assertEqual(result, {"success": True, "batch": "LB-TEST-0001"})

	def test_on_demand_after_insert_uses_draft_batch_generation(self):
		activity = self._make_scheduled_doc("On Demand Activity", "ODA-TEST-0001")

		with patch.object(oda_module, "create_or_refresh_draft_logistics_batch", return_value="LB-TEST-0002") as create_batch:
			oda_module.OnDemandActivity.after_insert(activity)

		create_batch.assert_called_once_with(activity)

	def test_on_demand_manual_trigger_uses_draft_batch_generation(self):
		activity = self._make_scheduled_doc("On Demand Activity", "ODA-TEST-0001")

		with patch.object(oda_module.frappe, "get_doc", return_value=activity), patch.object(
			oda_module, "create_or_refresh_draft_logistics_batch", return_value="LB-TEST-0002"
		) as create_batch:
			result = oda_module.create_transfer_tickets_for_activity(activity.name)

		create_batch.assert_called_once_with(activity)
		self.assertEqual(result, {"success": True, "batch": "LB-TEST-0002"})

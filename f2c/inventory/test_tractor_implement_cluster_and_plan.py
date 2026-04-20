"""Tests for equipment geo level helpers and field tractor↔implement round-trip LTT planning."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase

from f2c.inventory.equipment_location_level import (
	assert_cluster_for_tractor_implement_link_change,
	location_warehouse_level_from_location_name,
)
from f2c.inventory.logistics_transfer_ticket_api import planned_pickup_drop_for_round_trip_leg1_immediate
from f2c.inventory.tractor_implement_ltt_plan import (
	implement_asset_ids_paired_to_tractors_on_schedule,
	implement_doc_names_paired_to_tractors_on_schedule,
	plan_field_tractor_implement_round_trip,
)


class TestRoundTripLeg1ImmediatePlannedTimes(FrappeTestCase):
	@patch("f2c.inventory.logistics_transfer_ticket_api.estimate_internal_ltt_travel_minutes", return_value=30)
	@patch("f2c.inventory.logistics_transfer_ticket_api.now_datetime")
	@patch("f2c.inventory.logistics_transfer_ticket_api._get_f2c_ltt_timing_settings")
	def test_leg1_immediate_anchors_pickup_to_now(self, mock_settings, mock_now, _mock_travel):
		mock_settings.return_value = {"ltt_schedule_planned_times_enabled": 1, "ltt_dropoff_buffer_minutes": 15}
		anchor = datetime(2026, 4, 17, 10, 0, 0)
		mock_now.return_value = anchor
		pt = planned_pickup_drop_for_round_trip_leg1_immediate("WH-FIELD", "WH-CLUSTER")
		self.assertIsNotNone(pt)
		assert pt is not None
		from frappe.utils import get_datetime

		pu = get_datetime(pt[0])
		do = get_datetime(pt[1])
		self.assertEqual(pu, anchor)
		self.assertIsNotNone(do)
		assert do is not None
		self.assertGreaterEqual((do - pu).total_seconds() / 60.0, 60.0)

	@patch("f2c.inventory.logistics_transfer_ticket_api.estimate_internal_ltt_travel_minutes", return_value=30)
	@patch("f2c.inventory.logistics_transfer_ticket_api.now_datetime")
	@patch("f2c.inventory.logistics_transfer_ticket_api._get_f2c_ltt_timing_settings")
	def test_leg1_immediate_still_computes_when_schedule_flag_off(self, mock_settings, mock_now, _mock_travel):
		mock_settings.return_value = {"ltt_schedule_planned_times_enabled": 0, "ltt_dropoff_buffer_minutes": 10}
		anchor = datetime(2026, 4, 17, 12, 0, 0)
		mock_now.return_value = anchor
		pt = planned_pickup_drop_for_round_trip_leg1_immediate("WH-A", "WH-B")
		self.assertIsNotNone(pt)
		assert pt is not None
		from frappe.utils import get_datetime

		self.assertEqual(get_datetime(pt[0]), anchor)


class TestMachineryPayloadsSkipPairedImplement(FrappeTestCase):
	@patch("f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity.OnDemandActivity._is_field_machinery_equipment_asset", return_value=False)
	@patch("f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity.OnDemandActivity._machinery_transport_vehicle_for_primary_asset", return_value=None)
	@patch("f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests")
	@patch(
		"f2c.inventory.tractor_implement_ltt_plan.implement_asset_ids_paired_to_tractors_on_schedule",
		return_value={"IM-ASSET"},
	)
	def test_oda_payloads_skip_standalone_paired_implement(
		self, _mock_paired_fn, mock_expand, _mock_tv, _mock_field
	):
		from f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity import OnDemandActivity

		mock_expand.side_effect = lambda primary, paired_implement=None: [{"asset": primary, "qty": 1}]
		inst = MagicMock()
		inst.get.side_effect = lambda key: {
			"machinery": [SimpleNamespace(asset="TR-ASSET")],
			"implements": [SimpleNamespace(asset="IM-ASSET")],
			"hand_tools": [],
			"other_tools": [],
		}[key]
		units = OnDemandActivity._machinery_transfer_unit_payloads(inst)
		primaries = [u[0][0]["asset"] for u in units]
		self.assertEqual(primaries, ["TR-ASSET"])


class TestPairedImplementAssetIds(FrappeTestCase):
	def test_implement_asset_ids_empty_when_no_paired(self):
		doc = SimpleNamespace(machinery=[SimpleNamespace(asset="TA", paired_implement="")])
		self.assertEqual(implement_asset_ids_paired_to_tractors_on_schedule(doc), set())

	@patch("frappe.db.exists", return_value=True)
	@patch("frappe.db.get_value", return_value="AS-ROT")
	def test_implement_asset_ids_collects_paired_rows(self, mock_gv, mock_exists):
		doc = SimpleNamespace(
			machinery=[
				SimpleNamespace(asset="TA", paired_implement="IMP-ROT"),
				SimpleNamespace(asset="TB", paired_implement=""),
			]
		)
		s = implement_asset_ids_paired_to_tractors_on_schedule(doc)
		self.assertEqual(s, {"AS-ROT"})

	@patch("frappe.db.get_value")
	def test_asset_ids_single_tractor_single_implement_child_without_paired_implement(self, mock_gv):
		"""Schedule uses implements child only (no paired_implement on machinery row)."""

		def gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict) and arg2.get("asset") == "TA":
				return "Tractor"
			return None

		mock_gv.side_effect = gv
		doc = SimpleNamespace(
			machinery=[SimpleNamespace(asset="TA", paired_implement="")],
			implements=[SimpleNamespace(asset="AS-ROT")],
		)
		self.assertEqual(implement_asset_ids_paired_to_tractors_on_schedule(doc), {"AS-ROT"})

	@patch("frappe.db.exists", return_value=True)
	@patch("frappe.db.get_value")
	def test_asset_ids_include_child_row_when_implement_doc_has_no_asset(self, mock_gv, mock_exists):
		"""Schedule implements child can carry the asset even if Implement.asset is unset."""

		def gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Implement" and arg2 == "IMP-R" and arg3 == "asset":
				return None
			if doctype == "Implement" and isinstance(arg2, dict) and arg2.get("asset") == "AS-R":
				return "IMP-R"
			return None

		mock_gv.side_effect = gv
		doc = SimpleNamespace(
			machinery=[SimpleNamespace(asset="TA", paired_implement="IMP-R")],
			implements=[SimpleNamespace(asset="AS-R")],
		)
		self.assertEqual(implement_doc_names_paired_to_tractors_on_schedule(doc), {"IMP-R"})
		self.assertEqual(implement_asset_ids_paired_to_tractors_on_schedule(doc), {"AS-R"})


class TestEquipmentLocationLevel(FrappeTestCase):
	def test_location_name_depth(self):
		self.assertEqual(location_warehouse_level_from_location_name("FarmA-ClusterB"), "cluster")
		self.assertEqual(location_warehouse_level_from_location_name("FarmA-ClusterB-FieldC"), "field")
		self.assertEqual(location_warehouse_level_from_location_name("SoloFarm"), "farm")

	@patch("f2c.inventory.equipment_location_level.location_level_for_equipment_doc")
	def test_cluster_assert_skipped_with_flag(self, mock_lvl):
		frappe.flags.skip_cluster_attachment_validation = True
		try:
			assert_cluster_for_tractor_implement_link_change("TR-1", "OLD", "NEW")
		finally:
			frappe.flags.skip_cluster_attachment_validation = False
		mock_lvl.assert_not_called()

	@patch("f2c.inventory.equipment_location_level.location_level_for_equipment_doc")
	def test_cluster_assert_raises_when_tractor_at_field(self, mock_lvl):
		def _lvl(doctype, name):
			if doctype == "Machinery" and name == "TR-1":
				return "field"
			if doctype == "Implement":
				return "cluster"
			return "cluster"

		mock_lvl.side_effect = _lvl
		with self.assertRaises(ValidationError):
			assert_cluster_for_tractor_implement_link_change("TR-1", "OLD", "NEW")


class TestTractorImplementRoundTripPlan(FrappeTestCase):
	def _doc(self, implements):
		return SimpleNamespace(
			field="F1",
			implements=implements,
			_get_cluster_warehouse_for_field=lambda *_: "WH-CLUSTER",
			_get_target_warehouse_for_field=lambda *_: "WH-FIELD",
			_get_source_warehouse_for_equipment_asset=lambda *_: "WH-FIELD",
		)

	@patch("f2c.inventory.equipment_location_level.location_warehouse_level_for_warehouse_name", return_value="field")
	@patch("f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests")
	@patch("f2c.inventory.logistics_transfer_ticket_api.self_transport_machinery_name_for_asset", return_value="TV-1")
	@patch("frappe.db.get_value")
	def test_plan_replace_two_legs(self, mock_gv, _mock_tv, mock_expand, _mock_lvl):
		def _gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict) and arg2.get("asset") == "AS-TR":
				if isinstance(arg3, (list, tuple)):
					return {"name": "M-TR", "machinery_type": "Tractor", "current_implement": "IMP-OLD"}
			if doctype == "Implement" and isinstance(arg2, dict) and arg2.get("asset") == "AS-REQ":
				return "IMP-REQ"
			return None

		mock_gv.side_effect = _gv

		def _expand(primary, paired=None):
			return [{"asset": primary, "qty": 1, **({"paired_implement": paired} if paired else {})}]

		mock_expand.side_effect = _expand
		doc = self._doc([SimpleNamespace(asset="AS-REQ")])
		rt = plan_field_tractor_implement_round_trip(doc, "AS-TR")
		self.assertIsNotNone(rt)
		assert rt is not None
		self.assertEqual(rt.standalone_implement_asset_to_skip, "AS-REQ")
		self.assertTrue(any(r.get("asset") == "AS-TR" for r in rt.leg1_assets))
		self.assertTrue(any(r.get("asset") == "AS-TR" for r in rt.leg2_assets))

	@patch("f2c.inventory.equipment_location_level.location_warehouse_level_for_warehouse_name", return_value="field")
	@patch("f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests")
	@patch("f2c.inventory.logistics_transfer_ticket_api.self_transport_machinery_name_for_asset", return_value="TV-1")
	@patch("frappe.db.exists", return_value=True)
	@patch("frappe.db.get_value")
	def test_plan_replace_from_machinery_paired_implement_empty_implements(self, mock_gv, mock_exists, _mock_tv, mock_expand, _mock_lvl):
		"""UI stores required implement on machinery row (paired_implement), not in implements child."""
		doc = SimpleNamespace(
			field="F1",
			machinery=[SimpleNamespace(asset="AS-TR", paired_implement="IMP-REQ")],
			implements=[],
			_get_cluster_warehouse_for_field=lambda *_: "WH-CLUSTER",
			_get_target_warehouse_for_field=lambda *_: "WH-FIELD",
			_get_source_warehouse_for_equipment_asset=lambda *_: "WH-FIELD",
		)

		def _gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict) and arg2.get("asset") == "AS-TR":
				if isinstance(arg3, (list, tuple)):
					return {"name": "M-TR", "machinery_type": "Tractor", "current_implement": "IMP-OLD"}
			if doctype == "Implement" and arg2 == "IMP-REQ" and arg3 == "asset":
				return "AS-REQ"
			return None

		mock_gv.side_effect = _gv

		def _expand(primary, paired=None):
			return [{"asset": primary, "qty": 1, **({"paired_implement": paired} if paired else {})}]

		mock_expand.side_effect = _expand
		rt = plan_field_tractor_implement_round_trip(doc, "AS-TR")
		self.assertIsNotNone(rt)
		assert rt is not None
		self.assertEqual(rt.standalone_implement_asset_to_skip, "AS-REQ")

	@patch("frappe.db.get_value")
	def test_plan_none_when_multiple_implements(self, mock_gv):
		mock_gv.return_value = {"name": "M-TR", "machinery_type": "Tractor", "current_implement": "IMP-OLD"}
		doc = self._doc([SimpleNamespace(asset="A1"), SimpleNamespace(asset="A2")])
		self.assertIsNone(plan_field_tractor_implement_round_trip(doc, "AS-TR"))

	@patch("frappe.db.get_value")
	def test_plan_none_when_same_implement(self, mock_gv):
		def gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict):
				return {"name": "M-TR", "machinery_type": "Tractor", "current_implement": "IMP-X"}
			if doctype == "Implement" and isinstance(arg2, dict) and arg2.get("asset") == "AS-X":
				return "IMP-X"
			return None

		mock_gv.side_effect = gv
		doc = self._doc([SimpleNamespace(asset="AS-X")])
		self.assertIsNone(plan_field_tractor_implement_round_trip(doc, "AS-TR"))

	@patch("f2c.inventory.equipment_location_level.location_level_for_equipment_doc", return_value="cluster")
	@patch("f2c.inventory.equipment_location_level.location_warehouse_level_for_warehouse_name", return_value="cluster")
	@patch("f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests")
	@patch("f2c.inventory.logistics_transfer_ticket_api.self_transport_machinery_name_for_asset", return_value="TV-1")
	@patch("frappe.db.get_value")
	def test_plan_replace_allows_non_field_when_implement_swap(
		self, mock_gv, _mock_tv, mock_expand, _mock_wh, _mock_doc
	):
		"""Swap/replace: do not block when Asset→warehouse is cluster/stock (ERP) but implements differ."""

		def _gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict) and arg2.get("asset") == "AS-TR":
				if isinstance(arg3, (list, tuple)):
					return {"name": "M-TR", "machinery_type": "Tractor", "current_implement": "IMP-OLD"}
			if doctype == "Implement" and isinstance(arg2, dict) and arg2.get("asset") == "AS-REQ":
				return "IMP-REQ"
			return None

		mock_gv.side_effect = _gv

		def _expand(primary, paired=None):
			return [{"asset": primary, "qty": 1, **({"paired_implement": paired} if paired else {})}]

		mock_expand.side_effect = _expand
		doc = self._doc([SimpleNamespace(asset="AS-REQ")])
		rt = plan_field_tractor_implement_round_trip(doc, "AS-TR")
		self.assertIsNotNone(rt)
		assert rt is not None
		self.assertEqual(rt.standalone_implement_asset_to_skip, "AS-REQ")

	@patch("f2c.inventory.equipment_location_level.location_level_for_equipment_doc", return_value="cluster")
	@patch("f2c.inventory.equipment_location_level.location_warehouse_level_for_warehouse_name", return_value="cluster")
	@patch("f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests")
	@patch("f2c.inventory.logistics_transfer_ticket_api.self_transport_machinery_name_for_asset", return_value="TV-1")
	@patch("frappe.db.get_value")
	def test_plan_attach_still_requires_field_staging(
		self, mock_gv, _mock_tv, mock_expand, _mock_wh, _mock_doc
	):
		"""Pure attach: tractor not at activity field warehouse (still at cluster) → no round-trip."""

		def _gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict) and arg2.get("asset") == "AS-TR":
				if isinstance(arg3, (list, tuple)):
					return {"name": "M-TR", "machinery_type": "Tractor", "current_implement": None}
			if doctype == "Implement" and isinstance(arg2, dict) and arg2.get("asset") == "AS-REQ":
				return "IMP-REQ"
			return None

		mock_gv.side_effect = _gv
		mock_expand.side_effect = lambda primary, paired=None: [
			{"asset": primary, "qty": 1, **({"paired_implement": paired} if paired else {})}
		]
		doc = SimpleNamespace(
			field="F1",
			implements=[SimpleNamespace(asset="AS-REQ")],
			_get_cluster_warehouse_for_field=lambda *_: "WH-CLUSTER",
			_get_target_warehouse_for_field=lambda *_: "WH-FIELD",
			_get_source_warehouse_for_equipment_asset=lambda *_: "WH-CLUSTER",
		)
		self.assertIsNone(plan_field_tractor_implement_round_trip(doc, "AS-TR"))

	@patch("f2c.inventory.equipment_location_level.location_level_for_equipment_doc", return_value="cluster")
	@patch("f2c.inventory.equipment_location_level.location_warehouse_level_for_warehouse_name", return_value="cluster")
	@patch("f2c.inventory.logistics_transfer_ticket_api.expand_machinery_transfer_asset_requests")
	@patch("f2c.inventory.logistics_transfer_ticket_api.self_transport_machinery_name_for_asset", return_value="TV-1")
	@patch("frappe.db.get_value")
	def test_plan_attach_when_tractor_at_activity_field_wh_even_if_geo_not_field(
		self, mock_gv, _mock_tv, mock_expand, _mock_wh, _mock_doc
	):
		"""Tractor asset at activity field WH (source==target) but geo says cluster → still plan round-trip."""

		def _gv(doctype, arg2, arg3=None, *args, **kwargs):
			if doctype == "Machinery" and isinstance(arg2, dict) and arg2.get("asset") == "AS-TR":
				if isinstance(arg3, (list, tuple)):
					return {"name": "M-TR", "machinery_type": "Tractor", "current_implement": None}
			if doctype == "Implement" and isinstance(arg2, dict) and arg2.get("asset") == "AS-REQ":
				return "IMP-REQ"
			return None

		mock_gv.side_effect = _gv
		mock_expand.side_effect = lambda primary, paired=None: [
			{"asset": primary, "qty": 1, **({"paired_implement": paired} if paired else {})}
		]
		doc = SimpleNamespace(
			field="F1",
			implements=[SimpleNamespace(asset="AS-REQ")],
			_get_cluster_warehouse_for_field=lambda *_: "WH-CLUSTER",
			_get_target_warehouse_for_field=lambda *_: "WH-FIELD",
			_get_source_warehouse_for_equipment_asset=lambda *_: "WH-FIELD",
		)
		rt = plan_field_tractor_implement_round_trip(doc, "AS-TR")
		self.assertIsNotNone(rt)
		assert rt is not None

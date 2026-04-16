"""Tests for LTT planned pickup/drop-off from schedule (travel estimate + settings)."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, get_datetime_str

import f2c.inventory.logistics_transfer_ticket_api as ltt_api

_DEFAULT_LTT_SETTINGS = {
	"ltt_schedule_planned_times_enabled": 1,
	"ltt_dropoff_buffer_minutes": 10,
	"ltt_travel_avg_speed_kph": 35.0,
	"ltt_travel_road_factor": 1.25,
	"ltt_travel_min_minutes": 5,
	"ltt_travel_max_minutes": 480,
	"ltt_travel_fallback_minutes": 60,
}


class TestLttSchedulePlannedTimes(FrappeTestCase):
	def test_haversine_paris_lyon_band(self):
		km = ltt_api._haversine_km(48.8566, 2.3522, 45.7640, 4.8357)
		self.assertGreater(km, 350)
		self.assertLess(km, 450)

	def test_travel_minutes_fallback_when_coords_missing(self):
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_travel_fallback_minutes": 77}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "_warehouse_lat_lng", return_value=None):
				self.assertEqual(ltt_api.estimate_internal_ltt_travel_minutes("WH-A", "WH-B"), 77)

	def test_travel_minutes_clamps_to_minimum_when_distance_tiny(self):
		settings = {
			**_DEFAULT_LTT_SETTINGS,
			"ltt_travel_min_minutes": 5,
			"ltt_travel_max_minutes": 480,
			"ltt_travel_avg_speed_kph": 35.0,
			"ltt_travel_road_factor": 1.0,
		}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "_warehouse_lat_lng", return_value=(48.0, 2.0)):
				self.assertEqual(ltt_api.estimate_internal_ltt_travel_minutes("A", "B"), 5)

	def test_travel_minutes_clamps_to_maximum(self):
		settings = {
			**_DEFAULT_LTT_SETTINGS,
			"ltt_travel_min_minutes": 5,
			"ltt_travel_max_minutes": 100,
			"ltt_travel_avg_speed_kph": 35.0,
			"ltt_travel_road_factor": 1.0,
		}

		def _coords(wh):
			if wh == "A":
				return (0.0, 0.0)
			return (80.0, 0.0)

		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "_warehouse_lat_lng", side_effect=_coords):
				self.assertEqual(ltt_api.estimate_internal_ltt_travel_minutes("A", "B"), 100)

	def test_planned_pickup_drop_master_switch_off(self):
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_schedule_planned_times_enabled": 0}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			self.assertIsNone(
				ltt_api.planned_pickup_drop_for_activity_start("2026-04-14 12:00:00", "A", "B")
			)

	def test_get_f2c_ltt_timing_settings_null_master_switch_defaults_on(self):
		"""Migrated sites: new Check column can be NULL; must not become cint(None)==0 (off)."""
		row = {
			"ltt_schedule_planned_times_enabled": None,
			"ltt_dropoff_buffer_minutes": None,
			"ltt_travel_avg_speed_kph": None,
			"ltt_travel_road_factor": None,
			"ltt_travel_min_minutes": None,
			"ltt_travel_max_minutes": None,
			"ltt_travel_fallback_minutes": None,
		}
		with patch.object(frappe.db, "exists", return_value=True):
			with patch.object(frappe.db, "get_value", return_value=row):
				out = ltt_api._get_f2c_ltt_timing_settings()
		self.assertEqual(out["ltt_schedule_planned_times_enabled"], 1)

	def test_planned_pickup_drop_missing_start_uses_now_anchor(self):
		"""Missing planned_start: drop-off anchor is current time; pickup = anchor - travel - lead."""
		fixed_now = get_datetime("2026-04-14 12:00:00")
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=_DEFAULT_LTT_SETTINGS):
			with patch.object(ltt_api, "now_datetime", return_value=fixed_now):
				with patch.object(ltt_api, "estimate_internal_ltt_travel_minutes", return_value=60):
					pu, po = ltt_api.planned_pickup_drop_for_activity_start(None, "A", "B")
		self.assertEqual(po, get_datetime_str(fixed_now))
		self.assertEqual(pu, get_datetime_str(fixed_now - timedelta(minutes=60 + 10)))

	def test_planned_pickup_drop_drop_at_start_plus_lead_and_travel(self):
		"""Drop-off at activity anchor; pickup = anchor - travel - pickup lead."""
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_dropoff_buffer_minutes": 10}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "estimate_internal_ltt_travel_minutes", return_value=60):
				start = get_datetime("2026-04-14 14:00:00")
				pu, po = ltt_api.planned_pickup_drop_for_activity_start(start, "A", "B")
				self.assertEqual(po, get_datetime_str(start))
				self.assertEqual(pu, get_datetime_str(start - timedelta(minutes=60 + 10)))

	def test_planned_anchor_accepts_activity_end_datetime(self):
		"""Return LTTs use the same helper with planned_end as anchor (drop at end)."""
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_dropoff_buffer_minutes": 15}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "estimate_internal_ltt_travel_minutes", return_value=30):
				end = get_datetime("2026-04-16 18:00:00")
				pu, po = ltt_api.planned_pickup_drop_for_activity_start(end, "WH-F", "WH-C")
				self.assertEqual(po, get_datetime_str(end))
				# Travel+lead would be 45 min; minimum 60 min before drop is enforced
				self.assertEqual(pu, get_datetime_str(end - timedelta(minutes=60)))

	def test_schedule_ltt_planned_times_enabled_false_when_setting_off(self):
		with patch.object(
			ltt_api,
			"_get_f2c_ltt_timing_settings",
			return_value={**_DEFAULT_LTT_SETTINGS, "ltt_schedule_planned_times_enabled": 0},
		):
			self.assertFalse(ltt_api.schedule_ltt_planned_times_enabled())

	def test_schedule_ltt_planned_times_enabled_true_when_on(self):
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=_DEFAULT_LTT_SETTINGS):
			self.assertTrue(ltt_api.schedule_ltt_planned_times_enabled())

	def test_clamp_pickup_only_drop_sets_sixty_minute_lead(self):
		drop = "2026-04-15 17:19:00"
		pu, po = ltt_api.clamp_planned_pickup_before_drop_str(None, drop)
		self.assertEqual(get_datetime(po), get_datetime(drop))
		self.assertEqual(get_datetime(pu), get_datetime("2026-04-15 16:19:00"))

	def test_clamp_pickup_after_drop_is_corrected(self):
		drop = "2026-04-15 17:19:00"
		bad_pickup = "2026-04-16 15:40:00"
		pu, po = ltt_api.clamp_planned_pickup_before_drop_str(bad_pickup, drop)
		self.assertEqual(get_datetime(po), get_datetime(drop))
		self.assertEqual(get_datetime(pu), get_datetime("2026-04-15 16:19:00"))

	def test_planned_internal_ltt_kwargs_from_anchor(self):
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_dropoff_buffer_minutes": 5}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "estimate_internal_ltt_travel_minutes", return_value=40):
				start = get_datetime("2026-04-14 10:00:00")
				kw = ltt_api.planned_internal_ltt_kwargs_from_anchor(start, "WH-A", "WH-B")
				self.assertEqual(kw["planned_drop_off_on"], get_datetime_str(start))
				# Travel+lead would be 45 min; minimum 60 min before drop is enforced
				self.assertEqual(kw["planned_pickup_on"], get_datetime_str(start - timedelta(minutes=60)))

	def test_planned_internal_ltt_kwargs_empty_when_switch_off(self):
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_schedule_planned_times_enabled": 0}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			kw = ltt_api.planned_internal_ltt_kwargs_from_anchor("2026-04-14 10:00:00", "A", "B")
			self.assertEqual(kw, {})

	def test_execution_anchor_activity_start_from_schedule_ref(self):
		doc = SimpleNamespace(schedule_ref="CPS-001", on_demand_activity_ref=None, actual_start="2026-02-02 08:00:00")
		with patch.object(frappe.db, "get_value", return_value="2026-02-01 07:00:00") as gv:
			a = ltt_api.execution_anchor_datetime_for_ltt(doc, anchor_kind="activity_start")
		self.assertEqual(a, "2026-02-01 07:00:00")
		gv.assert_called()

	def test_execution_anchor_activity_start_falls_back_actual_start(self):
		doc = SimpleNamespace(schedule_ref="", on_demand_activity_ref="", actual_start="2026-03-03 12:00:00")
		with patch.object(frappe.db, "get_value", return_value=None):
			a = ltt_api.execution_anchor_datetime_for_ltt(doc, anchor_kind="activity_start")
		self.assertEqual(a, "2026-03-03 12:00:00")

	def test_execution_anchor_activity_end_prefers_planned_end(self):
		doc = SimpleNamespace(
			schedule_ref="CPS-1",
			on_demand_activity_ref=None,
			actual_end=None,
			actual_start="2026-04-01 06:00:00",
		)
		with patch.object(frappe.db, "get_value", return_value="2026-04-01 17:00:00"):
			a = ltt_api.execution_anchor_datetime_for_ltt(doc, anchor_kind="activity_end")
		self.assertEqual(a, "2026-04-01 17:00:00")

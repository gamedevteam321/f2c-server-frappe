"""Tests for LTT planned pickup/drop-off from schedule (travel estimate + settings)."""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

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

	def test_planned_pickup_drop_missing_start(self):
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=_DEFAULT_LTT_SETTINGS):
			self.assertIsNone(ltt_api.planned_pickup_drop_for_activity_start(None, "A", "B"))

	def test_planned_pickup_drop_drop_at_start_plus_lead_and_travel(self):
		"""Drop-off at activity anchor; pickup = anchor - travel - pickup lead."""
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_dropoff_buffer_minutes": 10}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "estimate_internal_ltt_travel_minutes", return_value=60):
				start = get_datetime("2026-04-14 14:00:00")
				pu, po = ltt_api.planned_pickup_drop_for_activity_start(start, "A", "B")
				self.assertEqual(po, start)
				self.assertEqual(pu, start - timedelta(minutes=60 + 10))

	def test_planned_anchor_accepts_activity_end_datetime(self):
		"""Return LTTs use the same helper with planned_end as anchor (drop at end)."""
		settings = {**_DEFAULT_LTT_SETTINGS, "ltt_dropoff_buffer_minutes": 15}
		with patch.object(ltt_api, "_get_f2c_ltt_timing_settings", return_value=settings):
			with patch.object(ltt_api, "estimate_internal_ltt_travel_minutes", return_value=30):
				end = get_datetime("2026-04-16 18:00:00")
				pu, po = ltt_api.planned_pickup_drop_for_activity_start(end, "WH-F", "WH-C")
				self.assertEqual(po, end)
				self.assertEqual(pu, end - timedelta(minutes=30 + 15))

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

# Copyright (c) 2026, Orgatek and contributors
# License: MIT. See LICENSE

"""Whitelisted API for F2C Settings (logistics + inventory) for React and integrations."""

import frappe
from frappe.utils import cint, flt


def _default_settings_dict():
	# LTT: ltt_schedule_planned_times_enabled = use anchor+travel pickup lead; off = creation-time defaults.
	# ltt_dropoff_buffer_minutes = extra pickup lead (minutes), added to travel; field name kept for compatibility.
	return {
		"enforce_logistics_location_check": 1,
		"logistics_proximity_radius_meters": 1000,
		"strict_geo_area_for_warehouse_lookup": 1,
		"manual_transfer_equipment_page_size": 10,
		"ltt_schedule_planned_times_enabled": 1,
		"ltt_dropoff_buffer_minutes": 10,
		"ltt_travel_avg_speed_kph": 35.0,
		"ltt_travel_road_factor": 1.25,
		"ltt_travel_min_minutes": 5,
		"ltt_travel_max_minutes": 480,
		"ltt_travel_fallback_minutes": 60,
	}


@frappe.whitelist()
def get_f2c_settings():
	"""Return non-secret F2C Settings for any logged-in user (reads Single with ignore_permissions)."""
	if not frappe.db.exists("DocType", "F2C Settings"):
		return _default_settings_dict()

	try:
		doc = frappe.get_doc("F2C Settings", "F2C Settings", ignore_permissions=True)
	except Exception:
		return _default_settings_dict()

	_ltt_times_flag = doc.get("ltt_schedule_planned_times_enabled")
	return {
		"enforce_logistics_location_check": cint(doc.get("enforce_logistics_location_check")),
		"logistics_proximity_radius_meters": cint(doc.get("logistics_proximity_radius_meters")) or 1000,
		"strict_geo_area_for_warehouse_lookup": cint(doc.get("strict_geo_area_for_warehouse_lookup")),
		"manual_transfer_equipment_page_size": cint(doc.get("manual_transfer_equipment_page_size")) or 10,
		"ltt_schedule_planned_times_enabled": 1
		if _ltt_times_flag is None
		else cint(_ltt_times_flag),
		"ltt_dropoff_buffer_minutes": cint(doc.get("ltt_dropoff_buffer_minutes")) or 10,
		"ltt_travel_avg_speed_kph": flt(doc.get("ltt_travel_avg_speed_kph")) or 35.0,
		"ltt_travel_road_factor": flt(doc.get("ltt_travel_road_factor")) or 1.25,
		"ltt_travel_min_minutes": cint(doc.get("ltt_travel_min_minutes")) or 5,
		"ltt_travel_max_minutes": cint(doc.get("ltt_travel_max_minutes")) or 480,
		"ltt_travel_fallback_minutes": cint(doc.get("ltt_travel_fallback_minutes")) or 60,
	}

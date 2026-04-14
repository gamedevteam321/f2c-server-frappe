# Copyright (c) 2026, Orgatek and contributors
# License: MIT. See LICENSE

"""Whitelisted API for F2C Settings (logistics + inventory) for React and integrations."""

import frappe
from frappe.utils import cint


def _default_settings_dict():
	return {
		"enforce_logistics_location_check": 1,
		"logistics_proximity_radius_meters": 1000,
		"strict_geo_area_for_warehouse_lookup": 1,
		"manual_transfer_equipment_page_size": 10,
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

	return {
		"enforce_logistics_location_check": cint(doc.get("enforce_logistics_location_check")),
		"logistics_proximity_radius_meters": cint(doc.get("logistics_proximity_radius_meters")) or 1000,
		"strict_geo_area_for_warehouse_lookup": cint(doc.get("strict_geo_area_for_warehouse_lookup")),
		"manual_transfer_equipment_page_size": cint(doc.get("manual_transfer_equipment_page_size")) or 10,
	}

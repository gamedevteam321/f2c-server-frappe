# -*- coding: utf-8 -*-
"""Resolve Farm–Cluster–Field depth for equipment and enforce cluster-only tractor↔implement edits."""

from __future__ import annotations

import frappe
from frappe import _


def location_warehouse_level_from_location_name(location_name: str | None) -> str | None:
	"""
	farm | cluster | field from Location.location_name depth (Farm-Cluster-Field path).
	"""
	if not (location_name or "").strip():
		return None
	parts = [p.strip() for p in str(location_name).split("-") if p.strip()]
	n = len(parts)
	if n >= 3:
		return "field"
	if n == 2:
		return "cluster"
	if n == 1:
		return "farm"
	return None


def location_level_for_equipment_doc(doctype: str, name: str | None) -> str | None:
	"""Machinery / Implement / Hand Tool / Other Tool → farm | cluster | field | None."""
	if not name or doctype not in ("Machinery", "Implement", "Hand Tool", "Other Tool"):
		return None
	row = frappe.db.get_value(doctype, name, ["asset", "location"], as_dict=True)
	if not row:
		return None
	loc_id = None
	if row.get("asset"):
		loc_id = frappe.db.get_value("Asset", row["asset"], "location")
	if not loc_id and row.get("location"):
		loc_id = row["location"]
	if not loc_id:
		return None
	location_name = frappe.db.get_value("Location", loc_id, "location_name")
	return location_warehouse_level_from_location_name(location_name)


def location_warehouse_level_for_warehouse_name(warehouse: str | None) -> str | None:
	"""Resolve Warehouse → Location → path depth (same rule as asset rows)."""
	if not warehouse:
		return None
	from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse

	try:
		res = get_location_for_warehouse(warehouse)
	except Exception:
		return None
	loc = (res or {}).get("location")
	if not loc:
		return None
	try:
		location_name = frappe.db.get_value("Location", loc, "location_name")
	except Exception:
		location_name = None
	return location_warehouse_level_from_location_name(location_name)


def assert_cluster_for_tractor_implement_link_change(
	tractor_machinery_name: str,
	old_implement_doc: str | None,
	new_implement_doc: str | None,
) -> None:
	"""
	Location restriction removed — attach/detach is now allowed at any level (field, cluster, farm).
	The 'in use' guard is enforced separately in implement_attach_api.
	"""
	return


def assert_cluster_for_implement_side_link_change(
	implement_doc_name: str,
	old_tractor_machinery: str | None,
	new_tractor_machinery: str | None,
) -> None:
	"""Location restriction removed — see assert_cluster_for_tractor_implement_link_change."""
	return

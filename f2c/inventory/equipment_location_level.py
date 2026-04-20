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
	Raise if tractor/implement attachment is being changed when any involved equipment
	is not physically at cluster level. Skip when skip_cluster_attachment_validation flag is set.
	"""
	if frappe.flags.get("skip_cluster_attachment_validation"):
		return
	if (old_implement_doc or "") == (new_implement_doc or ""):
		return

	def _need_cluster(label: str, doctype: str, docname: str) -> None:
		lvl = location_level_for_equipment_doc(doctype, docname)
		if lvl is None:
			return
		if lvl != "cluster":
			frappe.throw(
				_(
					"{0} must be at a cluster location to attach or detach implements. "
					"Move equipment to the cluster (logistics transfer) first."
				).format(label)
			)

	_need_cluster(_("Tractor"), "Machinery", tractor_machinery_name)
	if old_implement_doc:
		_need_cluster(_("Current implement"), "Implement", old_implement_doc)
	if new_implement_doc and new_implement_doc != old_implement_doc:
		_need_cluster(_("Implement"), "Implement", new_implement_doc)


def assert_cluster_for_implement_side_link_change(
	implement_doc_name: str,
	old_tractor_machinery: str | None,
	new_tractor_machinery: str | None,
) -> None:
	if frappe.flags.get("skip_cluster_attachment_validation"):
		return
	if (old_tractor_machinery or "") == (new_tractor_machinery or ""):
		return

	def _need_cluster(label: str, doctype: str, docname: str) -> None:
		lvl = location_level_for_equipment_doc(doctype, docname)
		if lvl is None:
			return
		if lvl != "cluster":
			frappe.throw(
				_(
					"{0} must be at a cluster location to attach or detach implements. "
					"Move equipment to the cluster (logistics transfer) first."
				).format(label)
			)

	_need_cluster(_("Implement"), "Implement", implement_doc_name)
	if old_tractor_machinery:
		_need_cluster(_("Tractor"), "Machinery", old_tractor_machinery)
	if new_tractor_machinery and new_tractor_machinery != old_tractor_machinery:
		_need_cluster(_("Tractor"), "Machinery", new_tractor_machinery)

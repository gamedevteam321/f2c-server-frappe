# -*- coding: utf-8 -*-
"""permission_query_conditions hooks for Field / Cluster supervisor geo scoping."""

from __future__ import annotations

import frappe

from f2c.access.field_scope import (
	allowed_warehouse_names_for_area_names,
	cluster_supervisor_data_scope_active,
	field_supervisor_data_scope_active,
	get_user_scope_area_roots,
	get_user_scope_expanded_area_names,
	supervisor_geo_scope_active,
)


def _supervisor_scope(user: str | None) -> tuple[frozenset[str], frozenset[str]] | None:
	"""Return (expanded_area_names, allowed_warehouses) when Field or Cluster supervisor geo scope applies."""
	if not supervisor_geo_scope_active(user):
		return None
	roots = get_user_scope_area_roots(user)
	if not roots:
		return (frozenset(), frozenset())
	areas = get_user_scope_expanded_area_names(user)
	wh = allowed_warehouse_names_for_area_names(areas)
	return (areas, wh)


def _sql_in_list(values: frozenset[str]) -> str:
	if not values:
		return ""
	return ", ".join(frappe.db.escape(v, percent=False) for v in sorted(values))


def _geo_parent_chain_upward(area_name: str) -> frozenset[str]:
	"""Self plus every parent_area up the tree (for Cluster Supervisor GFA list access)."""
	names: set[str] = set()
	cur = (area_name or "").strip()
	for _ in range(50):
		if not cur or cur in names:
			break
		names.add(cur)
		rows = frappe.get_all(
			"Geo Fencing Area",
			filters={"name": cur},
			fields=["parent_area"],
			limit=1,
			ignore_permissions=True,
		)
		if not rows:
			break
		pa = (rows[0].get("parent_area") or "").strip()
		if not pa:
			break
		cur = pa
	return frozenset(names)


def get_farm_task_execution_query(user, doctype=None) -> str | None:
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, _ = sc
	if not areas:
		return "1=0"
	return f"`tabFarm Task Execution`.`field` IN ({_sql_in_list(areas)})"


def get_on_demand_activity_query(user, doctype=None) -> str | None:
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, _ = sc
	if not areas:
		return "1=0"
	return f"`tabOn Demand Activity`.`field` IN ({_sql_in_list(areas)})"


def get_crop_plan_schedule_query(user, doctype=None) -> str | None:
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, _ = sc
	if not areas:
		return "1=0"
	return f"`tabCrop Plan Schedule`.`field` IN ({_sql_in_list(areas)})"


def get_logistics_transfer_ticket_query(user, doctype=None) -> str | None:
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, wh = sc
	if not areas:
		return "1=0"
	in_areas = _sql_in_list(areas)
	ex_sub = (
		f"`tabLogistics Transfer Ticket`.`farm_task_execution` IN ("
		f"SELECT `name` FROM `tabFarm Task Execution` WHERE `field` IN ({in_areas}))"
	)
	cs = cluster_supervisor_data_scope_active(user)
	if not wh:
		leg = f"({ex_sub})"
		ext = f"(`tabLogistics Transfer Ticket`.`transfer_type` = 'External' AND {leg})"
		if cs:
			intq = f"(`tabLogistics Transfer Ticket`.`transfer_type` = 'Internal' AND {leg})"
			return f"({ext} OR {intq})"
		return ext
	in_list = ", ".join(frappe.db.escape(w, percent=False) for w in sorted(wh))
	from_wh = f"`tabLogistics Transfer Ticket`.`from_warehouse` IN ({in_list})"
	to_wh = f"`tabLogistics Transfer Ticket`.`to_warehouse` IN ({in_list})"
	leg = f"({ex_sub} OR {from_wh} OR {to_wh})"
	ext = f"(`tabLogistics Transfer Ticket`.`transfer_type` = 'External' AND {leg})"
	if cs:
		intq = f"(`tabLogistics Transfer Ticket`.`transfer_type` = 'Internal' AND {leg})"
		return f"({ext} OR {intq})"
	return ext


def get_farm_worker_details_query(user, doctype=None) -> str | None:
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, _ = sc
	if not areas:
		return "1=0"
	return f"`tabFarm Worker Details`.`farm` IN ({_sql_in_list(areas)})"


def get_farm_worker_attendance_query(user, doctype=None) -> str | None:
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, _ = sc
	if not areas:
		return "1=0"
	in_areas = _sql_in_list(areas)
	return (
		"`tabFarm Worker Attendance`.`farm_worker` IN ("
		f"SELECT `name` FROM `tabFarm Worker Details` WHERE `farm` IN ({in_areas}))"
	)


def get_geo_fencing_area_query(user, doctype=None) -> str | None:
	"""Rows whose Geo Fencing Area name is in expanded scope (plus parent chain for Cluster Supervisor)."""
	sc = _supervisor_scope(user)
	if sc is None:
		return None
	areas, _ = sc
	if not areas:
		return "1=0"
	all_names = set(areas)
	if cluster_supervisor_data_scope_active(user) and not field_supervisor_data_scope_active(user):
		for r in get_user_scope_area_roots(user):
			all_names.update(_geo_parent_chain_upward(str(r).strip()))
	return f"`tabGeo Fencing Area`.`name` IN ({_sql_in_list(frozenset(all_names))})"


def has_farm_worker_attendance_permission(doc, ptype, user=None, debug=False) -> bool | None:
	"""Deny delete for Field Supervisor."""
	if ptype != "delete":
		return None
	if field_supervisor_data_scope_active(user):
		return False
	return None

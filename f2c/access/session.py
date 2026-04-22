# -*- coding: utf-8 -*-
"""Session / access context for React and other API clients."""

from __future__ import annotations

import frappe

from f2c.access.constants import (
	CLUSTER_LEVEL_GFA_TYPE_NAME,
	CLUSTER_SUPERVISOR_ROLE,
	DRIVER_ROLE,
	EMPLOYEE_ALLOWED_GEO_FIELDNAME,
	FARM_LEVEL_GFA_TYPE_NAME,
	FARM_MANAGER_ROLE,
	FIELD_LEVEL_GFA_TYPE_NAME,
	FIELD_SUPERVISOR_ROLE,
	PROJECT_MANAGER_ROLE,
	USER_ASSIGNED_FIELD_FIELDNAME,
)
from f2c.access.field_scope import (
	allowed_warehouse_names_for_area_names,
	cluster_supervisor_data_scope_active,
	driver_data_scope_active,
	farm_manager_data_scope_active,
	field_supervisor_data_scope_active,
	get_user_scope_area_roots,
	get_user_scope_expanded_area_names,
	supervisor_geo_scope_active,
	user_bypasses_field_supervisor_restrictions,
	user_has_cluster_supervisor_role,
	user_has_driver_role,
	user_has_farm_manager_role,
	user_has_field_supervisor_role,
	user_has_project_manager_role,
)


def _geo_fencing_area_level_type_name(area_name: str) -> str | None:
	"""Geo Fencing Type.geo_fencing_type_name for this area (Geo Fencing Area.geo_fencing_type is a Link)."""
	n = (area_name or "").strip()
	if not n:
		return None
	arows = frappe.get_all(
		"Geo Fencing Area",
		filters={"name": n},
		fields=["geo_fencing_type"],
		limit=1,
		ignore_permissions=True,
	)
	if not arows:
		return None
	gft = (arows[0].get("geo_fencing_type") or "").strip()
	if not gft:
		return None
	trows = frappe.get_all(
		"Geo Fencing Type",
		filters={"name": gft},
		fields=["geo_fencing_type_name"],
		limit=1,
		ignore_permissions=True,
	)
	if not trows:
		return None
	return (trows[0].get("geo_fencing_type_name") or "").strip() or None


def _geo_parent_area_name(area_name: str) -> str | None:
	n = (area_name or "").strip()
	if not n:
		return None
	arows = frappe.get_all(
		"Geo Fencing Area",
		filters={"name": n},
		fields=["parent_area"],
		limit=1,
		ignore_permissions=True,
	)
	if not arows:
		return None
	return (arows[0].get("parent_area") or "").strip() or None


def _cluster_supervisor_inventory_cluster_ids(user: str) -> list[str]:
	"""Cluster Geo Fencing Area `name` values for inventory filters (Cluster Supervisor only; server-resolved)."""
	if not cluster_supervisor_data_scope_active(user):
		return []
	roots = [str(x).strip() for x in get_user_scope_area_roots(user) if x and str(x).strip()]
	if not roots:
		return []
	found: set[str] = set()
	for r in roots:
		cur = r
		for _ in range(50):
			tn = _geo_fencing_area_level_type_name(cur)
			if tn == CLUSTER_LEVEL_GFA_TYPE_NAME:
				found.add(cur)
				break
			pa = _geo_parent_area_name(cur)
			if not pa:
				break
			cur = pa
	if found:
		return sorted(found)
	exp = list(get_user_scope_expanded_area_names(user))
	out: set[str] = set()
	for n in exp:
		if _geo_fencing_area_level_type_name(n) == CLUSTER_LEVEL_GFA_TYPE_NAME:
			out.add(n)
	return sorted(out)


def _field_level_geo_names_in_expanded(expanded: frozenset[str]) -> list[str]:
	"""Geo Fencing Area `name` values in ``expanded`` whose type is Field (matches ``Crop Plan.field`` / schedule field)."""
	if not expanded:
		return []
	out: set[str] = set()
	for n in expanded:
		nm = str(n or "").strip()
		if not nm:
			continue
		if _geo_fencing_area_level_type_name(nm) == FIELD_LEVEL_GFA_TYPE_NAME:
			out.add(nm)
	return sorted(out)


def _farm_manager_inventory_farm_names(user: str) -> list[str]:
	"""Farm Geo Fencing Area `name` values for the inventory Farm dropdown (Farm Manager scoped).

	Clusters and fields under a selected farm are loaded in full from the geo tree on the client;
	only which farms appear is restricted to the manager's Employee geo scope.
	"""
	if not farm_manager_data_scope_active(user):
		return []
	expanded = get_user_scope_expanded_area_names(user)
	if not expanded:
		return []
	farms: set[str] = set()
	for raw in expanded:
		cur = str(raw or "").strip()
		for _ in range(50):
			if not cur:
				break
			tn = _geo_fencing_area_level_type_name(cur)
			if tn == FARM_LEVEL_GFA_TYPE_NAME:
				farms.add(cur)
			pa = _geo_parent_area_name(cur)
			if not pa:
				break
			cur = pa
	return sorted(farms)


def _cluster_supervisor_inventory_cluster_rows(cluster_ids: list[str]) -> list[dict[str, str]]:
	"""name + area_name for inventory Cluster dropdown (stable order follows cluster_ids)."""
	if not cluster_ids:
		return []
	rows = frappe.get_all(
		"Geo Fencing Area",
		filters={"name": ["in", cluster_ids]},
		fields=["name", "area_name"],
		limit_page_length=0,
		ignore_permissions=True,
	) or []
	order = {n: i for i, n in enumerate(cluster_ids)}
	rows.sort(key=lambda r: order.get(str(r.get("name") or ""), 9999))
	out: list[dict[str, str]] = []
	for r in rows:
		nm = str(r.get("name") or "").strip()
		if not nm:
			continue
		out.append({"name": nm, "area_name": str(r.get("area_name") or "").strip()})
	return out


@frappe.whitelist()
def get_access_context():
	"""Return roles and Field / Cluster / Driver / Farm Manager flags for the current session user."""
	user = frappe.session.user
	roles = list(frappe.get_roles(user))
	fs_scope = field_supervisor_data_scope_active(user)
	cs_scope = cluster_supervisor_data_scope_active(user)
	driver_scope = driver_data_scope_active(user)
	fm_scope = farm_manager_data_scope_active(user)
	geo_scope = supervisor_geo_scope_active(user)
	has_fs_role = user_has_field_supervisor_role(user)
	has_cs_role = user_has_cluster_supervisor_role(user)
	has_driver_role = user_has_driver_role(user)
	has_fm_role = user_has_farm_manager_role(user)
	has_pm_role = user_has_project_manager_role(user)
	bypass_scoped = user_bypasses_field_supervisor_restrictions(user)
	roots = get_user_scope_area_roots(user) if geo_scope else tuple()
	expanded = get_user_scope_expanded_area_names(user) if geo_scope else frozenset()
	warehouses = sorted(allowed_warehouse_names_for_area_names(expanded)) if expanded else []

	supervisor_geo_mode = None
	if fs_scope:
		supervisor_geo_mode = "field"
	elif cs_scope or driver_scope:
		supervisor_geo_mode = "cluster"
	elif fm_scope:
		supervisor_geo_mode = "farm_manager"

	cs_inventory_clusters = _cluster_supervisor_inventory_cluster_ids(user)
	cs_inventory_cluster_rows = (
		_cluster_supervisor_inventory_cluster_rows(cs_inventory_clusters) if cs_inventory_clusters else []
	)

	fm_inv_farms = _farm_manager_inventory_farm_names(user)
	crop_plan_activity_field_names = _field_level_geo_names_in_expanded(expanded) if geo_scope else []

	def _can_list(doctype: str) -> bool:
		return bool(frappe.has_permission(doctype, ptype="read", user=user, throw=False))

	can_list_crop_plan = _can_list("Crop Plan")
	can_list_crop_plan_schedule = _can_list("Crop Plan Schedule")
	can_list_on_demand_activity = _can_list("On Demand Activity")
	can_list_farm_task_execution = _can_list("Farm Task Execution")
	can_list_geo_fencing_area = _can_list("Geo Fencing Area")
	can_list_material_request = _can_list("Material Request")

	return {
		"user": user,
		"roles": roles,
		"is_field_supervisor": bool(fs_scope),
		"has_field_supervisor_role": bool(has_fs_role),
		"field_supervisor_role": FIELD_SUPERVISOR_ROLE,
		"is_cluster_supervisor": bool(cs_scope),
		"has_cluster_supervisor_role": bool(has_cs_role),
		"cluster_supervisor_role": CLUSTER_SUPERVISOR_ROLE,
		"is_driver": bool(driver_scope),
		"has_driver_role": bool(has_driver_role),
		"driver_role": DRIVER_ROLE,
		"is_farm_manager": bool(fm_scope),
		"has_farm_manager_role": bool(has_fm_role),
		"farm_manager_role": FARM_MANAGER_ROLE,
		"has_project_manager_role": bool(has_pm_role),
		"project_manager_role": PROJECT_MANAGER_ROLE,
		"bypasses_scoped_restrictions": bool(bypass_scoped),
		"supervisor_geo_mode": supervisor_geo_mode,
		"assigned_field": roots[0] if roots else None,
		"assigned_fields": list(roots),
		"assigned_area_names": sorted(expanded),
		"allowed_warehouse_names": warehouses,
		"execution_review_readonly": bool(fs_scope),
		"inventory_review_readonly": bool(fs_scope),
		"user_assigned_field_fieldname": USER_ASSIGNED_FIELD_FIELDNAME,
		"employee_allowed_geo_fieldname": EMPLOYEE_ALLOWED_GEO_FIELDNAME,
		"cluster_supervisor_inventory_cluster_ids": cs_inventory_clusters,
		"cluster_supervisor_inventory_clusters": cs_inventory_cluster_rows,
		"farm_manager_inventory_farm_names": fm_inv_farms,
		"crop_plan_activity_field_names": crop_plan_activity_field_names,
		"can_list_crop_plan": can_list_crop_plan,
		"can_list_crop_plan_schedule": can_list_crop_plan_schedule,
		"can_list_on_demand_activity": can_list_on_demand_activity,
		"can_list_farm_task_execution": can_list_farm_task_execution,
		"can_list_geo_fencing_area": can_list_geo_fencing_area,
		"can_list_material_request": can_list_material_request,
	}

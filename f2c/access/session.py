# -*- coding: utf-8 -*-
"""Session / access context for React and other API clients."""

from __future__ import annotations

import frappe

from f2c.access.constants import (
	CLUSTER_LEVEL_GFA_TYPE_NAME,
	CLUSTER_SUPERVISOR_ROLE,
	FIELD_SUPERVISOR_ROLE,
	USER_ASSIGNED_FIELD_FIELDNAME,
	USER_SCOPE_AREAS_FIELDNAME,
)
from f2c.access.field_scope import (
	allowed_warehouse_names_for_area_names,
	cluster_supervisor_data_scope_active,
	field_supervisor_data_scope_active,
	get_user_scope_area_roots,
	get_user_scope_expanded_area_names,
	supervisor_geo_scope_active,
	user_has_cluster_supervisor_role,
	user_has_field_supervisor_role,
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
	"""Cluster Geo Fencing Area `name` values for inventory filters (server-resolved; bypasses GFA list perms)."""
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
	"""Return roles and Field / Cluster supervisor flags for the current session user."""
	user = frappe.session.user
	roles = list(frappe.get_roles(user))
	fs_scope = field_supervisor_data_scope_active(user)
	cs_scope = cluster_supervisor_data_scope_active(user)
	geo_scope = supervisor_geo_scope_active(user)
	has_fs_role = user_has_field_supervisor_role(user)
	has_cs_role = user_has_cluster_supervisor_role(user)
	roots = get_user_scope_area_roots(user) if geo_scope else tuple()
	expanded = get_user_scope_expanded_area_names(user) if geo_scope else frozenset()
	warehouses = sorted(allowed_warehouse_names_for_area_names(expanded)) if expanded else []

	supervisor_geo_mode = None
	if fs_scope:
		supervisor_geo_mode = "field"
	elif cs_scope:
		supervisor_geo_mode = "cluster"

	cs_inventory_clusters = _cluster_supervisor_inventory_cluster_ids(user) if cs_scope else []
	cs_inventory_cluster_rows = (
		_cluster_supervisor_inventory_cluster_rows(cs_inventory_clusters) if cs_inventory_clusters else []
	)

	return {
		"user": user,
		"roles": roles,
		"is_field_supervisor": bool(fs_scope),
		"has_field_supervisor_role": bool(has_fs_role),
		"field_supervisor_role": FIELD_SUPERVISOR_ROLE,
		"is_cluster_supervisor": bool(cs_scope),
		"has_cluster_supervisor_role": bool(has_cs_role),
		"cluster_supervisor_role": CLUSTER_SUPERVISOR_ROLE,
		"supervisor_geo_mode": supervisor_geo_mode,
		"assigned_field": roots[0] if roots else None,
		"assigned_fields": list(roots),
		"assigned_area_names": sorted(expanded),
		"allowed_warehouse_names": warehouses,
		"execution_review_readonly": bool(fs_scope),
		"inventory_review_readonly": bool(fs_scope),
		"user_assigned_field_fieldname": USER_ASSIGNED_FIELD_FIELDNAME,
		"user_scope_areas_fieldname": USER_SCOPE_AREAS_FIELDNAME,
		"cluster_supervisor_inventory_cluster_ids": cs_inventory_clusters,
		"cluster_supervisor_inventory_clusters": cs_inventory_cluster_rows,
	}

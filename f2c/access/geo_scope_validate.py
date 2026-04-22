# -*- coding: utf-8 -*-
"""Shared validation for Employee Allowed Geo Areas vs linked User F2C roles."""

from __future__ import annotations

import frappe

from f2c.access.constants import (
	CLUSTER_LEVEL_GFA_TYPE_NAME,
	CLUSTER_SUPERVISOR_ROLE,
	DRIVER_ROLE,
	FARM_LEVEL_GFA_TYPE_NAME,
	FARM_MANAGER_ROLE,
	FIELD_LEVEL_GFA_TYPE_NAME,
	FIELD_SUPERVISOR_ROLE,
	FULL_ACCESS_USERS,
	ROLES_ALLOWED_TO_EDIT_EMPLOYEE_GEO_SCOPE,
)
from f2c.access.field_scope import user_bypasses_field_supervisor_restrictions

_FARM_MANAGER_SCOPE_ROOT_TYPES = frozenset(
	{FARM_LEVEL_GFA_TYPE_NAME, CLUSTER_LEVEL_GFA_TYPE_NAME, FIELD_LEVEL_GFA_TYPE_NAME}
)


def _session_user_may_edit_employee_geo_scope() -> bool:
	if user_bypasses_field_supervisor_restrictions(frappe.session.user):
		return True
	return bool(ROLES_ALLOWED_TO_EDIT_EMPLOYEE_GEO_SCOPE & frozenset(frappe.get_roles()))


def _geo_fencing_area_type_name(area_name: str) -> str | None:
	gft = frappe.db.get_value("Geo Fencing Area", area_name, "geo_fencing_type")
	if not gft:
		return None
	return (frappe.db.get_value("Geo Fencing Type", gft, "geo_fencing_type_name") or "").strip() or None


def _scope_area_doc_label(area_id: str) -> str:
	an = (frappe.db.get_value("Geo Fencing Area", area_id, "area_name") or "").strip()
	return f"{area_id!r} ({an})" if an else f"{area_id!r}"


def validate_allowed_geo_for_linked_user(
	scope_root_names: list[str],
	*,
	linked_user_id: str,
	old_roots: tuple[str, ...],
) -> None:
	"""
	Raise if Allowed Geo Areas violate role rules or privileged-editor policy.
	linked_user_id: User linked via Employee.user_id.
	old_roots: committed Employee child rows before this save (same order semantics as tuple sort for comparison).
	"""
	uid = (linked_user_id or "").strip()
	if not uid or uid in FULL_ACCESS_USERS:
		return

	roles = frappe.get_roles(uid)
	has_fs = FIELD_SUPERVISOR_ROLE in roles
	has_cs = CLUSTER_SUPERVISOR_ROLE in roles
	has_driver = DRIVER_ROLE in roles
	has_fm = FARM_MANAGER_ROLE in roles

	if not (has_fs or has_cs or has_driver or has_fm):
		return

	if set(scope_root_names) != set(old_roots):
		if not _session_user_may_edit_employee_geo_scope():
			frappe.throw(
				"Only a System Manager, Administrator, or Project Manager can assign or change Allowed Geo Areas "
				f"for an employee whose user has role {FIELD_SUPERVISOR_ROLE!r}, {CLUSTER_SUPERVISOR_ROLE!r}, "
				f"{DRIVER_ROLE!r}, or {FARM_MANAGER_ROLE!r}."
			)

	if has_fs:
		if not scope_root_names:
			frappe.throw(
				f"Users with role {FIELD_SUPERVISOR_ROLE!r} must have at least one Geo Fencing Area in "
				"Allowed Geo Areas on the Employee record (Field-level areas only)."
			)
		for val in scope_root_names:
			if not frappe.db.exists("Geo Fencing Area", val):
				frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
			tn = _geo_fencing_area_type_name(val)
			if tn != FIELD_LEVEL_GFA_TYPE_NAME:
				frappe.throw(
					f"Field Supervisor scope must be {FIELD_LEVEL_GFA_TYPE_NAME!r}-level Geo Fencing Areas only "
					f"({_scope_area_doc_label(val)} is type {tn!r})."
				)
		return

	if has_cs or has_driver:
		if not scope_root_names:
			frappe.throw(
				f"Users with role {CLUSTER_SUPERVISOR_ROLE!r} or {DRIVER_ROLE!r} must have at least one Geo Fencing Area in "
				"Allowed Geo Areas on the Employee record (Cluster-level areas only)."
			)
		for val in scope_root_names:
			if not frappe.db.exists("Geo Fencing Area", val):
				frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
			tn = _geo_fencing_area_type_name(val)
			if tn != CLUSTER_LEVEL_GFA_TYPE_NAME:
				frappe.throw(
					f"Cluster Supervisor / Driver scope must use {CLUSTER_LEVEL_GFA_TYPE_NAME!r}-level Geo Fencing Areas only. "
					f"Linked {_scope_area_doc_label(val)} is type {tn!r}, not {CLUSTER_LEVEL_GFA_TYPE_NAME!r}. "
					"Open Geo Fencing Area and link the row whose Geo Fencing Type is Cluster (document Name is not the same as Area Name)."
				)
		return

	if has_fm:
		if not scope_root_names:
			frappe.throw(
				f"Users with role {FARM_MANAGER_ROLE!r} must have at least one Geo Fencing Area in "
				"Allowed Geo Areas on the Employee record (Farm, Cluster, or Field level)."
			)
		for val in scope_root_names:
			if not frappe.db.exists("Geo Fencing Area", val):
				frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
			tn = _geo_fencing_area_type_name(val)
			if tn not in _FARM_MANAGER_SCOPE_ROOT_TYPES:
				allowed = ", ".join(sorted(_FARM_MANAGER_SCOPE_ROOT_TYPES))
				frappe.throw(
					f"Farm Manager scope roots must be Farm, Cluster, or Field Geo Fencing Areas only "
					f"({_scope_area_doc_label(val)} is type {tn!r}; allowed: {allowed})."
				)

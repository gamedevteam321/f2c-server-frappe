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
	PROJECT_MANAGER_ROLE,
	ROLES_ALLOWED_TO_EDIT_EMPLOYEE_GEO_SCOPE,
)
from f2c.access.field_scope import user_bypasses_field_supervisor_restrictions

# Highest wins when multiple scope roles are assigned: PM > FM > CS/Driver > FS.
_SCOPE_ROLE_RANKS: dict[str, int] = {
	PROJECT_MANAGER_ROLE: 4,
	FARM_MANAGER_ROLE: 3,
	CLUSTER_SUPERVISOR_ROLE: 2,
	DRIVER_ROLE: 2,
	FIELD_SUPERVISOR_ROLE: 1,
}

_SCOPE_RANK_ALLOWED_ROOT_TYPES: dict[int, frozenset[str]] = {
	4: frozenset({FARM_LEVEL_GFA_TYPE_NAME, CLUSTER_LEVEL_GFA_TYPE_NAME, FIELD_LEVEL_GFA_TYPE_NAME}),
	3: frozenset({FARM_LEVEL_GFA_TYPE_NAME, CLUSTER_LEVEL_GFA_TYPE_NAME, FIELD_LEVEL_GFA_TYPE_NAME}),
	2: frozenset({CLUSTER_LEVEL_GFA_TYPE_NAME, FIELD_LEVEL_GFA_TYPE_NAME}),
	1: frozenset({FIELD_LEVEL_GFA_TYPE_NAME}),
}

# Set by attendance_portal.update_employee (etc.) to a list[str] of role names before Employee.save,
# so geo validation uses the merged portal roles while User is not yet saved to the DB.
FORCE_USER_ROLES_FOR_GEO_SCOPE_FLAG = "f2c_force_user_roles_for_geo_scope"


def _roles_for_geo_scope_validation(linked_user_id: str) -> list[str]:
	forced = getattr(frappe.flags, FORCE_USER_ROLES_FOR_GEO_SCOPE_FLAG, None)
	if isinstance(forced, (list, tuple)) and len(forced) > 0:
		return [str(r).strip() for r in forced if str(r).strip()]
	return frappe.get_roles(linked_user_id)


def _effective_scope_rank(roles: list[str]) -> int | None:
	"""Return highest scope rank among roles, or None if no scoped F2C role applies."""
	best = 0
	rset = frozenset(roles)
	for role, rank in _SCOPE_ROLE_RANKS.items():
		if role in rset:
			best = max(best, rank)
	return best if best else None


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


def _rank_scope_label(rank: int) -> str:
	return {
		4: "Project Manager",
		3: "Farm Manager",
		2: "Cluster Supervisor / Driver",
		1: "Field Supervisor",
	}.get(rank, "scoped role")


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

	roles = _roles_for_geo_scope_validation(uid)
	rank = _effective_scope_rank(roles)
	if rank is None:
		return

	allowed_types = _SCOPE_RANK_ALLOWED_ROOT_TYPES[rank]
	allowed_label = ", ".join(sorted(allowed_types))

	if set(scope_root_names) != set(old_roots):
		if not _session_user_may_edit_employee_geo_scope():
			frappe.throw(
				"Only a System Manager, Administrator, or Project Manager can assign or change Allowed Geo Areas "
				f"for an employee whose user has a scoped F2C role ({FIELD_SUPERVISOR_ROLE!r}, {CLUSTER_SUPERVISOR_ROLE!r}, "
				f"{DRIVER_ROLE!r}, {FARM_MANAGER_ROLE!r}, or {PROJECT_MANAGER_ROLE!r})."
			)

	if not scope_root_names:
		frappe.throw(
			f"At least one Geo Fencing Area is required in Allowed Geo Areas on the Employee record. "
			f"For the highest active scope role ({_rank_scope_label(rank)}), only these area levels are allowed: {allowed_label}."
		)

	for val in scope_root_names:
		if not frappe.db.exists("Geo Fencing Area", val):
			frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
		tn = _geo_fencing_area_type_name(val)
		if tn not in allowed_types:
			frappe.throw(
				f"Allowed Geo Areas must match the highest scope role ({_rank_scope_label(rank)}): "
				f"only {allowed_label!r} level(s). {_scope_area_doc_label(val)} is type {tn!r}."
			)

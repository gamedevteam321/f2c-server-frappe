# -*- coding: utf-8 -*-
"""User document hooks for F2C access fields."""

from __future__ import annotations

import frappe

from f2c.access.constants import (
	CLUSTER_SUPERVISOR_ROLE,
	DRIVER_ROLE,
	FARM_MANAGER_ROLE,
	FIELD_SUPERVISOR_ROLE,
	FULL_ACCESS_USERS,
	PROJECT_MANAGER_ROLE,
)


def _roles_from_user_doc(doc) -> list[str]:
	roles = []
	for r in doc.get("roles") or []:
		role = getattr(r, "role", None) or (r.get("role") if isinstance(r, dict) else None)
		if role:
			roles.append(role)
	return roles


def validate(doc, method=None):
	"""F2C role exclusivity (geo scope is validated on Employee Allowed Geo Areas)."""
	if (doc.name or "").strip() in FULL_ACCESS_USERS:
		return

	roles = _roles_from_user_doc(doc)
	has_fs = FIELD_SUPERVISOR_ROLE in roles
	has_cs = CLUSTER_SUPERVISOR_ROLE in roles
	has_driver = DRIVER_ROLE in roles
	has_fm = FARM_MANAGER_ROLE in roles
	has_pm = PROJECT_MANAGER_ROLE in roles

	if has_fs and has_cs:
		frappe.throw(
			f"A user cannot have both {FIELD_SUPERVISOR_ROLE!r} and {CLUSTER_SUPERVISOR_ROLE!r} roles."
		)
	if has_fs and has_driver:
		frappe.throw(f"A user cannot have both {FIELD_SUPERVISOR_ROLE!r} and {DRIVER_ROLE!r} roles.")
	if has_fs and has_fm:
		frappe.throw(f"A user cannot have both {FIELD_SUPERVISOR_ROLE!r} and {FARM_MANAGER_ROLE!r} roles.")
	if has_cs and has_fm:
		frappe.throw(f"A user cannot have both {CLUSTER_SUPERVISOR_ROLE!r} and {FARM_MANAGER_ROLE!r} roles.")
	if has_driver and has_fm:
		frappe.throw(f"A user cannot have both {DRIVER_ROLE!r} and {FARM_MANAGER_ROLE!r} roles.")
	if has_pm and (has_fs or has_cs or has_driver or has_fm):
		frappe.throw(
			f"A user cannot have role {PROJECT_MANAGER_ROLE!r} together with "
			f"{FIELD_SUPERVISOR_ROLE!r}, {CLUSTER_SUPERVISOR_ROLE!r}, {DRIVER_ROLE!r}, or {FARM_MANAGER_ROLE!r}."
		)

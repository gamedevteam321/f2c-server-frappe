# -*- coding: utf-8 -*-
"""User document hooks for F2C access fields."""

from __future__ import annotations

import frappe

from f2c.access.constants import (
	FIELD_LEVEL_GFA_TYPE_NAME,
	FIELD_SUPERVISOR_ROLE,
	FULL_ACCESS_USERS,
	ROLES_ALLOWED_TO_EDIT_USER_F2C_SCOPE,
	USER_ASSIGNED_FIELD_FIELDNAME,
	USER_SCOPE_AREAS_FIELDNAME,
)
from f2c.access.field_scope import get_user_scope_area_roots, user_bypasses_field_supervisor_restrictions


def _roles_from_user_doc(doc) -> list[str]:
	roles = []
	for r in doc.get("roles") or []:
		role = getattr(r, "role", None) or (r.get("role") if isinstance(r, dict) else None)
		if role:
			roles.append(role)
	return roles


def _scope_area_names_from_doc(doc) -> list[str]:
	"""Unique Geo Fencing Area names from the scope child table (in order)."""
	seen: set[str] = set()
	out: list[str] = []
	for row in doc.get(USER_SCOPE_AREAS_FIELDNAME) or []:
		a = (
			(getattr(row, "area", None) or (row.get("area") if isinstance(row, dict) else None) or "")
		).strip()
		if a and a not in seen:
			seen.add(a)
			out.append(a)
	return out


def _merged_scope_from_doc(doc) -> list[str]:
	merged = list(_scope_area_names_from_doc(doc))
	seen = set(merged)
	if frappe.db.has_column("User", USER_ASSIGNED_FIELD_FIELDNAME):
		legacy = (getattr(doc, USER_ASSIGNED_FIELD_FIELDNAME, None) or "").strip()
		if legacy and legacy not in seen:
			merged.append(legacy)
	return merged


def _session_user_may_edit_user_f2c_scope() -> bool:
	if user_bypasses_field_supervisor_restrictions(frappe.session.user):
		return True
	return bool(ROLES_ALLOWED_TO_EDIT_USER_F2C_SCOPE & frozenset(frappe.get_roles()))


def _geo_fencing_area_type_name(area_name: str) -> str | None:
	gft = frappe.db.get_value("Geo Fencing Area", area_name, "geo_fencing_type")
	if not gft:
		return None
	return (frappe.db.get_value("Geo Fencing Type", gft, "geo_fencing_type_name") or "").strip() or None


def validate(doc, method=None):
	"""F2C scope: privileged editors only when user has FS role; FS scope roots must be Field-level GFAs."""
	if (doc.name or "").strip() in FULL_ACCESS_USERS:
		return
	has_legacy = frappe.db.has_column("User", USER_ASSIGNED_FIELD_FIELDNAME)
	has_scope_cf = frappe.db.exists("Custom Field", {"dt": "User", "fieldname": USER_SCOPE_AREAS_FIELDNAME})
	if not has_legacy and not has_scope_cf:
		return

	roles = _roles_from_user_doc(doc)
	has_fs = FIELD_SUPERVISOR_ROLE in roles
	merged = _merged_scope_from_doc(doc)
	old_roots = () if doc.is_new() else get_user_scope_area_roots(doc.name)
	if has_fs and set(merged) != set(old_roots):
		if not _session_user_may_edit_user_f2c_scope():
			frappe.throw(
				"Only a System Manager or Administrator can assign or change F2C Scope Areas "
				f"for a user with role {FIELD_SUPERVISOR_ROLE!r}."
			)

	if not has_fs:
		return

	if not merged:
		frappe.throw(
			f"Users with role {FIELD_SUPERVISOR_ROLE!r} must have at least one Geo Fencing Area in "
			f"{USER_SCOPE_AREAS_FIELDNAME!r} (Field-level areas only)."
		)
	for val in merged:
		if not frappe.db.exists("Geo Fencing Area", val):
			frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
		tn = _geo_fencing_area_type_name(val)
		if tn != FIELD_LEVEL_GFA_TYPE_NAME:
			frappe.throw(
				f"Field Supervisor scope must be {FIELD_LEVEL_GFA_TYPE_NAME!r}-level Geo Fencing Areas only "
				f"({val!r} is type {tn!r})."
			)

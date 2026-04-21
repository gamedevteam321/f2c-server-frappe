# -*- coding: utf-8 -*-
"""User document hooks for F2C access fields."""

from __future__ import annotations

import frappe

from f2c.access.constants import (
	CLUSTER_LEVEL_GFA_TYPE_NAME,
	CLUSTER_SUPERVISOR_ROLE,
	FIELD_LEVEL_GFA_TYPE_NAME,
	FIELD_SUPERVISOR_ROLE,
	FULL_ACCESS_USERS,
	ROLES_ALLOWED_TO_EDIT_USER_F2C_SCOPE,
	USER_ASSIGNED_FIELD_FIELDNAME,
	USER_SCOPE_AREAS_FIELDNAME,
)
from f2c.access.field_scope import (
	get_user_scope_area_roots,
	user_bypasses_field_supervisor_restrictions,
)


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


def _scope_roots_for_validation(doc) -> list[str]:
	"""Use F2C Scope Areas child rows only when the form has any; else legacy single link (no merge)."""
	child = _scope_area_names_from_doc(doc)
	if child:
		return child
	if frappe.db.has_column("User", USER_ASSIGNED_FIELD_FIELDNAME):
		legacy = (getattr(doc, USER_ASSIGNED_FIELD_FIELDNAME, None) or "").strip()
		if legacy:
			return [legacy]
	return []


def _session_user_may_edit_user_f2c_scope() -> bool:
	if user_bypasses_field_supervisor_restrictions(frappe.session.user):
		return True
	return bool(ROLES_ALLOWED_TO_EDIT_USER_F2C_SCOPE & frozenset(frappe.get_roles()))


def _geo_fencing_area_type_name(area_name: str) -> str | None:
	gft = frappe.db.get_value("Geo Fencing Area", area_name, "geo_fencing_type")
	if not gft:
		return None
	return (frappe.db.get_value("Geo Fencing Type", gft, "geo_fencing_type_name") or "").strip() or None


def _scope_area_doc_label(area_id: str) -> str:
	"""Doc name + Area Name for validation messages (Name and label often differ)."""
	an = (frappe.db.get_value("Geo Fencing Area", area_id, "area_name") or "").strip()
	return f"{area_id!r} ({an})" if an else f"{area_id!r}"


def validate(doc, method=None):
	"""F2C scope: privileged editors when scope changes; FS roots = Field, CS roots = Cluster; roles mutually exclusive."""
	if (doc.name or "").strip() in FULL_ACCESS_USERS:
		return
	has_legacy = frappe.db.has_column("User", USER_ASSIGNED_FIELD_FIELDNAME)
	has_scope_cf = frappe.db.exists("Custom Field", {"dt": "User", "fieldname": USER_SCOPE_AREAS_FIELDNAME})
	if not has_legacy and not has_scope_cf:
		return

	roles = _roles_from_user_doc(doc)
	has_fs = FIELD_SUPERVISOR_ROLE in roles
	has_cs = CLUSTER_SUPERVISOR_ROLE in roles
	scope_roots = _scope_roots_for_validation(doc)
	old_effective = () if doc.is_new() else get_user_scope_area_roots(doc.name)

	if has_fs and has_cs:
		frappe.throw(
			f"A user cannot have both {FIELD_SUPERVISOR_ROLE!r} and {CLUSTER_SUPERVISOR_ROLE!r} roles."
		)

	# Same effective-scope rules as get_user_scope_area_roots (child rows win; else legacy).
	if (has_fs or has_cs) and set(scope_roots) != set(old_effective):
		if not _session_user_may_edit_user_f2c_scope():
			frappe.throw(
				"Only a System Manager or Administrator can assign or change F2C Scope Areas "
				f"for a user with role {FIELD_SUPERVISOR_ROLE!r} or {CLUSTER_SUPERVISOR_ROLE!r}."
			)

	if has_fs:
		if not scope_roots:
			frappe.throw(
				f"Users with role {FIELD_SUPERVISOR_ROLE!r} must have at least one Geo Fencing Area in "
				f"{USER_SCOPE_AREAS_FIELDNAME!r} (Field-level areas only)."
			)
		for val in scope_roots:
			if not frappe.db.exists("Geo Fencing Area", val):
				frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
			tn = _geo_fencing_area_type_name(val)
			if tn != FIELD_LEVEL_GFA_TYPE_NAME:
				frappe.throw(
					f"Field Supervisor scope must be {FIELD_LEVEL_GFA_TYPE_NAME!r}-level Geo Fencing Areas only "
					f"({_scope_area_doc_label(val)} is type {tn!r})."
				)
		return

	if has_cs:
		if not scope_roots:
			frappe.throw(
				f"Users with role {CLUSTER_SUPERVISOR_ROLE!r} must have at least one Geo Fencing Area in "
				f"{USER_SCOPE_AREAS_FIELDNAME!r} (Cluster-level areas only)."
			)
		for val in scope_roots:
			if not frappe.db.exists("Geo Fencing Area", val):
				frappe.throw(f"Scope area {val!r} is not a valid Geo Fencing Area.")
			tn = _geo_fencing_area_type_name(val)
			if tn != CLUSTER_LEVEL_GFA_TYPE_NAME:
				frappe.throw(
					f"Cluster Supervisor scope must use {CLUSTER_LEVEL_GFA_TYPE_NAME!r}-level Geo Fencing Areas only. "
					f"Linked {_scope_area_doc_label(val)} is type {tn!r}, not {CLUSTER_LEVEL_GFA_TYPE_NAME!r}. "
					"Open Geo Fencing Area and link the row whose Geo Fencing Type is Cluster (document Name is not the same as Area Name)."
				)

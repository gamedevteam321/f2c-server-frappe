# -*- coding: utf-8 -*-
"""Session / access context for React and other API clients."""

from __future__ import annotations

import frappe

from f2c.access.constants import FIELD_SUPERVISOR_ROLE, USER_ASSIGNED_FIELD_FIELDNAME, USER_SCOPE_AREAS_FIELDNAME
from f2c.access.field_scope import (
	allowed_warehouse_names_for_area_names,
	field_supervisor_data_scope_active,
	get_user_scope_area_roots,
	get_user_scope_expanded_area_names,
	user_has_field_supervisor_role,
)


@frappe.whitelist()
def get_access_context():
	"""Return roles and Field Supervisor flags for the current session user."""
	user = frappe.session.user
	roles = list(frappe.get_roles(user))
	scope_on = field_supervisor_data_scope_active(user)
	has_fs_role = user_has_field_supervisor_role(user)
	roots = get_user_scope_area_roots(user) if scope_on else tuple()
	expanded = get_user_scope_expanded_area_names(user) if scope_on else frozenset()
	warehouses = sorted(allowed_warehouse_names_for_area_names(expanded)) if expanded else []
	return {
		"user": user,
		"roles": roles,
		"is_field_supervisor": bool(scope_on),
		"has_field_supervisor_role": bool(has_fs_role),
		"field_supervisor_role": FIELD_SUPERVISOR_ROLE,
		"assigned_field": roots[0] if roots else None,
		"assigned_fields": list(roots),
		"assigned_area_names": sorted(expanded),
		"allowed_warehouse_names": warehouses,
		"execution_review_readonly": bool(scope_on),
		"inventory_review_readonly": bool(scope_on),
		"user_assigned_field_fieldname": USER_ASSIGNED_FIELD_FIELDNAME,
		"user_scope_areas_fieldname": USER_SCOPE_AREAS_FIELDNAME,
	}

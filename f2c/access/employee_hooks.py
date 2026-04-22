# -*- coding: utf-8 -*-
"""Employee hooks: Allowed Geo Areas vs linked User F2C roles."""

from __future__ import annotations

import frappe

from f2c.access.constants import EMPLOYEE_ALLOWED_GEO_FIELDNAME, EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME
from f2c.access.field_scope import get_employee_allowed_geo_roots_from_db
from f2c.access.geo_scope_validate import validate_allowed_geo_for_linked_user


def _scope_area_names_from_employee_doc(doc) -> list[str]:
	seen: set[str] = set()
	out: list[str] = []
	for row in doc.get(EMPLOYEE_ALLOWED_GEO_FIELDNAME) or []:
		a = (
			(getattr(row, EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME, None) or (row.get(EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME) if isinstance(row, dict) else None) or "")
		).strip()
		if a and a not in seen:
			seen.add(a)
			out.append(a)
	return out


def validate_employee_geo_scope(doc, method=None) -> None:
	if not frappe.get_meta("Employee").has_field(EMPLOYEE_ALLOWED_GEO_FIELDNAME):
		return
	uid = (getattr(doc, "user_id", None) or "").strip()
	if not uid:
		return
	new_roots = _scope_area_names_from_employee_doc(doc)
	old_roots = () if doc.is_new() else get_employee_allowed_geo_roots_from_db(doc.name)
	validate_allowed_geo_for_linked_user(new_roots, linked_user_id=uid, old_roots=old_roots)

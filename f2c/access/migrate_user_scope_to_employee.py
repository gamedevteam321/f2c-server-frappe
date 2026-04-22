# -*- coding: utf-8 -*-
"""
Copy User F2C scope (child table + optional legacy link) to Employee.allowed_geo_areas,
then remove the User custom field f2c_scope_areas.

Called from f2c.seed_defaults.after_migrate (after attendance_portal creates Employee fields).

Idempotent: safe to run multiple times.
"""
from __future__ import annotations

import frappe

from f2c.access.constants import (
	EMPLOYEE_ALLOWED_GEO_CHILD_DOCTYPE,
	EMPLOYEE_ALLOWED_GEO_FIELDNAME,
	EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME,
	LEGACY_USER_SCOPE_AREAS_FIELDNAME,
	LEGACY_USER_SCOPE_CHILD_DOCTYPE,
	USER_ASSIGNED_FIELD_FIELDNAME,
)


def _employee_has_allowed_geo_field() -> bool:
	try:
		return bool(frappe.get_meta("Employee").has_field(EMPLOYEE_ALLOWED_GEO_FIELDNAME))
	except Exception:
		return False


def _merge_geo_into_employee(employee_name: str, area_names: list[str]) -> None:
	if not area_names or not employee_name:
		return
	emp = frappe.get_doc("Employee", employee_name)
	existing: set[str] = set()
	for row in emp.get(EMPLOYEE_ALLOWED_GEO_FIELDNAME) or []:
		v = (getattr(row, EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME, None) or "").strip()
		if v:
			existing.add(v)
	changed = False
	for a in area_names:
		an = (a or "").strip()
		if not an or an in existing:
			continue
		if not frappe.db.exists("Geo Fencing Area", an):
			continue
		emp.append(EMPLOYEE_ALLOWED_GEO_FIELDNAME, {EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME: an})
		existing.add(an)
		changed = True
	if changed:
		emp.flags.ignore_permissions = True
		emp.save()


def run_migrate_user_scope_to_employee() -> None:
	if not _employee_has_allowed_geo_field():
		return

	skipped: list[str] = []
	child_table = f"tab{LEGACY_USER_SCOPE_CHILD_DOCTYPE}"

	if frappe.db.table_exists(child_table):
		users = frappe.db.sql(
			f"SELECT DISTINCT parent FROM `{child_table}` WHERE COALESCE(parent,'') != ''",
			as_list=True,
		)
		for (uname,) in users or []:
			user = (uname or "").strip()
			if not user:
				continue
			emp_name = frappe.db.get_value("Employee", {"user_id": user}, "name")
			if not emp_name:
				skipped.append(user)
				continue
			rows = frappe.get_all(
				LEGACY_USER_SCOPE_CHILD_DOCTYPE,
				filters={"parent": user, "parenttype": "User"},
				fields=["area"],
				order_by="idx asc",
				limit_page_length=0,
				ignore_permissions=True,
			)
			areas = [(r.get("area") or "").strip() for r in (rows or []) if (r.get("area") or "").strip()]
			if areas:
				_merge_geo_into_employee(emp_name, areas)

	if frappe.db.has_column("User", USER_ASSIGNED_FIELD_FIELDNAME):
		users_with_legacy = frappe.get_all(
			"User",
			filters={USER_ASSIGNED_FIELD_FIELDNAME: ["is", "set"]},
			pluck="name",
			limit_page_length=0,
		)
		for user in users_with_legacy or []:
			legacy = (frappe.db.get_value("User", user, USER_ASSIGNED_FIELD_FIELDNAME) or "").strip()
			if not legacy:
				continue
			emp_name = frappe.db.get_value("Employee", {"user_id": user}, "name")
			if not emp_name:
				continue
			if frappe.db.count(
				EMPLOYEE_ALLOWED_GEO_CHILD_DOCTYPE,
				{"parent": emp_name, "parenttype": "Employee"},
			):
				continue
			_merge_geo_into_employee(emp_name, [legacy])

	if skipped:
		frappe.log_error(
			"Users with User F2C Scope Area rows but no Employee(user_id): " + ", ".join(sorted(set(skipped))[:200]),
			"f2c migrate_user_f2c_scope_skipped_users",
		)

	cf_name = frappe.db.get_value(
		"Custom Field", {"dt": "User", "fieldname": LEGACY_USER_SCOPE_AREAS_FIELDNAME}, "name"
	)
	if cf_name:
		try:
			frappe.delete_doc("Custom Field", cf_name, ignore_permissions=True, force=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "f2c migrate delete User f2c_scope_areas Custom Field")

	if frappe.db.table_exists(child_table):
		try:
			frappe.db.sql(f"DELETE FROM `{child_table}`")
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "f2c migrate clear tabUser F2C Scope Area")

	frappe.clear_cache()

# -*- coding: utf-8 -*-
"""
Migration: Replace remarks with return_type on schedule and ODA equipment child tables.

- Backfill return_type = 'Non Returnable' where null (for existing rows before field existed).
- Drop remarks column from all 8 child tables if it exists.

Safe to run multiple times.
"""
import frappe


# Child table names (DocType names; Frappe table names have "tab" prefix and spaces)
EQUIPMENT_CHILD_DOCTYPES = [
	"Crop Plan Schedule Machinery",
	"Crop Plan Schedule Implement",
	"Crop Plan Schedule Hand Tool",
	"Crop Plan Schedule Other Tool",
	"On Demand Activity Machinery",
	"On Demand Activity Implement",
	"On Demand Activity Hand Tool",
	"On Demand Activity Other Tool",
	"Farm Task Execution Equipment",
	"Farm Task Execution Day Equipment",
]


def execute():
	for doctype in EQUIPMENT_CHILD_DOCTYPES:
		table = frappe.utils.get_table_name(doctype)
		if not table:
			continue
		# has_column expects DocType name, not table name
		if not frappe.db.table_exists(doctype):
			continue
		# Backfill return_type where null (for rows created before the field existed)
		if frappe.db.has_column(doctype, "return_type"):
			frappe.db.sql(
				f"UPDATE `{table}` SET return_type = 'Non Returnable' WHERE return_type IS NULL OR return_type = ''"
			)
		# Drop remarks column if it exists (use sql_ddl for DDL to avoid ImplicitCommitError)
		if frappe.db.has_column(doctype, "remarks"):
			frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN remarks")
	frappe.db.commit()

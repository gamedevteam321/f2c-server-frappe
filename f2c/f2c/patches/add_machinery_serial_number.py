# -*- coding: utf-8 -*-
"""
Add Machinery.serial_number for non-vehicle equipment (vehicle types use chassis_number + engine_registration_number).
Safe to run multiple times.
"""
import frappe


def execute():
    if not frappe.db.exists("DocType", "Machinery"):
        return

    columns = [
        c.get("Field") or c.get("field") or c.get("name")
        for c in frappe.db.sql("SHOW COLUMNS FROM `tabMachinery`", as_dict=True)
    ]

    if "serial_number" not in columns:
        frappe.db.sql("ALTER TABLE `tabMachinery` ADD COLUMN `serial_number` TEXT")
        frappe.db.commit()

    frappe.clear_cache()

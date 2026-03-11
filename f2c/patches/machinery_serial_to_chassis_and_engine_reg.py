# -*- coding: utf-8 -*-
"""
Rename Machinery.serial_number -> chassis_number and add engine_registration_number.
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

    if "serial_number" in columns and "chassis_number" not in columns:
        frappe.db.sql("ALTER TABLE `tabMachinery` CHANGE COLUMN `serial_number` `chassis_number` TEXT")
        frappe.db.commit()

    if "engine_registration_number" not in columns:
        frappe.db.sql("ALTER TABLE `tabMachinery` ADD COLUMN `engine_registration_number` TEXT")
        frappe.db.commit()

    frappe.clear_cache()

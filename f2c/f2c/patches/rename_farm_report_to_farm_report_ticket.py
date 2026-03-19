# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

"""
Rename DocType "Farm Report" to "Farm Report Ticket":
- Rename table tabFarm Report to tabFarm Report Ticket
- Update DocType document name in tabDocType
- Update child table rows (parenttype) for Farm Report Equipment and Farm Report Labour
"""

import frappe


def execute():
	if not frappe.db.table_exists("tabFarm Report"):
		return

	# 1. Rename table (Frappe rename_doc for DocType does not rename the table in v14)
	if not frappe.db.table_exists("tabFarm Report Ticket"):
		frappe.db.sql("RENAME TABLE `tabFarm Report` TO `tabFarm Report Ticket`")
		frappe.db.commit()

	# 2. Update DocType document name so Frappe uses table "tabFarm Report Ticket"
	# (We already renamed the table; rename_doc would try to rename it again and fail.)
	frappe.db.sql("UPDATE tabDocType SET name = 'Farm Report Ticket' WHERE name = 'Farm Report'")
	frappe.db.commit()
	frappe.cache().delete_value("doctype_map")
	frappe.cache().delete_value("doctype_name_map")

	# 3. Update child table parenttype
	for child_table in ("Farm Report Equipment", "Farm Report Labour"):
		if frappe.db.table_exists("tab" + child_table):
			frappe.db.sql(
				"UPDATE `tab%s` SET parenttype = 'Farm Report Ticket' WHERE parenttype = 'Farm Report'" % child_table
			)
	frappe.db.commit()

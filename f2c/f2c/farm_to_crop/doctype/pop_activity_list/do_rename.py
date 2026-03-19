import frappe

frappe.connect()
frappe.rename_doc("DocType", "POP-Activity List", "Farm Crop Activity Mapping", force=True)
frappe.db.commit()
print("✓ Successfully renamed doctype")


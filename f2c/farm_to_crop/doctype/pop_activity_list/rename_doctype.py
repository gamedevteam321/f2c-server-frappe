import frappe

old_name = "POP-Activity List"
new_name = "Farm Crop Activity Mapping"

print(f"Renaming '{old_name}' to '{new_name}'...")

try:
    frappe.rename_doc("DocType", old_name, new_name, force=True)
    frappe.db.commit()
    print(f"✓ Successfully renamed doctype from '{old_name}' to '{new_name}'")
    print("\nThis automatically updated:")
    print("  - Database table name")
    print("  - All Link field references in other doctypes")
    print("  - All foreign key constraints")
except Exception as e:
    print(f"✗ Error: {str(e)}")
    frappe.db.rollback()


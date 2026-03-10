import frappe

from f2c.farm_to_crop.doctype.water_source.seed_data import seed_water_source


def ensure_irrigation_types() -> None:
	"""Create default Irrigation Type master records if they don't exist.

	Idempotent: safe to run multiple times.
	"""
	# Only bootstrap defaults when the master is empty.
	# This avoids re-creating records that an admin intentionally removed.
	if frappe.db.count("Irrigation Type") > 0:
		return

	defaults = ["Drip", "Sprinkler", "Flood", "Other"]

	for name in defaults:
		frappe.get_doc(
			{
				"doctype": "Irrigation Type",
				"irrigation_type_name": name,
			}
		).insert(ignore_permissions=True)


def ensure_item_group_default_gst_hsn_code_field() -> None:
	"""Add Custom Field on Item Group for default GST HSN Code (India Compliance). Idempotent."""
	if not frappe.db.table_exists("GST HSN Code"):
		return
	if frappe.db.exists("Custom Field", {"dt": "Item Group", "fieldname": "default_gst_hsn_code"}):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Item Group",
				"fieldname": "default_gst_hsn_code",
				"label": "Default GST HSN Code",
				"fieldtype": "Link",
				"options": "GST HSN Code",
				"insert_after": "parent_item_group",
				"description": "Default HSN/SAC code for Items in this group (used when creating fixed-asset Items).",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_item_group_default_gst_hsn_code_field failed")


def after_migrate() -> None:
	"""Hook: run after `bench migrate`."""
	try:
		ensure_irrigation_types()
		seed_water_source()
		ensure_item_group_default_gst_hsn_code_field()
	except Exception:
		# Never block migrations due to seed failures
		frappe.log_error(frappe.get_traceback(), "f2c.after_migrate seed_defaults failed")


def after_install() -> None:
	"""Hook: run after app installation."""
	after_migrate()



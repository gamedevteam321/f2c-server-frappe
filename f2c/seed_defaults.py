import frappe


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


def after_migrate() -> None:
	"""Hook: run after `bench migrate`."""
	try:
		ensure_irrigation_types()
	except Exception:
		# Never block migrations due to seed failures
		frappe.log_error(frappe.get_traceback(), "f2c.after_migrate seed_defaults failed")


def after_install() -> None:
	"""Hook: run after app installation."""
	after_migrate()



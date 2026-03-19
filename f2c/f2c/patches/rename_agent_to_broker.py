import frappe


def execute():
	"""
	Rename DocType Agent -> Broker (once), preserving data.

	This patch is safe to run multiple times.
	"""
	if frappe.db.exists("DocType", "Broker"):
		return
	if not frappe.db.exists("DocType", "Agent"):
		return

	# Rename the DocType (renames table + related metadata)
	frappe.rename_doc("DocType", "Agent", "Broker", force=True)
	frappe.clear_cache()


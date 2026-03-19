# Script to backfill check-in and check-out times for Farm Task Execution Day labour rows (for testing).
# Run from bench root:
#   bench --site <your-site> execute f2c.scripts.fill_labour_checkin_times.run
# Or for a specific execution:
#   bench --site <your-site> execute f2c.scripts.fill_labour_checkin_times.run --args '["FTE-00001"]'
# Or from bench console:
#   from f2c.scripts.fill_labour_checkin_times import run; run()  # all days
#   from f2c.scripts.fill_labour_checkin_times import run; run(execution_name="FTE-00001")

import frappe
from frappe.utils import get_datetime


def run(execution_name=None):
	"""
	Fill checkin_in_time (09:00) and checkin_out_time (17:00) for all Farm Task Execution Day
	labour rows that are missing them. Optional execution_name to limit to one execution.
	Returns count of day docs updated.
	"""
	filters = {}
	if execution_name:
		filters["execution"] = execution_name
	day_names = frappe.get_all(
		"Farm Task Execution Day",
		filters=filters,
		fields=["name", "date"],
		order_by="date asc",
	)
	if not day_names:
		print("No Farm Task Execution Day records found.")
		return 0
	updated_count = 0
	for d in day_names:
		date_str = d.get("date")
		if not date_str:
			continue
		date_str = str(date_str)[:10]
		checkin_in_time = get_datetime(f"{date_str} 09:00:00")
		checkin_out_time = get_datetime(f"{date_str} 17:00:00")
		day_doc = frappe.get_doc("Farm Task Execution Day", d["name"])
		changed = False
		for row in day_doc.labour or []:
			if not row.get("checkin_in_time"):
				row.checkin_in_time = checkin_in_time
				changed = True
			if not row.get("checkin_out_time"):
				row.checkin_out_time = checkin_out_time
				changed = True
		if changed:
			day_doc.save(ignore_permissions=True)
			updated_count += 1
	frappe.db.commit()
	print(f"Updated {updated_count} day(s). Total days scanned: {len(day_names)}.")
	return updated_count

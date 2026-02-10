# Copyright (c) 2025, Orgatek and contributors
# Sync check-in from HR Attendance Log to Farm Worker Attendance for employees linked to farm workers.

import frappe


@frappe.whitelist()
def sync_attendance_log_to_farm_worker(attendance_date: str, employee_ids: list = None):
	"""
	For given employees (default hr-emp-00011, hr-emp-00013), find linked Farm Worker Details,
	read Attendance Log check-in for that date, and create/update Farm Worker Attendance
	with check_in time and status Present (punch-in).

	:param attendance_date: Date in YYYY-MM-DD
	:param employee_ids: Optional list of Employee IDs. Defaults to ["hr-emp-00011", "hr-emp-00013"]
	:return: dict with synced count and details
	"""
	if employee_ids is None:
		employee_ids = ["hr-emp-00011", "hr-emp-00013"]
	if isinstance(employee_ids, str):
		import json
		employee_ids = json.loads(employee_ids) if employee_ids else []

	synced = []
	skipped_no_worker = []
	skipped_no_log = []

	for employee_id in employee_ids:
		if not employee_id:
			continue
		# Farm workers linked to this employee
		farm_worker_names = frappe.get_all(
			"Farm Worker Details",
			filters={"employee": employee_id},
			pluck="name"
		)
		if not farm_worker_names:
			skipped_no_worker.append(employee_id)
			continue

		# Attendance Log for this employee and date (from HR Attendance Portal)
		log = frappe.db.get_value(
			"Attendance Log",
			{"employee": employee_id, "attendance_date": attendance_date},
			["name", "check_in"],
			as_dict=True
		)
		if not log or not log.get("check_in"):
			skipped_no_log.append(employee_id)
			continue

		check_in = log.get("check_in")
		for farm_worker in farm_worker_names:
			existing = frappe.db.get_value(
				"Farm Worker Attendance",
				{"farm_worker": farm_worker, "attendance_date": attendance_date},
				"name"
			)
			if existing:
				doc = frappe.get_doc("Farm Worker Attendance", existing)
				doc.check_in = check_in
				doc.status = "Present"
				doc.flags.ignore_validate = False
				doc.save()
				synced.append({"farm_worker": farm_worker, "employee": employee_id, "action": "updated", "check_in": str(check_in)})
			else:
				doc = frappe.get_doc({
					"doctype": "Farm Worker Attendance",
					"farm_worker": farm_worker,
					"attendance_date": attendance_date,
					"check_in": check_in,
					"status": "Present"
				})
				doc.insert()
				synced.append({"farm_worker": farm_worker, "employee": employee_id, "action": "created", "check_in": str(check_in)})
	frappe.db.commit()

	return {
		"synced_count": len(synced),
		"synced": synced,
		"skipped_no_farm_worker": skipped_no_worker,
		"skipped_no_attendance_log": skipped_no_log
	}

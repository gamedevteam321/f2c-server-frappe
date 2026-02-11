# Copyright (c) 2025, Orgatek and contributors
# Sync check-in from HR Attendance Log to Farm Worker Attendance for employees linked to farm workers.

import frappe
from frappe.utils import cint


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


@frappe.whitelist()
def auto_link_farm_workers_from_employees(attendance_date: str, employee_ids=None, create_if_missing: int = 0):
	"""
	Auto-link punched-in Employees to existing Farm Worker Details records.

	This runs safely/idempotently:
	- If a Farm Worker Details already has employee=<employee_id>, we skip.
	- We only link when we can find EXACTLY ONE Farm Worker Details record with:
		- employee is empty
		- worker_name matches Employee.employee_name (trimmed, case-insensitive)

	This is intended to be called from Execution labour selection so it "happens only once".
	Subsequent calls won't change anything once linked.

	If create_if_missing is truthy:
	- When there is NO match in Farm Worker Details for the employee name, we will CREATE a new
	  Farm Worker Details record and link it to the employee.
	- Creation uses best-effort defaults (first Farm Project, first Farm Geo Fencing Area, first Contractor).
	  This is still idempotent because once created/linked, subsequent runs will skip.
	"""
	import json

	attendance_date = (attendance_date or "").strip()
	if not attendance_date:
		frappe.throw("attendance_date is required")

	if isinstance(employee_ids, str):
		employee_ids = json.loads(employee_ids) if employee_ids else []
	if employee_ids is None:
		employee_ids = []
	if not isinstance(employee_ids, (list, tuple)):
		employee_ids = [employee_ids]

	# If no explicit list passed, derive from Attendance Log (punched-in only)
	if not employee_ids:
		rows = frappe.get_all(
			"Attendance Log",
			filters={"attendance_date": attendance_date},
			fields=["employee", "check_in"],
			limit=2000,
			ignore_permissions=True,
		)
		employee_ids = [
			r.get("employee")
			for r in (rows or [])
			if r.get("employee") and r.get("check_in")
		]

	# De-dupe and clean
	employee_ids = list(dict.fromkeys([str(e).strip() for e in (employee_ids or []) if str(e).strip()]))
	if not employee_ids:
		return {"linked_count": 0, "linked": [], "skipped": []}

	# Fetch employee names
	emp_rows = frappe.get_all(
		"Employee",
		filters={"name": ["in", employee_ids]},
		fields=["name", "employee_name", "gender", "date_of_birth"],
		limit=2000,
		ignore_permissions=True,
	)
	emp_by_id = {e.get("name"): e for e in (emp_rows or [])}

	linked = []
	created = []
	skipped = []

	# Defaults for creating Farm Worker Details (only computed if needed)
	default_farm_project = None
	default_farm = None
	default_contractor = None

	for emp_id in employee_ids:
		emp_id = (emp_id or "").strip()
		if not emp_id:
			continue

		# Already linked somewhere?
		existing_fw = frappe.db.get_value("Farm Worker Details", {"employee": emp_id}, "name")
		if existing_fw:
			skipped.append({"employee": emp_id, "reason": "already_linked", "farm_worker": existing_fw})
			continue

		emp = emp_by_id.get(emp_id) or {}
		emp_name = (emp.get("employee_name") or "").strip()
		if not emp_name:
			skipped.append({"employee": emp_id, "reason": "missing_employee_name"})
			continue

		# Find candidates by case-insensitive exact match on worker_name, only where employee is empty
		# Use SQL for case-insensitive match
		candidates = frappe.db.sql(
			"""
			SELECT name
			FROM `tabFarm Worker Details`
			WHERE (employee IS NULL OR employee = '')
			  AND LOWER(TRIM(worker_name)) = LOWER(TRIM(%s))
			LIMIT 5
			""",
			(emp_name,),
			as_dict=True,
		) or []

		if len(candidates) != 1:
			# If requested, create when there is NO match at all (safe, non-ambiguous).
			if cint(create_if_missing) and len(candidates) == 0:
				try:
					# Resolve defaults lazily (only once)
					if default_farm_project is None:
						default_farm_project = frappe.db.get_value("Farm Project", {}, "name")
					if default_farm is None:
						default_farm = frappe.db.get_value("Geo Fencing Area", {"geo_fencing_type": "Farm"}, "name")
					if default_contractor is None:
						default_contractor = frappe.db.get_value("Farm Worker Contractor", {}, "name")

					if not (default_farm_project and default_farm and default_contractor):
						skipped.append({
							"employee": emp_id,
							"employee_name": emp_name,
							"reason": "missing_defaults_for_create",
							"default_farm_project": default_farm_project,
							"default_farm": default_farm,
							"default_contractor": default_contractor,
						})
						continue

					gender = (emp.get("gender") or "").strip() or "Other"
					# Farm Worker Details uses `dob` fieldname; Employee typically has date_of_birth
					dob = emp.get("date_of_birth") or "2000-01-01"

					doc = frappe.new_doc("Farm Worker Details")
					doc.worker_name = emp_name
					# Aadhaar is mandatory+unique in Farm Worker Details; use employee id as a stable unique placeholder
					doc.aadhaar_number = emp_id
					doc.dob = dob
					doc.gender = gender
					doc.farm_project = default_farm_project
					doc.farm = default_farm
					doc.daily_wage_amount = 0
					doc.contractor = default_contractor
					doc.employee = emp_id
					doc.insert(ignore_permissions=True)
					created.append({"employee": emp_id, "employee_name": emp_name, "farm_worker": doc.name})
					linked.append({"employee": emp_id, "employee_name": emp_name, "farm_worker": doc.name, "created": 1})
					continue
				except Exception as e:
					skipped.append({"employee": emp_id, "employee_name": emp_name, "reason": f"create_error:{str(e)}"})
					continue

			# Otherwise skip (ambiguous or no match + create disabled)
			skipped.append({
				"employee": emp_id,
				"employee_name": emp_name,
				"reason": "no_unique_match",
				"matches": [c.get("name") for c in candidates],
			})
			continue

		fw_name = candidates[0].get("name")
		if not fw_name:
			skipped.append({"employee": emp_id, "employee_name": emp_name, "reason": "invalid_match"})
			continue

		try:
			# Set link (minimal write)
			frappe.db.set_value("Farm Worker Details", fw_name, "employee", emp_id, update_modified=False)
			linked.append({"employee": emp_id, "employee_name": emp_name, "farm_worker": fw_name})
		except Exception as e:
			skipped.append({"employee": emp_id, "employee_name": emp_name, "reason": f"error:{str(e)}"})

	if linked:
		frappe.db.commit()

	return {"linked_count": len(linked), "created_count": len(created), "linked": linked, "created": created, "skipped": skipped}

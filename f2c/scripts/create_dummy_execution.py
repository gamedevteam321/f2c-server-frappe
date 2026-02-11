# Script to create a dummy Farm Task Execution (like Execution Details: In Progress, Day 1, etc.).
# Run from bench root:
#   bench --site <your-site> execute f2c.scripts.create_dummy_execution.run
# Or with an optional schedule name: bench --site <your-site> execute f2c.scripts.create_dummy_execution.run --args '["CPS-0123"]'
#
# If a schedule without execution_ref exists: creates new FTE, starts it, sets Actual Start, creates all days.
# If all schedules already have an execution: uses first In Progress/On Hold execution and creates all days for it.

# bench --site localhost execute f2c.scripts.create_dummy_execution.run --args '["CPS-0123"]'

import frappe
from frappe.utils import get_datetime, getdate, add_days, flt


def _build_dummy_sections_from_fte(fte, date_str, checkin_time_str=None):
	"""Build inputs, equipment, labour payloads from FTE for update_day_data. Labour gets checkin_in_time for this day."""
	# Check-in time for labour: day date at 15:31 (naive string for API)
	if checkin_time_str is None:
		checkin_time_str = f"{date_str} 15:31:00"

	def _g(row, key, default=None):
		return getattr(row, key, None) if hasattr(row, key) else (row.get(key) if isinstance(row, dict) else default)

	inputs = []
	for row in (fte.get("inputs") or []):
		inputs.append({
			"item": _g(row, "item"),
			"item_name": _g(row, "item_name"),
			"uom": _g(row, "uom"),
			"rate_qty": flt(_g(row, "rate_qty"), 3),
			"planned_qty": flt(_g(row, "planned_qty"), 3),
			"issued_qty": flt(_g(row, "issued_qty"), 3) or 0,
			"returned_qty": flt(_g(row, "returned_qty"), 3) or 0,
			"consumed_qty": flt(_g(row, "consumed_qty"), 3) or 0,
		})

	equipment = []
	for row in (fte.get("equipment") or []):
		equipment.append({
			"asset": _g(row, "asset"),
			"asset_name": _g(row, "asset_name"),
			"planned_hours": flt(_g(row, "planned_hours"), 2) or 1,
			"actual_hours": flt(_g(row, "actual_hours"), 2) or 0,
			"remarks": _g(row, "remarks") or "",
		})

	labour = []
	for row in (fte.get("labour_attendance") or []):
		labour.append({
			"labour": _g(row, "labour"),
			"labour_name": _g(row, "labour_name"),
			"role": _g(row, "role"),
			"checkin_in_time": checkin_time_str,
			"checkin_out_time": None,
		})

	# If FTE has no labour, use first Farm Worker Details so the day has at least one labour row
	if not labour and frappe.db.table_exists("Farm Worker Details"):
		workers = frappe.get_all("Farm Worker Details", fields=["name", "worker_name"], limit=1)
		if workers:
			labour = [{
				"labour": workers[0].name,
				"labour_name": workers[0].get("worker_name"),
				"role": "",
				"checkin_in_time": checkin_time_str,
				"checkin_out_time": None,
			}]

	return inputs, equipment, labour


def _fill_all_days_sections(execution_name, schedule_name):
	"""Populate Inputs, Equipment, Labour for every day from FTE data."""
	from f2c.farm_execution.doctype.farm_task_execution.farm_task_execution import update_day_data

	fte = frappe.get_doc("Farm Task Execution", execution_name)
	schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
	planned_start = schedule.get("planned_start")
	planned_end = schedule.get("planned_end")

	if not planned_start or not planned_end:
		date_str = "2026-02-09"
		inputs, equipment, labour = _build_dummy_sections_from_fte(fte, date_str)
		update_day_data(execution_name, date_str, inputs=inputs, equipment=equipment, labour=labour)
		print(f"Filled Inputs/Equipment/Labour for day {date_str}.")
		frappe.db.commit()
		return

	start_date = getdate(planned_start)
	end_date = getdate(planned_end)
	if end_date < start_date:
		end_date = start_date

	dt = start_date
	while dt <= end_date:
		date_str = dt.strftime("%Y-%m-%d")
		checkin_time_str = f"{date_str} 15:31:00"
		inputs, equipment, labour = _build_dummy_sections_from_fte(fte, date_str, checkin_time_str=checkin_time_str)
		update_day_data(execution_name, date_str, inputs=inputs, equipment=equipment, labour=labour)
		print(f"Filled Inputs/Equipment/Labour for day {date_str}.")
		dt = add_days(dt, 1)

	frappe.db.commit()
	num_days = (end_date - start_date).days + 1
	print(f"Filled dummy Inputs, Equipment, Labour for all {num_days} day(s).")


def _create_all_days_for_execution(execution_name, schedule_name):
	"""Create Farm Task Execution Day for each date from schedule planned_start to planned_end."""
	from f2c.farm_execution.doctype.farm_task_execution.farm_task_execution import get_or_create_current_day

	schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
	planned_start = schedule.get("planned_start")
	planned_end = schedule.get("planned_end")
	if planned_start and planned_end:
		start_date = getdate(planned_start)
		end_date = getdate(planned_end)
		if end_date < start_date:
			end_date = start_date
		created_days = []
		dt = start_date
		while dt <= end_date:
			date_str = dt.strftime("%Y-%m-%d")
			result = get_or_create_current_day(execution_name, date_str)
			day_name = result.get("name") if isinstance(result, dict) else result
			created_days.append((date_str, day_name))
			print(f"Day {date_str} created/ensured: {day_name}.")
			dt = add_days(dt, 1)
		print(f"Created/ensured {len(created_days)} day(s) from {start_date} to {end_date}.")
		return
	# No planned range: create only Day 1 (9 Feb)
	day_date = "2026-02-09"
	result = get_or_create_current_day(execution_name, day_date)
	day_name = result.get("name") if isinstance(result, dict) else result
	print(f"Day 1 (9 Feb) created/ensured: {day_name} (schedule has no planned_start/planned_end).")


def run(schedule_name=None):
	"""
	Ensure a dummy execution with all days: either create new FTE from a free schedule,
	or add all days to an existing In Progress/On Hold execution.
	"""
	# Schedules that have no execution_ref (can create new FTE)
	schedules_no_exec = frappe.get_all(
		"Crop Plan Schedule",
		filters={"execution_ref": ["in", ["", None]]},
		fields=["name", "field", "activity_name"],
		limit=50,
	)
	# Schedules that already have an execution (fallback: add days to that execution)
	schedules_with_exec = []
	if not schedules_no_exec:
		all_with_ref = frappe.get_all(
			"Crop Plan Schedule",
			fields=["name", "execution_ref", "field", "activity_name", "planned_start", "planned_end"],
			limit=100,
		)
		schedules_with_exec = [s for s in all_with_ref if (s.get("execution_ref") or "").strip()]

	if not schedules_no_exec and not schedules_with_exec:
		print("No Crop Plan Schedule found. Create a schedule in the app first.")
		return None

	from f2c.farm_execution.doctype.farm_task_execution.farm_task_execution import (
		create_from_schedule,
		start_execution,
		get_or_create_current_day,
	)

	if schedules_no_exec:
		# Prefer Spraying / sbt-f-1001
		preferred = [
			s for s in schedules_no_exec
			if (s.get("activity_name") or "").strip().lower() == "spraying"
			and (s.get("field") or "") == "sbt-f-1001"
		]
		candidate_names = [s["name"] for s in (preferred or schedules_no_exec)]
		if schedule_name and schedule_name not in candidate_names:
			if frappe.db.exists("Crop Plan Schedule", schedule_name):
				exec_ref = frappe.db.get_value("Crop Plan Schedule", schedule_name, "execution_ref")
				print(f"Schedule '{schedule_name}' already has execution {exec_ref}. Run without --args to use first available.")
				return None
			schedule_name = candidate_names[0]
			print(f"Schedule from --args not found; using first available: {schedule_name}")
		elif not schedule_name:
			schedule_name = candidate_names[0]

		print(f"Using schedule (no execution yet): {schedule_name}")
		execution_name = create_from_schedule(schedule_name)
		print(f"Created execution: {execution_name}")
		start_execution(execution_name)
		print("Started execution (In Progress).")
		doc = frappe.get_doc("Farm Task Execution", execution_name)
		actual_start = get_datetime("2026-02-09 15:31:00")
		if getattr(actual_start, "tzinfo", None):
			from frappe.utils import convert_utc_to_system_timezone
			actual_start = convert_utc_to_system_timezone(actual_start).replace(tzinfo=None)
		doc.actual_start = actual_start
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		print("Set Actual Start to 2026-02-09 15:31:00.")
		_create_all_days_for_execution(execution_name, schedule_name)
		_fill_all_days_sections(execution_name, schedule_name)
		print(f"\nDone. Execution: {execution_name} (Status: In Progress, all days + Inputs/Equipment/Labour).")
		return execution_name

	# Fallback: all schedules have an execution — use first one that is In Progress or On Hold
	for s in schedules_with_exec:
		exec_ref = (s.get("execution_ref") or "").strip()
		if not exec_ref or not frappe.db.exists("Farm Task Execution", exec_ref):
			continue
		status = frappe.db.get_value("Farm Task Execution", exec_ref, "status")
		if status not in ("In Progress", "On Hold"):
			continue
		schedule_name = s["name"]
		execution_name = exec_ref
		print(f"No schedule without execution. Using existing execution: {execution_name} (schedule: {schedule_name}).")
		_create_all_days_for_execution(execution_name, schedule_name)
		_fill_all_days_sections(execution_name, schedule_name)
		print(f"\nDone. All days + Inputs/Equipment/Labour for execution: {execution_name}.")
		return execution_name

	print("No Crop Plan Schedule without execution, and no In Progress/On Hold execution found. Start an execution in the app first.")
	return None

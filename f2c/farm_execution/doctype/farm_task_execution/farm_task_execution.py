# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import compute_total_qty


def _insert_with_retry(exec_doc, max_retries=3):
	"""
	Insert document with retry logic to handle naming series deadlock.
	"""
	import time
	for attempt in range(max_retries):
		try:
			# Commit any pending transactions to ensure clean state
			frappe.db.commit()
			exec_doc.insert(ignore_permissions=True)
			return exec_doc.name
		except frappe.QueryDeadlockError:
			if attempt < max_retries - 1:
				frappe.db.rollback()
				time.sleep(0.1 * (attempt + 1))  # Exponential backoff
				# Clear the name so it can be regenerated on retry
				if hasattr(exec_doc, 'name'):
					exec_doc.name = None
			else:
				frappe.db.rollback()
				raise
	return exec_doc.name


class FarmTaskExecution(Document):
	def validate(self):
		self._validate_reference()
		self._validate_status_rules()
		self._compute_consumed_qty()

	def on_trash(self):
		"""Prevent deletion if linked to schedule or on-demand activity."""
		self._check_linked_schedule()
		self._check_linked_on_demand_activity()

	def on_cancel(self):
		"""Prevent cancellation if linked to schedule or on-demand activity."""
		self._check_linked_schedule()
		self._check_linked_on_demand_activity()

	def on_update(self):
		"""Update linked Crop Plan Schedule status when execution is completed."""
		if self.status == "Completed" and self.schedule_ref and self.has_value_changed("status"):
			# Update the linked Crop Plan Schedule status to Completed
			schedule_status = frappe.db.get_value("Crop Plan Schedule", self.schedule_ref, "status")
			# Only update if schedule is not already in a terminal state
			if schedule_status and schedule_status not in ("Aborted", "Completed", "Rescheduled"):
				frappe.db.set_value("Crop Plan Schedule", self.schedule_ref, "status", "Completed", update_modified=False)

	def _check_linked_schedule(self):
		"""Check if this execution is linked to a Crop Plan Schedule."""
		if self.schedule_ref:
			schedule_name = self.schedule_ref
			# Check if schedule exists and get its status
			schedule_status = frappe.db.get_value("Crop Plan Schedule", schedule_name, "status")
			if schedule_status:
				# If schedule is not in a terminal state, prevent deletion/cancellation
				if schedule_status not in ("Aborted", "Completed", "Rescheduled"):
					frappe.throw(
						f"Cannot delete or cancel because Farm Task Execution <b>{self.name}</b> is linked with Crop Plan Schedule <b>{schedule_name}</b> which is {schedule_status}. Please abort or complete the schedule first."
					)
				# If schedule is in terminal state, allow deletion/cancellation
				# but clear the bidirectional link to maintain data integrity
				if schedule_status in ("Aborted", "Completed", "Rescheduled"):
					# Clear execution_ref on the schedule to break the link
					frappe.db.set_value("Crop Plan Schedule", schedule_name, "execution_ref", None, update_modified=False)
					# Clear schedule_ref on this execution (will be cleared on deletion anyway, but good for cancellation)
					self.schedule_ref = None

	def _check_linked_on_demand_activity(self):
		"""Check if this execution is linked to an On Demand Activity."""
		if self.on_demand_activity_ref:
			activity_name = self.on_demand_activity_ref
			# Check if activity exists and get its status
			activity_status = frappe.db.get_value("On Demand Activity", activity_name, "status")
			if activity_status:
				# If activity is not in a terminal state, prevent deletion/cancellation
				if activity_status not in ("Aborted", "Completed"):
					frappe.throw(
						f"Cannot delete or cancel because Farm Task Execution <b>{self.name}</b> is linked with On Demand Activity <b>{activity_name}</b> which is {activity_status}. Please abort or complete the activity first."
					)
				# If activity is in terminal state, allow deletion/cancellation
				# but clear the bidirectional link to maintain data integrity
				if activity_status in ("Aborted", "Completed"):
					# Clear execution_ref on the activity to break the link
					frappe.db.set_value("On Demand Activity", activity_name, "execution_ref", None, update_modified=False)
					# Clear on_demand_activity_ref on this execution (will be cleared on deletion anyway, but good for cancellation)
					self.on_demand_activity_ref = None

	def _validate_reference(self):
		"""Ensure either schedule_ref or on_demand_activity_ref is provided, but not both."""
		if not self.schedule_ref and not self.on_demand_activity_ref:
			frappe.throw("Either Schedule or On Demand Activity must be provided.")
		if self.schedule_ref and self.on_demand_activity_ref:
			frappe.throw("Cannot have both Schedule and On Demand Activity. Please provide only one.")
		
		# Auto-populate field, crop_plan, and is_spray based on reference
		if self.schedule_ref:
			schedule = frappe.get_doc("Crop Plan Schedule", self.schedule_ref)
			if not self.field:
				self.field = schedule.field
			if not self.crop_plan:
				self.crop_plan = schedule.crop_plan
			if not hasattr(self, 'is_spray') or self.is_spray is None:
				self.is_spray = schedule.is_spray or 0
			if not self.planned_spray_water_liters:
				self.planned_spray_water_liters = flt(schedule.water_to_be_used_liters or 0, 3)
		elif self.on_demand_activity_ref:
			activity = frappe.get_doc("On Demand Activity", self.on_demand_activity_ref)
			if not self.field:
				self.field = activity.field
			# On-demand activities don't have crop_plan, so leave it blank
			if not hasattr(self, 'is_spray') or self.is_spray is None:
				self.is_spray = activity.is_spray or 0
			if not self.planned_spray_water_liters:
				self.planned_spray_water_liters = flt(activity.water_to_be_used_liters or 0, 3)

	def _validate_status_rules(self):
		if self.status == "Started" and not self.actual_start:
			self.actual_start = now_datetime()

		if self.status in ("Completed", "Aborted") and not self.actual_end:
			self.actual_end = now_datetime()

		if self.is_spray and self.status in ("Completed", "Aborted"):
			if flt(self.actual_spray_water_liters) <= 0:
				frappe.throw("Actual Spray Water (Liters) is required for Spray activities.")

		if self.status == "Aborted":
			if not (self.abort_category or "").strip() or not (self.abort_reason or "").strip():
				frappe.throw("Abort Category and Abort Reason are required when status is Aborted.")

	def _compute_consumed_qty(self):
		for row in self.get("inputs") or []:
			row.consumed_qty = flt(flt(row.issued_qty) - flt(row.returned_qty), 3)


@frappe.whitelist()
def create_from_schedule(schedule_name: str, labour_list: str = None) -> str:
	"""
	Create a Farm Task Execution document from a Crop Plan Schedule.
	Also writes back execution_ref on the schedule.
	Uses database-level checks and transactions to prevent duplicate creation.
	
	Args:
		schedule_name: Name of the Crop Plan Schedule
		labour_list: JSON string of list of farm worker names (from Farm Worker Details) to add to labour_attendance
	"""
	# First, check if execution already exists (fast path)
	existing_execution = frappe.db.get_value(
		"Farm Task Execution",
		{"schedule_ref": schedule_name},
		"name"
	)
	if existing_execution:
		# Update schedule with execution_ref if not already set
		frappe.db.set_value("Crop Plan Schedule", schedule_name, "execution_ref", existing_execution, update_modified=False)
		frappe.db.commit()
		return existing_execution

	# Reload schedule to get latest execution_ref
	schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
	schedule.reload()
	
	if schedule.execution_ref:
		return schedule.execution_ref

	# Use SQL with FOR UPDATE to lock the schedule row and prevent concurrent creation
	# This ensures only one process can create an execution for this schedule at a time
	frappe.db.begin()
	try:
		# Lock the schedule row using SQL FOR UPDATE
		locked_schedule = frappe.db.sql("""
			SELECT execution_ref 
			FROM `tabCrop Plan Schedule` 
			WHERE name = %s 
			FOR UPDATE
		""", (schedule_name,), as_dict=True)
		
		if locked_schedule and locked_schedule[0].get("execution_ref"):
			frappe.db.commit()
			return locked_schedule[0].get("execution_ref")
		
		# Double-check execution doesn't exist (another process might have created it)
		existing_execution = frappe.db.get_value(
			"Farm Task Execution",
			{"schedule_ref": schedule_name},
			"name"
		)
		if existing_execution:
			frappe.db.set_value("Crop Plan Schedule", schedule_name, "execution_ref", existing_execution, update_modified=False)
			frappe.db.commit()
			return existing_execution

		exec_doc = frappe.get_doc({"doctype": "Farm Task Execution"})
		exec_doc.schedule_ref = schedule.name
		exec_doc.status = "Started"
		exec_doc.actual_start = now_datetime()

		# Snapshot planned context
		exec_doc.farm_activity = schedule.farm_activity
		exec_doc.activity_name = schedule.activity_name
		exec_doc.sequence = schedule.sequence
		exec_doc.approved_input_mix = schedule.approved_input_mix
		exec_doc.planned_male_count = int(schedule.male_count or 0)
		exec_doc.planned_female_count = int(schedule.female_count or 0)

		# Warehouses selected later by user; keep blank for now

		# Copy blocks
		if schedule.block:
			exec_doc.append(
				"blocks",
				{
					"block": schedule.block,
					"block_name": schedule.block_name,
					"block_area_acres": schedule.block_area_acres,
					"no_of_seedlings": schedule.no_of_seedlings,
				},
			)

		# Copy equipment (planned)
		for eq in schedule.get("equipment") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
				},
			)

		# Copy inputs (planned)
		for it in schedule.get("inputs") or []:
			rate_qty = it.rate_quantity
			unit = it.unit
			planned_qty = it.total_quantity_to_use if schedule.is_spray else it.rate_quantity
			exec_doc.append(
				"inputs",
				{
					"item": it.item,
					"item_name": it.item_name,
					"uom": unit,
					"rate_qty": rate_qty,
					"planned_qty": planned_qty,
					"qty_to_issue": planned_qty,
					"qty_to_return": 0,
					"issued_qty": 0,
					"returned_qty": 0,
				},
			)

		_insert_with_retry(exec_doc)

		# Add labour to labour_attendance if provided
		if labour_list:
			import json
			try:
				labour_names = json.loads(labour_list) if isinstance(labour_list, str) else labour_list
				if isinstance(labour_names, list):
					for labour_name in labour_names:
						if labour_name:
							exec_doc.append("labour_attendance", {
								"labour": labour_name,
								"role": ""
							})
					exec_doc.save(ignore_permissions=True)
			except (json.JSONDecodeError, TypeError):
				# If labour_list is invalid, continue without adding labour
				pass

		# Set execution_ref on schedule atomically
		frappe.db.set_value("Crop Plan Schedule", schedule_name, "execution_ref", exec_doc.name, update_modified=False)
		frappe.db.commit()
		
		return exec_doc.name
	except Exception as e:
		frappe.db.rollback()
		# If we get a duplicate key error or similar, check if execution was created
		existing_execution = frappe.db.get_value(
			"Farm Task Execution",
			{"schedule_ref": schedule_name},
			"name"
		)
		if existing_execution:
			frappe.db.set_value("Crop Plan Schedule", schedule_name, "execution_ref", existing_execution, update_modified=False)
			frappe.db.commit()
			return existing_execution
		raise


@frappe.whitelist()
def create_from_on_demand_activity(on_demand_activity_name: str, labour_list: str = None) -> str:
	"""
	Create a Farm Task Execution document from an On Demand Activity.
	Also writes back execution_ref on the on-demand activity.
	
	Args:
		on_demand_activity_name: Name of the On Demand Activity
		labour_list: JSON string of list of farm worker names (from Farm Worker Details) to add to labour_attendance
	"""
	activity = frappe.get_doc("On Demand Activity", on_demand_activity_name)

	if activity.execution_ref:
		return activity.execution_ref

	exec_doc = frappe.get_doc({"doctype": "Farm Task Execution"})
	exec_doc.on_demand_activity_ref = activity.name
	exec_doc.status = "Started"
	exec_doc.actual_start = now_datetime()

	# Snapshot planned context
	exec_doc.farm_activity = activity.activity
	exec_doc.activity_name = activity.activity_name
	exec_doc.approved_input_mix = activity.approved_input_mix
	exec_doc.planned_male_count = int(activity.male_count or 0)
	exec_doc.planned_female_count = int(activity.female_count or 0)
	exec_doc.field = activity.field
	exec_doc.is_spray = activity.is_spray or 0
	exec_doc.planned_spray_water_liters = flt(activity.water_to_be_used_liters or 0, 3)

	# Copy blocks
	for block_row in activity.get("blocks") or []:
		exec_doc.append(
			"blocks",
			{
				"block": block_row.block,
				"block_name": block_row.block_name,
				"block_area_acres": block_row.block_area_acres,
				"no_of_seedlings": block_row.no_of_seedlings,
			},
		)

	# Copy equipment (planned) - combine all equipment types
	for eq in activity.get("machinery") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
			},
		)
	for eq in activity.get("implements") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
			},
		)
	for eq in activity.get("hand_tools") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
			},
		)
	for eq in activity.get("other_tools") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
			},
		)

	# Copy inputs (planned)
	for it in activity.get("inputs") or []:
		rate_qty = it.rate_quantity
		unit = it.unit
		# Use total_quantity_to_use if available (for spray activities), otherwise use rate_quantity
		planned_qty = flt(it.total_quantity_to_use or 0, 3) if (activity.is_spray and it.total_quantity_to_use) else flt(it.rate_quantity or 0, 3)
		exec_doc.append(
			"inputs",
			{
				"item": it.item,
				"item_name": it.item_name,
				"uom": unit,
				"rate_qty": rate_qty,
				"planned_qty": planned_qty,
				"qty_to_issue": planned_qty,
				"qty_to_return": 0,
				"issued_qty": 0,
				"returned_qty": 0,
			},
		)

	_insert_with_retry(exec_doc)

	# Add labour to labour_attendance if provided
	if labour_list:
		import json
		try:
			labour_names = json.loads(labour_list) if isinstance(labour_list, str) else labour_list
			if isinstance(labour_names, list):
				for labour_name in labour_names:
					if labour_name:
						exec_doc.append("labour_attendance", {
							"labour": labour_name,
							"role": ""
						})
				exec_doc.save(ignore_permissions=True)
		except (json.JSONDecodeError, TypeError):
			# If labour_list is invalid, continue without adding labour
			pass

	activity.db_set("execution_ref", exec_doc.name, update_modified=False)
	return exec_doc.name


@frappe.whitelist()
def start_execution(execution_name: str) -> str:
	"""
	Transition execution status from Started to In Progress.
	Uses row locking to prevent concurrent modification errors.
	"""
	import time
	max_retries = 3
	
	for attempt in range(max_retries):
		try:
			# Begin transaction and lock the row to prevent concurrent modifications
			frappe.db.begin()
			doc = frappe.get_doc("Farm Task Execution", execution_name, for_update=True)
			
			if doc.status != "Started":
				frappe.db.rollback()
				frappe.throw(f"Cannot start execution. Current status is {doc.status}. Only 'Started' executions can be moved to 'In Progress'.")
			
			doc.status = "In Progress"
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			return doc.name
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt < max_retries - 1:
				time.sleep(0.1 * (attempt + 1))  # Exponential backoff
			else:
				raise
		except Exception:
			frappe.db.rollback()
			raise
	
	return execution_name


@frappe.whitelist()
def recalculate_inputs(execution_name: str, use_actual_water: int = 0) -> str:
	"""
	Recalculate planned_qty / qty_to_issue from water liters for spray executions.
	use_actual_water=1 uses actual_spray_water_liters if available.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if not doc.is_spray:
		return doc.name

	total_acres = 0.0
	for b in doc.get("blocks") or []:
		total_acres += flt(b.block_area_acres)

	water_liters = flt(doc.planned_spray_water_liters)
	if int(use_actual_water) == 1 and flt(doc.actual_spray_water_liters) > 0:
		water_liters = flt(doc.actual_spray_water_liters)

	for row in doc.get("inputs") or []:
		unit = (row.uom or "").strip()
		total = compute_total_qty(
			water_liters=water_liters,
			total_acres=flt(total_acres),
			rate=flt(row.rate_qty),
			unit=unit,
		)
		# If total is 0 (unknown unit), keep values unchanged
		if flt(total) > 0:
			row.planned_qty = flt(total, 3)
			row.qty_to_issue = flt(total, 3)

	doc.save(ignore_permissions=True)
	return doc.name


def _require_warehouses(doc: Document):
	if not doc.source_warehouse or not doc.target_warehouse:
		frappe.throw("Please set Source Warehouse and Target Warehouse before creating Stock Entries.")


@frappe.whitelist()
def issue_inputs(execution_name: str) -> str:
	"""
	Create Stock Entry (Material Transfer) to issue inputs from source_warehouse to target_warehouse.
	Updates issued_qty on execution inputs.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	_require_warehouses(doc)

	if doc.issue_stock_entry:
		return doc.issue_stock_entry

	items = []
	for row in doc.get("inputs") or []:
		qty = flt(row.qty_to_issue)
		if qty <= 0:
			continue
		items.append(
			{
				"item_code": row.item,
				"qty": qty,
				"s_warehouse": doc.source_warehouse,
				"t_warehouse": doc.target_warehouse,
				"batch_no": row.batch_no,
			}
		)

	if not items:
		frappe.throw("No Qty to Issue found in Inputs table.")

	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Transfer",
			"items": items,
		}
	)
	se.insert(ignore_permissions=True)
	se.submit()

	doc.issue_stock_entry = se.name

	# Update issued quantities (single-shot issue for now)
	for row in doc.get("inputs") or []:
		qty = flt(row.qty_to_issue)
		if qty > 0:
			row.issued_qty = flt(qty, 3)

	doc.save(ignore_permissions=True)
	return se.name


@frappe.whitelist()
def return_inputs(execution_name: str) -> str:
	"""
	Create Stock Entry (Material Transfer) to return inputs from target_warehouse back to source_warehouse.
	Updates returned_qty and consumed_qty.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	_require_warehouses(doc)

	if doc.return_stock_entry:
		return doc.return_stock_entry

	items = []
	for row in doc.get("inputs") or []:
		qty = flt(row.qty_to_return)
		if qty <= 0:
			continue
		if qty > flt(row.issued_qty) - flt(row.returned_qty):
			frappe.throw(f"Qty to Return for item {row.item} exceeds available issued quantity.")
		items.append(
			{
				"item_code": row.item,
				"qty": qty,
				"s_warehouse": doc.target_warehouse,
				"t_warehouse": doc.source_warehouse,
				"batch_no": row.batch_no,
			}
		)

	if not items:
		frappe.throw("No Qty to Return found in Inputs table.")

	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Transfer",
			"items": items,
		}
	)
	se.insert(ignore_permissions=True)
	se.submit()

	doc.return_stock_entry = se.name

	for row in doc.get("inputs") or []:
		qty = flt(row.qty_to_return)
		if qty > 0:
			row.returned_qty = flt(flt(row.returned_qty) + qty, 3)

	doc.save(ignore_permissions=True)
	return se.name


def _clone_attachment_to_checkin(*, execution_doc: Document, checkin_name: str, file_url: str):
	"""
	Clone an attachment from the execution doc to the Employee Checkin.
	This creates a new File record pointing to the same file_url.
	"""
	if not file_url:
		return

	original = frappe.db.get_value(
		"File",
		{
			"file_url": file_url,
			"attached_to_doctype": execution_doc.doctype,
			"attached_to_name": execution_doc.name,
		},
		["file_name", "is_private", "folder"],
		as_dict=True,
	)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_url": file_url,
			"file_name": (original or {}).get("file_name") or file_url.split("/")[-1],
			"is_private": (original or {}).get("is_private") or 0,
			"folder": (original or {}).get("folder") or "Home",
			"attached_to_doctype": "Employee Checkin",
			"attached_to_name": checkin_name,
		}
	)
	file_doc.insert(ignore_permissions=True)


@frappe.whitelist()
def mark_checkin_in(execution_name: str) -> str:
	"""
	Create Employee Checkin (IN) records for labour in labour_attendance.
	Attaches execution checkin_in_photo (if any) to each created checkin.
	
	NOTE: This function currently uses Employee Checkin which requires an Employee.
	Since we're now using Labour Details, this may need to be updated to use
	a Labour Checkin doctype or link Labour to Employee.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if not doc.get("labour_attendance"):
		frappe.throw("Please add labour in Labour Attendance first.")

	photo_url = doc.checkin_in_photo
	for row in doc.labour_attendance:
		# TODO: Employee Checkin requires an Employee field, but we're using Labour Details now
		# This needs to be addressed - either create Labour Checkin or link Labour to Employee
		if not row.labour or row.checkin_in:
			continue

		# For now, skip checkin creation since Employee Checkin requires Employee
		# This functionality needs to be re-implemented for Labour Details
		frappe.throw("Check-in functionality for Labour Details is not yet implemented. Please use Employee Checkin separately if needed.")

	doc.save(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def mark_checkin_out(execution_name: str) -> str:
	"""
	Create Employee Checkin (OUT) records for labour in labour_attendance.
	Attaches execution checkin_out_photo (if any) to each created checkin.
	
	NOTE: This function currently uses Employee Checkin which requires an Employee.
	Since we're now using Labour Details, this may need to be updated to use
	a Labour Checkin doctype or link Labour to Employee.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if not doc.get("labour_attendance"):
		frappe.throw("Please add labour in Labour Attendance first.")

	photo_url = doc.checkin_out_photo
	for row in doc.labour_attendance:
		# TODO: Employee Checkin requires an Employee field, but we're using Labour Details now
		# This needs to be addressed - either create Labour Checkin or link Labour to Employee
		if not row.labour or row.checkin_out:
			continue

		# For now, skip checkin creation since Employee Checkin requires Employee
		# This functionality needs to be re-implemented for Labour Details
		frappe.throw("Check-out functionality for Labour Details is not yet implemented. Please use Employee Checkin separately if needed.")

	# Set end time if moving to completed later; do not force status here
	if not doc.actual_end and doc.status in ("Completed", "Aborted"):
		doc.actual_end = now_datetime()

	doc.save(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def get_recent_error_logs(limit: int = 10) -> List[Dict]:
	"""
	Get recent error logs related to get_available_labour for debugging.
	"""
	try:
		error_logs = frappe.get_all(
			"Error Log",
			filters={
				"method": ["like", "%Get Available Labour%"]
			},
			fields=["name", "method", "error", "creation", "modified"],
			order_by="modified desc",
			limit=limit
		)
		return error_logs or []
	except Exception as e:
		return [{"error": str(e)}]


@frappe.whitelist()
def get_available_labour(attendance_date: str = None, debug: bool = False) -> List[Dict]:
	"""
	Get list of Farm Worker Details with Present attendance for given date.
	
	Args:
		attendance_date: Date string in YYYY-MM-DD format. Defaults to today.
		Note: For attendance checking, we use today's date since attendance is typically set for today.
	
	Returns:
		List of dictionaries with farm worker details (name, worker_name, aadhaar_number, dob, address, gender)
	"""
	from frappe.utils import today, getdate
	
	# Log function entry
	print(f"[get_available_labour] FUNCTION CALLED with attendance_date={attendance_date}, debug={debug}")
	frappe.log_error(f"get_available_labour called with attendance_date={attendance_date}", "Get Available Labour Entry")
	
	# First, let's directly query the database to see what Present records exist for today
	today_str = today()
	try:
		direct_check = frappe.db.sql("""
			SELECT name, farm_worker, worker_name, attendance_date, status
			FROM `tabFarm Worker Attendance`
			WHERE attendance_date = %s AND status = 'Present'
			ORDER BY worker_name
		""", (today_str,), as_dict=True)
		print(f"[get_available_labour] DIRECT QUERY for {today_str}: Found {len(direct_check) if direct_check else 0} Present records")
		if direct_check:
			for rec in direct_check:
				print(f"[get_available_labour]   - {rec.name}: {rec.farm_worker} ({rec.worker_name}), Date: {rec.attendance_date}")
	except Exception as e:
		print(f"[get_available_labour] ERROR in direct query: {str(e)}")
		frappe.log_error(f"Direct query error: {str(e)}", "Get Available Labour Error")
	
	# Default to today if no date provided, but use the provided date if available
	# This allows checking attendance for the task's planned date
	if not attendance_date:
		attendance_date = today()
	
	# Extract ONLY the date part from datetime string (YYYY-MM-DD)
	# Handle both date strings (YYYY-MM-DD) and datetime strings (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD+HH:MM:SS)
	date_str = str(attendance_date).strip()
	
	# Remove time part if present - extract just YYYY-MM-DD
	if ' ' in date_str:
		# Format: "2026-01-05 15:01:00"
		date_str = date_str.split()[0]
	elif 'T' in date_str:
		# Format: "2026-01-05T15:01:00"
		date_str = date_str.split('T')[0]
	elif '+' in date_str:
		# Format: "2026-01-05+15:01:00"
		date_str = date_str.split('+')[0]
	
	# Now date_str should be in YYYY-MM-DD format
	print(f"[get_available_labour] Extracted date from '{attendance_date}' -> '{date_str}'")
	frappe.log_error(f"Extracted date from '{attendance_date}' -> '{date_str}'", "Get Available Labour Debug")
	
	# Convert to date object for comparison
	try:
		attendance_date_obj = getdate(date_str)
	except Exception as e:
		frappe.log_error(f"Invalid date format: {date_str} (from {attendance_date}). Error: {str(e)}", "Get Available Labour Date Error")
		# Fallback to today if date parsing fails
		attendance_date_obj = getdate(today())
		date_str = str(attendance_date_obj)
	
	# Step 1: Get all Farm Worker Attendance records with "Present" status
	# Use the extracted date string (YYYY-MM-DD format) for queries
	today_date_str = today_str  # Use the one we already got
	today_date_obj = getdate(today_date_str)
	
	# ALWAYS check today's date first (attendance is typically set for today)
	# Then check the provided date if it's different
	dates_to_try = [(today_date_str, today_date_obj)]
	
	# If the provided date is not today, also try the provided date
	if date_str != today_date_str:
		dates_to_try.append((date_str, attendance_date_obj))
		print(f"[get_available_labour] Will check both today ({today_date_str}) and provided date ({date_str})")
	else:
		print(f"[get_available_labour] Checking today's date: {today_date_str}")
	
	present_attendance = []
	
	# First, get ALL Present records to see what we have
	try:
		all_present_records = frappe.get_all(
			"Farm Worker Attendance",
			filters={"status": "Present"},
			fields=["name", "farm_worker", "worker_name", "attendance_date", "status"],
			order_by="attendance_date desc",
			limit=50
		)
		print(f"[get_available_labour] Total Present records in database: {len(all_present_records) if all_present_records else 0}")
		if all_present_records:
			print(f"[get_available_labour] Sample Present records (last 10):")
			for att in all_present_records[:10]:
				print(f"  - {att.get('name')}: Farm Worker {att.get('farm_worker')}, Date: {att.get('attendance_date')} (type: {type(att.get('attendance_date'))})")
	except Exception as e:
		frappe.log_error(f"Error fetching all Present records: {str(e)}", "Get Available Labour Error")
	
	# Now try to get records for the specific dates
	# Use date string (YYYY-MM-DD) for all queries
	for date_str_check, date_obj_check in dates_to_try:
		try:
			print(f"[get_available_labour] Querying Farm Worker Attendance for date: {date_str_check} (YYYY-MM-DD format)")
			
			# Try direct SQL query first with date string
			sql_results = None
			try:
				sql_results = frappe.db.sql("""
					SELECT name, farm_worker, worker_name, attendance_date, status
					FROM `tabFarm Worker Attendance`
					WHERE attendance_date = %s AND status = 'Present'
					ORDER BY worker_name
				""", (date_str_check,), as_dict=True)
				print(f"[get_available_labour] SQL query found {len(sql_results) if sql_results else 0} Present records for {date_str_check}")
				if sql_results:
					for att in sql_results:
						print(f"  SQL - {att.get('name')}: Farm Worker {att.get('farm_worker')}, Date: {att.get('attendance_date')}, Status: {att.get('status')}")
			except Exception as sql_err:
				print(f"[get_available_labour] SQL query error: {str(sql_err)}")
				frappe.log_error(f"SQL query error: {str(sql_err)}", "Get Available Labour Error")
			
			# Get Present records using get_all with date string
			# Get farm_worker and worker_name directly from Farm Worker Attendance
			attendance_for_date = frappe.get_all(
				"Farm Worker Attendance",
				filters={
					"attendance_date": date_str_check,  # Use date string, not date object
					"status": "Present"
				},
				fields=["name", "farm_worker", "worker_name", "attendance_date"],
				order_by="worker_name"
			)
			
			print(f"[get_available_labour] get_all() with date string '{date_str_check}' found {len(attendance_for_date) if attendance_for_date else 0} Present attendance records")
			
			# If no results with date string, try with date object
			if (not attendance_for_date or len(attendance_for_date) == 0):
				print(f"[get_available_labour] Trying get_all() with date object for {date_str_check}")
				try:
					attendance_for_date = frappe.get_all(
						"Farm Worker Attendance",
						filters={
							"attendance_date": date_obj_check,  # Try with date object
							"status": "Present"
						},
						fields=["name", "farm_worker", "worker_name", "attendance_date"],
						order_by="worker_name"
					)
					print(f"[get_available_labour] get_all() with date object found {len(attendance_for_date) if attendance_for_date else 0} Present attendance records")
				except Exception as date_obj_err:
					print(f"[get_available_labour] Error with date object query: {str(date_obj_err)}")
					frappe.log_error(f"Error with date object query: {str(date_obj_err)}", "Get Available Labour Error")
			
			# If get_all didn't work but SQL did, use SQL results
			# ALWAYS prefer SQL results if they exist, as they're more reliable
			if sql_results and len(sql_results) > 0:
				if not attendance_for_date or len(attendance_for_date) == 0:
					print(f"[get_available_labour] Using SQL results since get_all() returned no results")
				else:
					print(f"[get_available_labour] SQL found {len(sql_results)} records, get_all() found {len(attendance_for_date)} - using SQL results")
				# Convert SQL results to same format as get_all results
				attendance_for_date = [
					{
						"name": r.get("name", ""),
						"farm_worker": r.farm_worker,
						"worker_name": r.worker_name,
						"attendance_date": r.attendance_date
					}
					for r in sql_results
				]
				print(f"[get_available_labour] Using {len(attendance_for_date)} SQL results")
			
			# If still no results, try getting all Present records and filter by date in Python
			if (not attendance_for_date or len(attendance_for_date) == 0):
				print(f"[get_available_labour] Trying fallback: Get all Present records and filter by date {date_str_check}")
				try:
					all_present = frappe.get_all(
						"Farm Worker Attendance",
						filters={"status": "Present"},
						fields=["name", "farm_worker", "worker_name", "attendance_date"],
						order_by="worker_name"
					)
					if all_present:
						# Filter by date in Python
						matching_records = []
						for rec in all_present:
							# Convert rec.attendance_date to string format YYYY-MM-DD for comparison
							rec_date = rec.attendance_date
							if rec_date:
								# Handle both date objects and date strings
								if hasattr(rec_date, 'strftime'):
									# It's a date/datetime object
									rec_date_str = rec_date.strftime('%Y-%m-%d')
								else:
									# It's already a string, extract date part
									rec_date_str = str(rec_date).split()[0].split('T')[0].split('+')[0]
								
								if rec_date_str == date_str_check:
									matching_records.append({
										"name": rec.get("name", ""),
										"farm_worker": rec.farm_worker,
										"worker_name": rec.worker_name,
										"attendance_date": rec.attendance_date
									})
						if matching_records:
							print(f"[get_available_labour] Fallback found {len(matching_records)} records for {date_str_check}")
							attendance_for_date = matching_records
				except Exception as fallback_err:
					print(f"[get_available_labour] Fallback query error: {str(fallback_err)}")
					frappe.log_error(f"Fallback query error: {str(fallback_err)}", "Get Available Labour Error")
			
			if attendance_for_date and len(attendance_for_date) > 0:
				present_attendance = attendance_for_date
				# Debug: Log the workers found
				print(f"[get_available_labour] ✓ Found {len(present_attendance)} Present attendance records for {date_str_check}")
				for att in present_attendance:
					print(f"  - Farm Worker: {att.get('farm_worker')}, Name: {att.get('worker_name')}, Date: {att.get('attendance_date')}")
				break  # Use the first date that has results
			else:
				print(f"[get_available_labour] ✗ No Present attendance records found for {date_str_check}")
		
		except Exception as e:
			error_msg = f"Error fetching Farm Worker Attendance for {date_str_check}: {str(e)}"
			print(f"[get_available_labour] ERROR: {error_msg}")
			frappe.log_error(error_msg, "Get Available Labour Error")
			# Continue to next date
			continue
	
	if not present_attendance or len(present_attendance) == 0:
		print(f"[get_available_labour] No Present attendance records found for any checked date")
		
		# Debug: Let's also check all Present records regardless of date to see if there are any
		try:
			all_present = frappe.get_all(
				"Farm Worker Attendance",
				filters={"status": "Present"},
				fields=["name", "farm_worker", "worker_name", "attendance_date", "status"],
				order_by="attendance_date desc",
				limit=10
			)
			print(f"[get_available_labour] Debug: Found {len(all_present) if all_present else 0} total Present records (showing last 20)")
			if all_present:
				print(f"[get_available_labour] Available Present attendance dates:")
				for att in all_present:
					att_date_str = str(att.get("attendance_date")).split()[0] if att.get("attendance_date") else "None"
					print(f"  - {att.get('name')}: Farm Worker {att.get('farm_worker')} ({att.get('worker_name')}), Date: {att_date_str}, Status: {att.get('status')}")
					
				# Check if any match today or the requested date
				today_str = str(today())
				print(f"[get_available_labour] Today's date: {today_str}, Requested date: {date_str}")
				print(f"[get_available_labour] NOTE: If attendance is set for a different date, workers won't appear. Set attendance for {today_str} to see workers.")
		except Exception as e:
			print(f"[get_available_labour] Error checking all Present records: {str(e)}")
			frappe.log_error(f"Error checking all Present records: {str(e)}", "Get Available Labour Error")
		
		# Return empty list - this is expected if no workers have Present attendance
		return []
	
	# Step 2: Get Farm Worker Details for each Present attendance record
	available_labour = []
	
	# Get Farm Worker Details for all present workers in one query
	if present_attendance:
		# present_attendance is a list of dictionaries, so use dictionary access
		farm_worker_names = [att.get("farm_worker") for att in present_attendance if att.get("farm_worker")]
		print(f"[get_available_labour] Processing {len(farm_worker_names)} workers with Present attendance: {farm_worker_names}")
		
		if not farm_worker_names:
			print(f"[get_available_labour] WARNING: No farm_worker names extracted from attendance records")
			return []
		
		# Get Farm Worker Details with all needed fields
		farm_workers_data = frappe.get_all(
			"Farm Worker Details",
			filters={"name": ["in", farm_worker_names]},
			fields=["name", "worker_name", "aadhaar_number", "dob", "address", "gender"],
			order_by="worker_name"
		)
		
		print(f"[get_available_labour] Found {len(farm_workers_data)} Farm Worker Details records for {len(farm_worker_names)} attendance records")
		
		if len(farm_workers_data) != len(farm_worker_names):
			missing = set(farm_worker_names) - {fw.get("name") for fw in farm_workers_data}
			print(f"[get_available_labour] WARNING: Missing Farm Worker Details for: {missing}")
		
		# Return Farm Worker Details directly (no Labour Details needed)
		for farm_worker in farm_workers_data:
			available_labour.append({
				"name": farm_worker.get("name"),
				"worker_name": farm_worker.get("worker_name"),
				"labour_name": farm_worker.get("worker_name"),  # Keep for backward compatibility with frontend
				"aadhaar_number": farm_worker.get("aadhaar_number"),
				"dob": farm_worker.get("dob"),
				"address": farm_worker.get("address"),
				"gender": farm_worker.get("gender")
			})
	
	# Sort by worker name
	available_labour.sort(key=lambda x: x.get("worker_name", ""))
	
	print(f"[get_available_labour] === SUMMARY ===")
	print(f"[get_available_labour] Date checked: {date_str}")
	print(f"[get_available_labour] Present attendance records found: {len(present_attendance) if present_attendance else 0}")
	print(f"[get_available_labour] Available labour returned: {len(available_labour)}")
	print(f"[get_available_labour] Returning {len(available_labour)} labour with Present attendance for date {date_str}")
	
	# Debug: Log what we're returning
	if available_labour:
		for labour in available_labour:
			print(f"[get_available_labour]   - Farm Worker: {labour.get('name')}, Name: {labour.get('worker_name')}, Aadhaar: {labour.get('aadhaar_number')}")
	else:
		print(f"[get_available_labour] WARNING: No labour returned! present_attendance count: {len(present_attendance) if present_attendance else 0}")
		if present_attendance:
			print(f"[get_available_labour] DEBUG: present_attendance contains {len(present_attendance)} records but no Farm Worker Details were found")
			print(f"[get_available_labour] DEBUG: First few attendance records: {present_attendance[:3]}")
	
	# Also log to error log for visibility - this will show in Frappe Error Log
	frappe.log_error(
		f"get_available_labour summary: Date={date_str}, Present records={len(present_attendance) if present_attendance else 0}, Farm Workers returned={len(available_labour)}.",
		"Get Available Labour Summary"
	)
	
	# If debug mode, add diagnostic info
	if debug:
		debug_info = {
		"input_date": attendance_date,
		"extracted_date": date_str,
		"parsed_date": str(attendance_date_obj),
		"today_date": today_date_str,
		"dates_checked": [d[0] for d in dates_to_try],  # Just the date strings
			"present_attendance_count": len(present_attendance) if present_attendance else 0,
			"available_labour_count": len(available_labour)
		}
		return {
			"labour": available_labour,
			"debug": debug_info
		}
	
	return available_labour



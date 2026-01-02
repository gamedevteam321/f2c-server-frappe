# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List

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
def create_from_schedule(schedule_name: str) -> str:
	"""
	Create a Farm Task Execution document from a Crop Plan Schedule.
	Also writes back execution_ref on the schedule.
	Uses database-level checks and transactions to prevent duplicate creation.
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
def create_from_on_demand_activity(on_demand_activity_name: str) -> str:
	"""
	Create a Farm Task Execution document from an On Demand Activity.
	Also writes back execution_ref on the on-demand activity.
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
	Create Employee Checkin (IN) records for employees in labour_attendance.
	Attaches execution checkin_in_photo (if any) to each created checkin.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if not doc.get("labour_attendance"):
		frappe.throw("Please add employees in Labour Attendance first.")

	photo_url = doc.checkin_in_photo
	for row in doc.labour_attendance:
		if not row.employee or row.checkin_in:
			continue

		chk = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": row.employee,
				"time": now_datetime(),
				"log_type": "IN",
			}
		)
		chk.insert(ignore_permissions=True)
		row.checkin_in = chk.name

		if photo_url:
			_clone_attachment_to_checkin(execution_doc=doc, checkin_name=chk.name, file_url=photo_url)

	# Optionally move status to Started
	if doc.status == "Draft":
		doc.status = "Started"
		if not doc.actual_start:
			doc.actual_start = now_datetime()

	doc.save(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def mark_checkin_out(execution_name: str) -> str:
	"""
	Create Employee Checkin (OUT) records for employees in labour_attendance.
	Attaches execution checkin_out_photo (if any) to each created checkin.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if not doc.get("labour_attendance"):
		frappe.throw("Please add employees in Labour Attendance first.")

	photo_url = doc.checkin_out_photo
	for row in doc.labour_attendance:
		if not row.employee or row.checkin_out:
			continue

		chk = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": row.employee,
				"time": now_datetime(),
				"log_type": "OUT",
			}
		)
		chk.insert(ignore_permissions=True)
		row.checkin_out = chk.name

		if photo_url:
			_clone_attachment_to_checkin(execution_doc=doc, checkin_name=chk.name, file_url=photo_url)

	# Set end time if moving to completed later; do not force status here
	if not doc.actual_end and doc.status in ("Completed", "Aborted"):
		doc.actual_end = now_datetime()

	doc.save(ignore_permissions=True)
	return doc.name



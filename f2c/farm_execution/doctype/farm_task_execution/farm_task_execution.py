# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, get_datetime, now_datetime, cint, convert_utc_to_system_timezone

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import compute_total_qty
from f2c.inventory.doctype.warehouse_stock.warehouse_stock import (
	_get_ws_docname_for_warehouse,
	refresh_from_ledger,
)


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
		self._sync_and_validate_progress_images()
		self._validate_status_rules()
		self._validate_inputs_consumed_qty()
		self._compute_consumed_qty()

	def _sync_and_validate_progress_images(self):
		"""
		Progress media (images/videos) are uploaded as a child table.
		- Keep progress_image_count in sync
		- Enforce max 5 media items
		"""
		rows = self.get("progress_images") or []
		if len(rows) > 5:
			frappe.throw("Maximum 5 progress media items are allowed.")
		self.progress_image_count = len(rows)

	def on_trash(self):
		"""Prevent deletion if linked to schedule or on-demand activity."""
		self._check_linked_schedule()
		self._check_linked_on_demand_activity()

	def on_cancel(self):
		"""Prevent cancellation if linked to schedule or on-demand activity."""
		self._check_linked_schedule()
		self._check_linked_on_demand_activity()

	def on_update(self):
		"""Update linked Crop Plan Schedule or On Demand Activity status when execution is completed or aborted."""
		if self.has_value_changed("status"):
			# Update linked Crop Plan Schedule status if execution is completed
			if self.status == "Completed" and self.schedule_ref:
				try:
					schedule_status = frappe.db.get_value("Crop Plan Schedule", self.schedule_ref, "status")
					# Only update if schedule is not already in a terminal state
					if schedule_status and schedule_status not in ("Aborted", "Completed", "Rescheduled"):
						frappe.db.set_value(
							"Crop Plan Schedule",
							self.schedule_ref,
							"status",
							"Completed",
							update_modified=False,
						)
				except Exception as e:
					frappe.log_error(
						f"Error updating Crop Plan Schedule status for {self.schedule_ref}: {str(e)}",
						"Farm Task Execution on_update Error",
					)
			
			# Update linked Crop Plan Schedule status if execution is aborted
			if self.status == "Aborted" and self.schedule_ref:
				try:
					schedule_status = frappe.db.get_value("Crop Plan Schedule", self.schedule_ref, "status")
					# Only update if schedule is not already in a terminal state
					if schedule_status and schedule_status not in ("Aborted", "Completed", "Rescheduled"):
						frappe.db.set_value(
							"Crop Plan Schedule",
							self.schedule_ref,
							"status",
							"Aborted",
							update_modified=False,
						)
				except Exception as e:
					frappe.log_error(
						f"Error updating Crop Plan Schedule status to Aborted for {self.schedule_ref}: {str(e)}",
						"Farm Task Execution on_update Error",
					)

			# Update linked On Demand Activity status if execution is completed
			if self.status == "Completed" and self.on_demand_activity_ref:
				try:
					activity_status = frappe.db.get_value("On Demand Activity", self.on_demand_activity_ref, "status")
					# Only update if activity is not already in a terminal state
					if activity_status and activity_status not in ("Aborted", "Completed", "Archived"):
						frappe.db.set_value("On Demand Activity", self.on_demand_activity_ref, "status", "Completed", update_modified=False)
				except Exception as e:
					frappe.log_error(f"Error updating On Demand Activity status for {self.on_demand_activity_ref}: {str(e)}", "Farm Task Execution on_update Error")
			
			# Update linked On Demand Activity status if execution is aborted
			if self.status == "Aborted" and self.on_demand_activity_ref:
				try:
					activity_status = frappe.db.get_value("On Demand Activity", self.on_demand_activity_ref, "status")
					# Only update if activity is not already in a terminal state
					if activity_status and activity_status not in ("Aborted", "Completed", "Archived"):
						frappe.db.set_value("On Demand Activity", self.on_demand_activity_ref, "status", "Aborted", update_modified=False)
				except Exception as e:
					frappe.log_error(f"Error updating On Demand Activity status to Aborted for {self.on_demand_activity_ref}: {str(e)}", "Farm Task Execution on_update Error")

			# Create Material Issue (consumption) from target warehouse when execution completes and used approved inputs
			if self.status == "Completed":
				try:
					_create_consumption_stock_entry_if_applicable(self)
				except Exception as e:
					# Title must be <= 140 chars (Error Log doctype)
					frappe.log_error(
						message=f"Consumption stock entry on completion failed for {self.name}: {str(e)}",
						title="Execution consumption stock entry failed",
					)
					raise

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
		if self.status == "Ready" and not self.actual_start:
			self.actual_start = now_datetime()

		# When transitioning to In Review, set actual_end if not already set
		if self.status == "In Review" and not self.actual_end:
			self.actual_end = now_datetime()

		if self.status in ("Completed", "Aborted") and not self.actual_end:
			self.actual_end = now_datetime()

		# Get old status for transition validation (needed for paused_at/resumed_at and transitions)
		old_status = None
		if self.has_value_changed("status"):
			if hasattr(self, "_doc_before_save") and self._doc_before_save:
				old_status = self._doc_before_save.status
			elif not self.is_new():
				# Fallback: fetch from database if _doc_before_save is not available
				old_status = frappe.db.get_value(self.doctype, self.name, "status")

		# When transitioning to On Hold, set paused_at
		if self.has_value_changed("status") and self.status == "On Hold":
			self.paused_at = now_datetime()

		# When transitioning from On Hold to In Progress, set resumed_at
		if self.has_value_changed("status") and old_status == "On Hold" and self.status == "In Progress":
			self.resumed_at = now_datetime()

		# For spray activities, validate actual_spray_water_liters
		# If transitioning from In Review to Completed, use planned value as fallback if actual is not set
		if self.is_spray and self.status in ("Completed", "Aborted"):
			actual_water = flt(self.actual_spray_water_liters)
			if actual_water <= 0:
				# If approving from In Review and actual is not set, use planned value
				if old_status == "In Review" and self.status == "Completed":
					if flt(self.planned_spray_water_liters) > 0:
						self.actual_spray_water_liters = flt(self.planned_spray_water_liters, 3)
					else:
						frappe.throw("Actual Spray Water (Liters) is required for Spray activities. Please set it before completing.")
				else:
					frappe.throw("Actual Spray Water (Liters) is required for Spray activities.")

		if self.status == "Aborted":
			if not (self.abort_category or "").strip() or not (self.abort_reason or "").strip():
				frappe.throw("Abort Category and Abort Reason are required when status is Aborted.")
		
		# Validate status transitions
		if self.has_value_changed("status"):
			# old_status already retrieved above
			new_status = self.status

			# Require at least 1 progress image before submitting for review (aggregate across all Days if using per-day model)
			if new_status == "In Review":
				# Allow explicit skip from API when user chooses "Submit for review without images"
				if not getattr(frappe.flags, "skip_progress_image_min", False):
					total_images = 0
					day_names = frappe.get_all(
						"Farm Task Execution Day",
						filters={"execution": self.name},
						pluck="name",
					)
					if day_names:
						for day_name in day_names:
							count = frappe.db.count("Farm Task Execution Day Progress Image", {"parent": day_name})
							total_images += count
					else:
						total_images = len(self.get("progress_images") or [])
					if total_images < 1:
						frappe.throw("Please upload at least 1 progress image before submitting for review (total across all days).")
			
			# Only allow specific transitions
			valid_transitions = {
				"Ready": ["In Progress", "Reported", "Aborted"],
				"In Progress": ["In Review", "Reported", "Aborted", "On Hold"],
				"On Hold": ["In Progress", "Aborted"],
				"In Review": ["Completed", "Reported", "Aborted"],
				"Reported": ["Rescheduled", "Aborted"],
				"Rescheduled": [],  # Terminal-ish state for this execution
				"Completed": [],  # Terminal state
				"Aborted": []  # Terminal state
			}
			
			if old_status and old_status in valid_transitions:
				if new_status not in valid_transitions[old_status]:
					frappe.throw(
						f"Cannot transition from '{old_status}' to '{new_status}'. "
						f"Valid transitions from '{old_status}' are: {', '.join(valid_transitions[old_status])}"
					)

	def _validate_inputs_consumed_qty(self):
		"""Consumed quantity cannot exceed issued quantity for any input."""
		for row in self.get("inputs") or []:
			consumed = flt(row.get("consumed_qty"), 3)
			issued = flt(row.get("issued_qty"), 3)
			if consumed > issued:
				item_label = (row.get("item_name") or row.get("item") or "Item").strip() or "Item"
				frappe.throw(
					frappe._("Consumed quantity cannot be greater than issued quantity for {0}. Issued: {1}, Consumed: {2}.").format(
						item_label, issued, consumed
					)
				)

	def _compute_consumed_qty(self):
		# consumed_qty is user-editable input; do not overwrite with issued - returned
		pass


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
		exec_doc.status = "Ready"
		exec_doc.actual_start = now_datetime()
		# Set execution_type from planned span (Single Day vs Multi Day)
		if schedule.planned_start and schedule.planned_end:
			d1 = getdate(schedule.planned_start)
			d2 = getdate(schedule.planned_end)
			exec_doc.execution_type = "Multi Day" if d1 != d2 else "Single Day"
		else:
			exec_doc.execution_type = "Single Day"

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

		# Copy equipment (planned) from machinery, implements, hand_tools, other_tools
		for eq in schedule.get("machinery") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"paired_implement": getattr(eq, "paired_implement", None),
					"planned_hours": eq.planned_hours,
					"return_type": getattr(eq, "return_type", None) or "Non Returnable",
				},
			)
		for eq in schedule.get("implements") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
					"return_type": getattr(eq, "return_type", None) or "Non Returnable",
				},
			)
		for eq in schedule.get("hand_tools") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
					"return_type": getattr(eq, "return_type", None) or "Non Returnable",
				},
			)
		for eq in schedule.get("other_tools") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
					"return_type": getattr(eq, "return_type", None) or "Non Returnable",
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
	exec_doc.status = "Ready"
	exec_doc.actual_start = now_datetime()
	# Set execution_type from planned span (Single Day vs Multi Day)
	if activity.planned_start and activity.planned_end:
		d1 = getdate(activity.planned_start)
		d2 = getdate(activity.planned_end)
		exec_doc.execution_type = "Multi Day" if d1 != d2 else "Single Day"
	else:
		exec_doc.execution_type = "Single Day"

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
				"paired_implement": getattr(eq, "paired_implement", None),
				"planned_hours": eq.planned_hours,
				"return_type": getattr(eq, "return_type", None) or "Non Returnable",
			},
		)
	for eq in activity.get("implements") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
				"return_type": getattr(eq, "return_type", None) or "Non Returnable",
			},
		)
	for eq in activity.get("hand_tools") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
				"return_type": getattr(eq, "return_type", None) or "Non Returnable",
			},
		)
	for eq in activity.get("other_tools") or []:
		exec_doc.append(
			"equipment",
			{
				"asset": eq.asset,
				"asset_name": eq.asset_name,
				"planned_hours": eq.planned_hours,
				"return_type": getattr(eq, "return_type", None) or "Non Returnable",
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


def _apply_pre_execution_checklist(doc, equipment_checklist=None, input_checklist=None, equipment_photo=None, equipment_photo_urls=None):
	"""Apply pre-execution checklist and photos to doc. Does not save.
	equipment_photo_urls: list of file URLs (preferred when multiple images).
	equipment_photo: legacy single base64 data URL. Stored field holds JSON array of URLs."""
	from frappe.utils import cint
	import json
	import base64

	if equipment_checklist is not None:
		if isinstance(equipment_checklist, str):
			try:
				equipment_checklist = json.loads(equipment_checklist) if equipment_checklist.strip() else None
			except Exception:
				equipment_checklist = None
		if equipment_checklist and isinstance(equipment_checklist, (list, tuple)):
			present_by_asset = {str(e.get("asset")): cint(e.get("present")) for e in equipment_checklist if e.get("asset") is not None}
			for row in (doc.equipment or []):
				if getattr(row, "asset", None) and row.asset in present_by_asset:
					row.pre_execution_present = present_by_asset[row.asset]

	if input_checklist is not None:
		if isinstance(input_checklist, str):
			try:
				input_checklist = json.loads(input_checklist) if (input_checklist or "").strip() else None
			except Exception:
				input_checklist = None
		if input_checklist and isinstance(input_checklist, (list, tuple)):
			present_by_item = {str(i.get("item")): cint(i.get("present")) for i in input_checklist if i.get("item") is not None}
			for row in (doc.inputs or []):
				if getattr(row, "item", None) and row.item in present_by_item:
					row.pre_execution_present = present_by_item[row.item]

	urls = []
	if equipment_photo_urls is not None:
		if isinstance(equipment_photo_urls, str):
			try:
				parsed = json.loads(equipment_photo_urls) if (equipment_photo_urls or "").strip() else []
				urls = [str(u) for u in parsed] if isinstance(parsed, (list, tuple)) else []
			except Exception:
				urls = [s for s in (equipment_photo_urls or "").strip().split(",") if s.strip()]
		elif isinstance(equipment_photo_urls, (list, tuple)):
			urls = [str(u) for u in equipment_photo_urls if u]
	if urls:
		doc.pre_execution_equipment_photo = json.dumps(urls)
		return

	if equipment_photo:
		from frappe.utils.file_manager import save_file
		from frappe.utils import get_datetime
		b64 = equipment_photo
		if isinstance(equipment_photo, str) and "," in equipment_photo and equipment_photo.strip().startswith("data:"):
			b64 = equipment_photo.split(",", 1)[1]
		try:
			decoded = base64.b64decode(b64)
		except Exception:
			decoded = None
		if decoded:
			fname = f"pre_exec_equipment_{doc.name}_{get_datetime().strftime('%Y%m%d%H%M%S')}.png"
			file_doc = save_file(
				fname, decoded,
				dt="Farm Task Execution", dn=doc.name,
				folder=None, decode=False, is_private=0, df="pre_execution_equipment_photo"
			)
			if file_doc and getattr(file_doc, "file_url", None):
				doc.pre_execution_equipment_photo = json.dumps([file_doc.file_url])


def _apply_equipment_fuel_to_day(day_doc, equipment_fuel):
	"""Apply per-equipment fuel_reading_start and fuel_photo to day's equipment table. Does not save."""
	import json
	if equipment_fuel is None:
		return
	if isinstance(equipment_fuel, str):
		try:
			equipment_fuel = json.loads(equipment_fuel) if (equipment_fuel or "").strip() else None
		except Exception:
			equipment_fuel = None
	if not equipment_fuel or not isinstance(equipment_fuel, (list, tuple)):
		return
	fuel_by_asset = {}
	for e in equipment_fuel:
		asset = (e.get("asset") or "").strip()
		if not asset:
			continue
		fuel_by_asset[asset] = {
			"fuel_reading_start": (e.get("fuel_reading_start") or "").strip() or None,
			"fuel_photo_url": (e.get("fuel_photo_url") or "").strip() or None,
			"is_refill": cint(e.get("is_refill")) if e.get("is_refill") is not None else None,
			"refill_qty": flt(e.get("refill_qty"), 2) if e.get("refill_qty") is not None and e.get("refill_qty") != "" else None,
			"machinery_place": (e.get("machinery_place") or "").strip() or None,
		}
	for row in (day_doc.equipment or []):
		asset = getattr(row, "asset", None)
		if not asset or asset not in fuel_by_asset:
			continue
		vals = fuel_by_asset[asset]
		if vals.get("fuel_reading_start") is not None:
			row.fuel_reading_start = vals["fuel_reading_start"]
		if vals.get("fuel_photo_url") is not None:
			row.fuel_photo = vals["fuel_photo_url"]
		if vals.get("is_refill") is not None:
			row.is_refill = vals["is_refill"]
		if vals.get("refill_qty") is not None:
			row.refill_qty = vals["refill_qty"]
		if vals.get("machinery_place") is not None:
			row.machinery_place = vals["machinery_place"]


@frappe.whitelist()
def start_execution(
	execution_name: str,
	equipment_checklist: Optional[str] = None,
	input_checklist: Optional[str] = None,
	equipment_photo: Optional[str] = None,
	equipment_photo_urls=None,
	equipment_fuel=None,
	day_date: Optional[str] = None,
) -> str:
	"""
	Transition execution status from Ready to In Progress.
	Optionally persist pre-execution checklist (equipment/input present flags) and equipment photos.
	equipment_photo_urls: list of file URLs (multiple images). equipment_photo: legacy single base64.
	Uses row locking to prevent concurrent modification errors.
	"""
	import time
	max_retries = 3

	for attempt in range(max_retries):
		try:
			frappe.db.begin()
			doc = frappe.get_doc("Farm Task Execution", execution_name, for_update=True)

			if doc.status != "Ready":
				frappe.db.rollback()
				frappe.throw(f"Cannot start execution. Current status is {doc.status}. Only 'Ready' executions can be moved to 'In Progress'.")

			_apply_pre_execution_checklist(
				doc,
				equipment_checklist=equipment_checklist,
				input_checklist=input_checklist,
				equipment_photo=equipment_photo,
				equipment_photo_urls=equipment_photo_urls,
			)

			doc.status = "In Progress"
			doc.save(ignore_permissions=True)
			frappe.db.commit()

			# Apply equipment fuel to day (day-wise storage)
			if equipment_fuel:
				day_date_str = (day_date or "").strip() or str(getdate())
				result = get_or_create_current_day(execution_name, day_date_str)
				day_name = result.get("name")
				if day_name:
					day_doc = frappe.get_doc("Farm Task Execution Day", day_name)
					_apply_equipment_fuel_to_day(day_doc, equipment_fuel)
					day_doc.save(ignore_permissions=True)
					frappe.db.commit()

			return doc.name
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt < max_retries - 1:
				time.sleep(0.1 * (attempt + 1))
			else:
				raise
		except Exception:
			frappe.db.rollback()
			raise

	return execution_name


@frappe.whitelist()
def pause_execution(execution_name: str) -> str:
	"""
	Transition execution status from In Progress to On Hold (put on hold).
	Only allowed when status is In Progress. Sets paused_at on the document.
	"""
	import time
	max_retries = 3
	for attempt in range(max_retries):
		try:
			frappe.db.begin()
			doc = frappe.get_doc("Farm Task Execution", execution_name, for_update=True)
			if doc.status != "In Progress":
				frappe.db.rollback()
				frappe.throw(f"Cannot put on hold. Current status is {doc.status}. Only 'In Progress' executions can be put on hold.")
			doc.status = "On Hold"
			doc.paused_at = now_datetime()
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			return doc.name
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt < max_retries - 1:
				time.sleep(0.1 * (attempt + 1))
			else:
				raise
		except Exception:
			frappe.db.rollback()
			raise
	return execution_name


@frappe.whitelist()
def resume_execution(execution_name: str) -> str:
	"""
	Transition execution status from On Hold to In Progress (resume after hold).
	Only allowed when status is On Hold. Sets resumed_at on the document.
	"""
	import time
	max_retries = 3
	for attempt in range(max_retries):
		try:
			frappe.db.begin()
			doc = frappe.get_doc("Farm Task Execution", execution_name, for_update=True)
			if doc.status != "On Hold":
				frappe.db.rollback()
				frappe.throw(f"Cannot resume. Current status is {doc.status}. Only executions that are on hold can be resumed.")
			doc.status = "In Progress"
			doc.resumed_at = now_datetime()
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			return doc.name
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt < max_retries - 1:
				time.sleep(0.1 * (attempt + 1))
			else:
				raise
		except Exception:
			frappe.db.rollback()
			raise
	return execution_name


@frappe.whitelist()
def get_execution_days(execution_name: str) -> List[Dict[str, Any]]:
	"""
	Return list of Farm Task Execution Day docs (with child tables) for the given execution, ordered by date.
	Used by Update and View modals to show day list and load selected day.
	"""
	execution_name = (execution_name or "").strip()
	if not execution_name:
		return []
	names = frappe.get_all(
		"Farm Task Execution Day",
		filters={"execution": execution_name},
		fields=["name", "date", "actual_spray_water_liters", "actual_irrigation_water_liters", "remark"],
		order_by="date asc",
	)
	out = []
	fte = None
	for d in names:
		doc = frappe.get_doc("Farm Task Execution Day", d["name"])
		# Backfill: existing days created before we copied FTE child data may have no inputs/equipment; copy from FTE so UI shows tables for each day
		has_inputs = doc.get("inputs") and len(doc.inputs) > 0
		has_equipment = doc.get("equipment") and len(doc.equipment) > 0
		if not has_inputs and not has_equipment:
			if fte is None:
				fte = frappe.get_doc("Farm Task Execution", execution_name)
			if (fte.get("inputs") and len(fte.inputs) > 0) or (fte.get("equipment") and len(fte.equipment) > 0):
				_copy_fte_inputs_equipment_to_day(fte, doc)
				doc.save(ignore_permissions=True)
				frappe.db.commit()
		out.append(doc.as_dict())
	return out


def _copy_fte_inputs_equipment_to_day(fte_doc, day_doc):
	"""Copy only inputs and equipment from FTE to day (for backfilling existing days that have none)."""
	for row in (fte_doc.get("inputs") or []):
		issued = flt(row.issued_qty, 3)
		if not issued and flt(row.planned_qty, 3):
			issued = flt(row.planned_qty, 3)
		day_doc.append("inputs", {
			"item": row.item,
			"item_name": getattr(row, "item_name", None),
			"uom": getattr(row, "uom", None),
			"rate_qty": flt(row.rate_qty, 3),
			"planned_qty": flt(row.planned_qty, 3),
			"issued_qty": issued,
			"returned_qty": flt(row.returned_qty, 3),
			"consumed_qty": flt(row.consumed_qty, 3),
		})
	for row in (fte_doc.get("equipment") or []):
		day_doc.append("equipment", {
			"asset": row.asset,
			"asset_name": getattr(row, "asset_name", None),
			"planned_hours": flt(row.planned_hours, 2),
			"actual_hours": flt(row.actual_hours, 2),
			"return_type": getattr(row, "return_type", None) or "Non Returnable",
		})


def _copy_fte_child_to_day(fte_doc, day_doc):
	"""Copy FTE labour, inputs, equipment, progress_images, remark, water into a Day doc (for lazy migration)."""
	day_doc.remark = (fte_doc.remark or "").strip()
	day_doc.actual_spray_water_liters = flt(fte_doc.actual_spray_water_liters, 3)
	day_doc.actual_irrigation_water_liters = flt(fte_doc.actual_irrigation_water_liters, 3)
	for row in (fte_doc.get("labour_attendance") or []):
		day_doc.append("labour", {
			"labour_type": getattr(row, "labour_type", None) or "Farm Worker Details",
			"labour": row.labour,
			"labour_name": getattr(row, "labour_name", None),
			"role": getattr(row, "role", None),
			"checkin_in": getattr(row, "checkin_in", None),
			"checkin_out": getattr(row, "checkin_out", None),
		})
	for row in (fte_doc.get("inputs") or []):
		day_doc.append("inputs", {
			"item": row.item,
			"item_name": getattr(row, "item_name", None),
			"uom": getattr(row, "uom", None),
			"rate_qty": flt(row.rate_qty, 3),
			"planned_qty": flt(row.planned_qty, 3),
			"issued_qty": flt(row.issued_qty, 3),
			"returned_qty": flt(row.returned_qty, 3),
			"consumed_qty": flt(row.consumed_qty, 3),
		})
	for row in (fte_doc.get("equipment") or []):
		day_doc.append("equipment", {
			"asset": row.asset,
			"asset_name": getattr(row, "asset_name", None),
			"planned_hours": flt(row.planned_hours, 2),
			"actual_hours": flt(row.actual_hours, 2),
			"return_type": getattr(row, "return_type", None) or "Non Returnable",
		})
	for row in (fte_doc.get("progress_images") or []):
		day_doc.append("progress_images", {"image": row.image})


@frappe.whitelist()
def get_or_create_current_day(execution_name: str, date: str) -> Dict[str, Any]:
	"""
	If a Day for that execution + date exists, return it (with children).
	If not, create one (lazy migration: if execution has no days, create from FTE and optionally copy FTE child data).
	Only allowed when FTE status is In Progress or On Hold.
	"""
	fte = frappe.get_doc("Farm Task Execution", execution_name)
	if fte.status not in ("In Progress", "On Hold"):
		frappe.throw(f"Cannot get or create day. Current status is {fte.status}. Only 'In Progress' or 'On Hold' executions can have day data updated.")

	existing = frappe.db.get_value(
		"Farm Task Execution Day",
		{"execution": execution_name, "date": date},
		"name",
	)
	if existing:
		doc = frappe.get_doc("Farm Task Execution Day", existing)
		# Ensure delivery ticket exists for Daily Returnable equipment (idempotent)
		try:
			from f2c.farm_execution.equipment_transfer_on_completion import create_delivery_ticket_for_daily_returnable_equipment
			create_delivery_ticket_for_daily_returnable_equipment(execution_name, date)
		except Exception:
			pass
		return doc.as_dict()

	# Create new day
	day_doc = frappe.new_doc("Farm Task Execution Day")
	day_doc.execution = execution_name
	day_doc.date = date

	# Copy FTE child data (inputs, equipment, labour, etc.) into every new day so Update/End for day/End Activity show tables for all days
	existing_days = frappe.get_all(
		"Farm Task Execution Day",
		filters={"execution": execution_name},
		fields=["name"],
	)
	_copy_fte_child_to_day(fte, day_doc)

	day_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# When second day is created (we had at least one day before this insert), set execution_type to Multi Day
	if len(existing_days) >= 1 and getattr(fte, "execution_type", None) == "Single Day":
		frappe.db.set_value("Farm Task Execution", execution_name, "execution_type", "Multi Day", update_modified=False)
		frappe.db.commit()

	# Create delivery ticket (cluster -> field) for Daily Returnable equipment for this day
	try:
		from f2c.farm_execution.equipment_transfer_on_completion import create_delivery_ticket_for_daily_returnable_equipment
		create_delivery_ticket_for_daily_returnable_equipment(execution_name, date)
	except Exception as e:
		frappe.log_error(
			title="Farm Task Execution Day Delivery",
			message=f"Delivery ticket for daily returnable equipment failed for {execution_name} day {date}: {str(e)}",
		)

	return day_doc.as_dict()


def create_dummy_execution_days_for_testing(execution_name: str = None, num_days: int = None) -> List[str]:
	"""
	[Dev/testing only - not whitelisted] Create dummy Farm Task Execution Day records for an execution.
	Use via bench execute script: f2c.scripts.create_dummy_execution_days.run
	For testing day-wise view/update and consolidated multi-day view.
	If execution_name is None, uses the first Farm Task Execution with status In Progress.
	Creates num_days days (default 3) ending today: today, yesterday, day before, ...
	Each day gets: dummy remark; labour from first available Farm Worker Details; equipment from FTE equipment or first Assets.
	Returns list of day docnames (created or existing).
	"""
	from frappe.utils import add_days, getdate

	if not execution_name:
		names = frappe.get_all(
			"Farm Task Execution",
			filters={"status": ["in", ["In Progress", "On Hold"]]},
			fields=["name"],
			limit=1,
		)
		if not names:
			frappe.throw("No In Progress or On Hold execution found. Create one or pass execution_name.")
		execution_name = names[0].name
	else:
		if not frappe.db.exists("Farm Task Execution", execution_name):
			frappe.throw(
				f"Farm Task Execution '{execution_name}' not found. Omit execution_name to use the first In Progress execution."
			)

	fte = frappe.get_doc("Farm Task Execution", execution_name)
	if fte.status not in ("In Progress", "On Hold"):
		frappe.throw(f"Execution {execution_name} status is {fte.status}. Only In Progress or On Hold can have day data.")

	num_days = cint(num_days)
	if num_days < 1:
		num_days = 3
	if num_days > 31:
		num_days = 31

	today = getdate()
	dates_to_create = [add_days(today, -i) for i in range(num_days - 1, -1, -1)]  # oldest first: today-(n-1) .. today
	created = []

	# Labour: up to 3 Farm Worker Details
	labour_rows = frappe.get_all(
		"Farm Worker Details",
		fields=["name", "worker_name"],
		limit=3,
	)
	labour_list = [{"labour": r.name, "labour_name": r.worker_name} for r in labour_rows] if labour_rows else []

	# Equipment: from FTE's equipment if any, else first 2 Assets
	equipment_rows = []
	if getattr(fte, "equipment", None) and len(fte.equipment) > 0:
		for eq in fte.equipment[:2]:
			equipment_rows.append({
				"asset": eq.asset,
				"asset_name": getattr(eq, "asset_name", None) or eq.asset,
				"planned_hours": flt(getattr(eq, "planned_hours", None), 2) or 1,
				"actual_hours": 1,
				"return_type": getattr(eq, "return_type", None) or "Non Returnable",
			})
	if not equipment_rows and frappe.db.table_exists("Asset"):
		assets = frappe.get_all("Asset", fields=["name", "asset_name"], limit=2)
		for a in assets:
			equipment_rows.append({
				"asset": a.name,
				"asset_name": a.asset_name or a.name,
				"planned_hours": 1,
				"actual_hours": 1,
				"return_type": "Non Returnable",
			})

	for d in dates_to_create:
		date_str = d.strftime("%Y-%m-%d")
		existing = frappe.db.get_value(
			"Farm Task Execution Day",
			{"execution": execution_name, "date": date_str},
			"name",
		)
		if existing:
			created.append(existing)
			continue
		day_doc = frappe.new_doc("Farm Task Execution Day")
		day_doc.execution = execution_name
		day_doc.date = date_str
		day_doc.remark = f"Dummy day for testing ({date_str})"
		for lab in labour_list:
			day_doc.append("labour", {"labour_type": "Farm Worker Details", "labour": lab["labour"], "labour_name": lab.get("labour_name")})
		for eq in equipment_rows:
			day_doc.append("equipment", {
				"asset": eq["asset"],
				"asset_name": eq.get("asset_name"),
				"planned_hours": eq.get("planned_hours", 1),
				"actual_hours": eq.get("actual_hours", 1),
				"return_type": eq.get("return_type") or "Non Returnable",
			})
		day_doc.insert(ignore_permissions=True)
		created.append(day_doc.name)

	# Ensure execution_type is Multi Day when we have more than one day
	if len(created) >= 2 and getattr(fte, "execution_type", None) == "Single Day":
		frappe.db.set_value("Farm Task Execution", execution_name, "execution_type", "Multi Day", update_modified=False)

	frappe.db.commit()
	return created


@frappe.whitelist()
def update_day_data(
	execution_name: str,
	day_date: str,
	labour: List[Dict[str, Any]] | str | None = None,
	inputs: List[Dict[str, Any]] | str | None = None,
	equipment: List[Dict[str, Any]] | str | None = None,
	equipment_fuel=None,
	actual_spray_water_liters: Optional[float] = None,
	actual_irrigation_water_liters: Optional[float] = None,
	remark: Optional[str] = None,
	progress_images: List[Dict[str, Any]] | str | None = None,
	ended_for_day: Optional[bool] = None,
	activity_start_time: Optional[str] = None,
	activity_end_time: Optional[str] = None,
) -> str:
	"""
	Load or create Farm Task Execution Day for (execution_name, day_date), update child tables and fields, save.
	Only allowed when FTE status is In Progress or On Hold.
	"""
	import json
	if isinstance(labour, str):
		labour = json.loads(labour) if labour else None
	if isinstance(inputs, str):
		inputs = json.loads(inputs) if inputs else None
	if isinstance(equipment, str):
		equipment = json.loads(equipment) if equipment else None
	if isinstance(progress_images, str):
		progress_images = json.loads(progress_images) if progress_images else None

	fte = frappe.get_doc("Farm Task Execution", execution_name)
	if fte.status not in ("In Progress", "On Hold"):
		frappe.throw(f"Cannot update day data. Current status is {fte.status}. Only 'In Progress' or 'On Hold' executions can be updated.")

	day_name = frappe.db.get_value(
		"Farm Task Execution Day",
		{"execution": execution_name, "date": day_date},
		"name",
	)
	if not day_name:
		# Create via get_or_create (which handles lazy migration)
		result = get_or_create_current_day(execution_name, day_date)
		day_name = result.get("name")

	day_doc = frappe.get_doc("Farm Task Execution Day", day_name)

	if remark is not None:
		day_doc.remark = str(remark).strip()
	if actual_spray_water_liters is not None:
		day_doc.actual_spray_water_liters = flt(actual_spray_water_liters, 3)
	if actual_irrigation_water_liters is not None:
		day_doc.actual_irrigation_water_liters = flt(actual_irrigation_water_liters, 3)

	# Activity start/end time (datetime, same timezone handling as labour check-in times)
	if activity_start_time is not None:
		_val = None
		if activity_start_time and str(activity_start_time).strip():
			try:
				_val = get_datetime(activity_start_time)
				if getattr(_val, "tzinfo", None):
					_val = convert_utc_to_system_timezone(_val).replace(tzinfo=None)
			except Exception:
				_val = None
		day_doc.activity_start_time = _val
	if activity_end_time is not None:
		_val = None
		if activity_end_time and str(activity_end_time).strip():
			try:
				_val = get_datetime(activity_end_time)
				if getattr(_val, "tzinfo", None):
					_val = convert_utc_to_system_timezone(_val).replace(tzinfo=None)
			except Exception:
				_val = None
		day_doc.activity_end_time = _val

	if labour is not None and isinstance(labour, list):
		day_doc.labour = []
		for row in labour:
			# Support both dict rows and plain string (labour id only)
			if isinstance(row, dict):
				labour_id = row.get("labour")
			else:
				labour_id = row
			if not labour_id:
				continue
			labour_type = "Farm Worker Details"
			if isinstance(row, dict) and row.get("labour_type"):
				labour_type = str(row.get("labour_type")).strip() or "Farm Worker Details"

			# Auto-fill labour_name when not provided
			labour_name = row.get("labour_name") if isinstance(row, dict) else None
			if not labour_name:
				try:
					if labour_type == "Employee":
						labour_name = frappe.db.get_value("Employee", labour_id, "employee_name") or labour_id
					else:
						labour_name = frappe.db.get_value("Farm Worker Details", labour_id, "worker_name") or labour_id
				except Exception:
					labour_name = labour_id

			# Auto-fill role when not provided
			role_val = row.get("role") if isinstance(row, dict) else None
			if not role_val:
				try:
					if labour_type == "Employee":
						role_val = frappe.db.get_value("Employee", labour_id, "designation") or "Employee"
					else:
						roles_raw = frappe.db.get_value("Farm Worker Details", labour_id, "worker_roles") or ""
						# take first token (newline or comma)
						first = [s.strip() for s in str(roles_raw).replace(",", "\n").splitlines() if s.strip()]
						role_val = first[0] if first else "Daily Worker"
				except Exception:
					role_val = role_val or None
			# Parse check-in/check-out times (ISO string or None). MySQL DATETIME is naive;
			# convert timezone-aware values to system timezone and strip tzinfo.
			checkin_in_time = None
			checkin_out_time = None
			if isinstance(row, dict):
				ci = row.get("checkin_in_time")
				co = row.get("checkin_out_time")
				if ci is not None and ci != "":
					try:
						checkin_in_time = get_datetime(ci)
						if getattr(checkin_in_time, "tzinfo", None):
							checkin_in_time = convert_utc_to_system_timezone(checkin_in_time).replace(tzinfo=None)
					except Exception:
						checkin_in_time = None
				if co is not None and co != "":
					try:
						checkin_out_time = get_datetime(co)
						if getattr(checkin_out_time, "tzinfo", None):
							checkin_out_time = convert_utc_to_system_timezone(checkin_out_time).replace(tzinfo=None)
					except Exception:
						checkin_out_time = None
			day_doc.append("labour", {
				"labour_type": labour_type,
				"labour": labour_id,
				"labour_name": labour_name,
				"role": role_val,
				"checkin_in": row.get("checkin_in") if isinstance(row, dict) else None,
				"checkin_out": row.get("checkin_out") if isinstance(row, dict) else None,
				"checkin_in_time": checkin_in_time,
				"checkin_out_time": checkin_out_time,
			})
	if inputs is not None and isinstance(inputs, list):
		day_doc.inputs = []
		for row in inputs:
			day_doc.append("inputs", {
				"item": row.get("item"),
				"item_name": row.get("item_name"),
				"uom": row.get("uom"),
				"rate_qty": flt(row.get("rate_qty"), 3),
				"planned_qty": flt(row.get("planned_qty"), 3),
				"issued_qty": flt(row.get("issued_qty"), 3),
				"returned_qty": flt(row.get("returned_qty"), 3),
				"consumed_qty": flt(row.get("consumed_qty"), 3),
			})
	if equipment is not None and isinstance(equipment, list):
		day_doc.equipment = []
		for row in equipment:
			day_doc.append("equipment", {
				"asset": row.get("asset"),
				"asset_name": row.get("asset_name"),
				"planned_hours": flt(row.get("planned_hours"), 2),
				"actual_hours": flt(row.get("actual_hours"), 2),
				"return_type": row.get("return_type") or "Non Returnable",
				"is_refill": cint(row.get("is_refill")) if row.get("is_refill") is not None else 0,
				"refill_qty": flt(row.get("refill_qty"), 2) if row.get("refill_qty") is not None and row.get("refill_qty") != "" else None,
				"machinery_place": (row.get("machinery_place") or "").strip() or None,
				"fuel_reading_start": (row.get("fuel_reading_start") or "").strip() or None,
				"fuel_photo": (row.get("fuel_photo") or "").strip() or None,
				"fuel_reading_end": (row.get("fuel_reading_end") or "").strip() or None,
				"fuel_photo_end": (row.get("fuel_photo_end") or "").strip() or None,
				"fuel_consumption": flt(row.get("fuel_consumption"), 2),
			})
	if progress_images is not None and isinstance(progress_images, list):
		day_doc.progress_images = []
		for row in progress_images:
			if row.get("image"):
				day_doc.append("progress_images", {"image": row["image"]})

	_apply_equipment_fuel_to_day(day_doc, equipment_fuel)

	if ended_for_day is not None:
		day_doc.ended_for_day = cint(ended_for_day)
		if day_doc.ended_for_day:
			# Set check-out time for all labour rows that don't have it
			now_ts = now_datetime()
			for row in day_doc.labour:
				if not row.get("checkin_out_time"):
					row.checkin_out_time = now_ts

	day_doc.save(ignore_permissions=True)
	frappe.db.commit()
	# Create equipment transfer tickets on End of the day: only Daily Returnable -> return to cluster
	if ended_for_day:
		try:
			from f2c.farm_execution.equipment_transfer_on_completion import create_equipment_transfer_tickets_for_execution
			create_equipment_transfer_tickets_for_execution(fte, on_end_of_day=True)
		except Exception as e:
			frappe.log_error(
				f"Equipment transfer tickets on end of day failed for {execution_name}: {str(e)}",
				"Farm Task Execution Equipment Transfer",
			)
		# Create consumption stock entry for this day (reduce field warehouse stock by consumed qty)
		try:
			_create_consumption_stock_entry_for_day(execution_name, day_date)
		except Exception as e:
			frappe.log_error(
				f"Consumption stock entry on end of day failed for {execution_name} day {day_date}: {str(e)}",
				"Farm Task Execution Day Consumption",
			)
	return day_doc.name


@frappe.whitelist()
def save_day_progress_images(execution_name: str, day_date: str, progress_images: List[Dict[str, Any]] | str) -> str:
	"""Update only progress_images for the Day (execution_name, day_date). Only when FTE status is In Progress or On Hold."""
	import json
	if isinstance(progress_images, str):
		progress_images = json.loads(progress_images) if progress_images else []

	fte = frappe.get_doc("Farm Task Execution", execution_name)
	if fte.status not in ("In Progress", "On Hold"):
		frappe.throw(f"Cannot save day progress images. Current status is {fte.status}.")

	day_name = frappe.db.get_value(
		"Farm Task Execution Day",
		{"execution": execution_name, "date": day_date},
		"name",
	)
	if not day_name:
		result = get_or_create_current_day(execution_name, day_date)
		day_name = result.get("name")

	day_doc = frappe.get_doc("Farm Task Execution Day", day_name)
	day_doc.progress_images = []
	for row in (progress_images or []):
		if row.get("image"):
			day_doc.append("progress_images", {"image": row["image"]})
	day_doc.save(ignore_permissions=True)
	frappe.db.commit()
	return day_doc.name


@frappe.whitelist()
def get_pre_execution_availability(execution_name: str) -> Dict[str, List[Dict[str, Any]]]:
	"""
	Return equipment and inputs with an 'available' flag based on field warehouse.
	Equipment: available if asset is at the field warehouse's location.
	Inputs: available if stock at field warehouse >= required qty (planned_qty or qty_to_issue).
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	field = getattr(doc, "field", None) or ""
	if not field:
		return {"equipment": [], "inputs": []}

	from f2c.farm_execution.equipment_transfer_on_completion import get_target_warehouse_for_field
	from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse

	field_warehouse = get_target_warehouse_for_field(field)
	if not field_warehouse:
		# No field warehouse: return structure with all not available
		equipment = []
		for row in (doc.equipment or []):
			asset = getattr(row, "asset", None)
			if asset:
				asset_category = frappe.db.get_value("Asset", asset, "asset_category") or ""
				equipment.append({
					"asset": asset,
					"asset_name": getattr(row, "asset_name") or asset,
					"available": False,
					"asset_category": asset_category,
				})
		inputs = []
		for row in (doc.inputs or []):
			item = getattr(row, "item", None)
			if item:
				inputs.append({
					"item": item,
					"item_name": getattr(row, "item_name") or item,
					"required_qty": flt(getattr(row, "planned_qty") or getattr(row, "qty_to_issue") or 0),
					"available_qty": 0,
					"available": False,
				})
		return {"equipment": equipment, "inputs": inputs}

	location_result = get_location_for_warehouse(field_warehouse)
	warehouse_location = (location_result or {}).get("location") if location_result else None

	# Equipment: available if asset.location == warehouse_location
	seen_assets = set()
	equipment = []
	for row in doc.equipment or []:
		asset = (getattr(row, "asset", None) or "").strip()
		if not asset or asset in seen_assets:
			continue
		seen_assets.add(asset)
		available = False
		if warehouse_location:
			asset_location = frappe.db.get_value("Asset", asset, "location")
			available = bool(asset_location and asset_location == warehouse_location)
		asset_category = frappe.db.get_value("Asset", asset, "asset_category") or ""
		equipment.append({
			"asset": asset,
			"asset_name": getattr(row, "asset_name") or asset,
			"available": available,
			"asset_category": asset_category,
		})

	# Inputs: available if get_stock_balance(item, field_warehouse) >= required
	from erpnext.stock.utils import get_stock_balance

	inputs = []
	for row in doc.inputs or []:
		item = getattr(row, "item", None)
		if not item:
			continue
		required_qty = flt(getattr(row, "planned_qty") or getattr(row, "qty_to_issue") or 0)
		available_qty = 0
		try:
			bal = get_stock_balance(item, field_warehouse)
			if bal is not None:
				available_qty = flt(bal)
		except Exception:
			pass
		available = available_qty >= required_qty if required_qty else (available_qty > 0)
		inputs.append({
			"item": item,
			"item_name": getattr(row, "item_name") or item,
			"required_qty": required_qty,
			"available_qty": available_qty,
			"available": available,
		})

	return {"equipment": equipment, "inputs": inputs}


@frappe.whitelist()
def get_warehouse_for_issued_qty(execution_name: str):
	"""
	Return the field warehouse for displaying Issued Qty in the Inputs table.
	Issued Qty shows available stock at the field warehouse only (activity field's target warehouse).
	Returns None if execution has no field or field has no linked warehouse.
	"""
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	field = getattr(doc, "field", None) or ""
	if not field:
		return None
	from f2c.farm_execution.equipment_transfer_on_completion import get_target_warehouse_for_field
	return get_target_warehouse_for_field(field)


@frappe.whitelist()
def get_stock_at_warehouse_for_execution(execution_name: str) -> Dict[str, Any]:
	"""
	Return field warehouse and list of stock items with balance at that warehouse
	for the execution's field, restricted to items related to the activity (inputs on
	execution or on any execution day). Used by Return Equipment modal to show stock table.
	"""
	execution_name = (execution_name or "").strip()
	if not execution_name:
		return {"warehouse": None, "stock_items": []}
	try:
		doc = frappe.get_doc("Farm Task Execution", execution_name)
		field = getattr(doc, "field", None) or ""
		if not field:
			return {"warehouse": None, "stock_items": []}
		from f2c.farm_execution.equipment_transfer_on_completion import get_target_warehouse_for_field
		warehouse = get_target_warehouse_for_field(field)
		if not warehouse:
			return {"warehouse": None, "stock_items": []}
		# Item codes related to this activity (execution inputs + all days' inputs)
		activity_item_codes = set()
		for row in (doc.get("inputs") or []):
			item = getattr(row, "item", None) or (row.get("item") if isinstance(row, dict) else None)
			if item:
				activity_item_codes.add(str(item).strip())
		day_names = frappe.get_all(
			"Farm Task Execution Day",
			filters={"execution": execution_name},
			fields=["name"],
			order_by="date asc",
		)
		for d in day_names:
			day_doc = frappe.get_doc("Farm Task Execution Day", d["name"])
			for row in (day_doc.get("inputs") or []):
				item = getattr(row, "item", None) or (row.get("item") if isinstance(row, dict) else None)
				if item:
					activity_item_codes.add(str(item).strip())
		# Real-time balance from Bin, only for activity-related items
		if not activity_item_codes:
			return {"warehouse": warehouse, "stock_items": []}
		bins = frappe.get_all(
			"Bin",
			filters={"warehouse": warehouse, "actual_qty": [">", 0], "item_code": ["in", list(activity_item_codes)]},
			fields=["item_code", "actual_qty"],
			limit_page_length=500,
		)
		stock_items = []
		for b in bins:
			item_code = b.get("item_code")
			qty = flt(b.get("actual_qty"), 3)
			if not item_code or qty <= 0:
				continue
			item_name = frappe.db.get_value("Item", item_code, "item_name")
			stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
			stock_items.append({
				"item_code": item_code,
				"item_name": item_name or item_code,
				"qty": qty,
				"stock_uom": stock_uom or "",
			})
		return {"warehouse": warehouse, "stock_items": stock_items}
	except Exception as e:
		frappe.log_error(
			title="get_stock_at_warehouse_for_execution",
			message=str(e),
		)
		return {"warehouse": None, "stock_items": []}


@frappe.whitelist()
def update_execution_data(
	execution_name: str,
	inputs: List[Dict[str, Any]] | str | None = None,
	equipment: List[Dict[str, Any]] | str | None = None,
	labour_attendance: List[Dict[str, Any]] | str | None = None,
	planned_male_count: int | None = None,
	planned_female_count: int | None = None,
	remark: str | None = None,
) -> str:
	"""
	Update execution child tables (inputs, equipment, labour_attendance), optional planned counts, and optional remark.
	Used by the Update Activity modal before or when submitting for review.
	Only allowed for In Progress executions. Updates only writable fields; preserves row identity by index.
	"""
	import json
	if isinstance(inputs, str):
		inputs = json.loads(inputs) if inputs else None
	if isinstance(equipment, str):
		equipment = json.loads(equipment) if equipment else None
	if isinstance(labour_attendance, str):
		labour_attendance = json.loads(labour_attendance) if labour_attendance else None

	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if doc.status not in ("In Progress", "On Hold"):
		frappe.throw(f"Cannot update execution data. Current status is {doc.status}. Only 'In Progress' or 'On Hold' (on hold) executions can be updated.")

	if inputs is not None and isinstance(inputs, list):
		doc_inputs = doc.get("inputs") or []
		for i, row in enumerate(inputs):
			child = None
			if row.get("item"):
				for c in doc_inputs:
					if c.get("item") == row.get("item"):
						child = c
						break
			if child is None and isinstance(row.get("_idx"), (int, float)):
				idx = int(row["_idx"])
				if 0 <= idx < len(doc_inputs):
					child = doc_inputs[idx]
			if child is None and i < len(doc_inputs):
				child = doc_inputs[i]
			if child is None:
				continue
			if "issued_qty" in row and row["issued_qty"] is not None:
				child.issued_qty = flt(row["issued_qty"], 3)
			if "returned_qty" in row and row["returned_qty"] is not None:
				child.returned_qty = flt(row["returned_qty"], 3)
			if "consumed_qty" in row and row["consumed_qty"] is not None:
				child.consumed_qty = flt(row["consumed_qty"], 3)

	if equipment is not None and isinstance(equipment, list):
		doc_equipment = doc.get("equipment") or []
		for i, row in enumerate(equipment):
			child = None
			if row.get("asset"):
				for c in doc_equipment:
					if c.get("asset") == row.get("asset"):
						child = c
						break
			if child is None and isinstance(row.get("_idx"), (int, float)):
				idx = int(row["_idx"])
				if 0 <= idx < len(doc_equipment):
					child = doc_equipment[idx]
			if child is None and i < len(doc_equipment):
				child = doc_equipment[i]
			if child is None:
				continue
			if "actual_hours" in row and row["actual_hours"] is not None:
				child.actual_hours = flt(row["actual_hours"], 2)
			if "return_type" in row:
				child.return_type = str(row["return_type"]).strip() if row["return_type"] else "Non Returnable"

	if labour_attendance is not None and isinstance(labour_attendance, list):
		for i, row in enumerate(labour_attendance):
			if i >= len(doc.labour_attendance):
				break
			child = doc.labour_attendance[i]
			if "role" in row:
				child.role = str(row["role"]) if row["role"] is not None else ""

	if planned_male_count is not None and str(planned_male_count).strip() != "":
		try:
			doc.planned_male_count = int(planned_male_count)
		except (TypeError, ValueError):
			pass
	if planned_female_count is not None and str(planned_female_count).strip() != "":
		try:
			doc.planned_female_count = int(planned_female_count)
		except (TypeError, ValueError):
			pass

	if remark is not None:
		doc.remark = str(remark).strip() if remark else ""

	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def submit_for_review(execution_name: str, skip_images: int = 0) -> str:
	"""
	Transition execution status from In Progress to In Review.
	Uses row locking to prevent concurrent modification errors.
	"""
	import time
	max_retries = 3
	
	for attempt in range(max_retries):
		try:
			# Begin transaction and lock the row to prevent concurrent modifications
			frappe.db.begin()
			doc = frappe.get_doc("Farm Task Execution", execution_name, for_update=True)
			
			if doc.status != "In Progress":
				frappe.db.rollback()
				if doc.status == "On Hold":
					frappe.throw("Cannot submit for review while execution is on hold. Please Resume the execution first.")
				frappe.throw(f"Cannot submit for review. Current status is {doc.status}. Only 'In Progress' executions can be moved to 'In Review'.")
			
			# Optional bypass for progress-image requirement (requested UX)
			frappe.flags.skip_progress_image_min = bool(int(skip_images or 0))
			
			doc.status = "In Review"
			# Set actual_end when submitting for review
			if not doc.actual_end:
				doc.actual_end = now_datetime()
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			# Create equipment transfer tickets on End Activity: same-cluster next field or return to cluster
			try:
				from f2c.farm_execution.equipment_transfer_on_completion import create_equipment_transfer_tickets_for_execution
				create_equipment_transfer_tickets_for_execution(doc)
			except Exception as e:
				frappe.log_error(
					f"Equipment transfer tickets on submit for review failed for {doc.name}: {str(e)}",
					"Farm Task Execution Equipment Transfer",
				)
			# Create consumption stock entry for each day that does not have one yet (reduce field warehouse stock)
			days_list = frappe.get_all(
				"Farm Task Execution Day",
				filters={"execution": execution_name},
				fields=["name", "date", "consumption_stock_entry"],
				order_by="date asc",
			)
			for d in days_list:
				if d.get("consumption_stock_entry"):
					continue
				day_date_str = d.get("date")
				if day_date_str:
					try:
						_create_consumption_stock_entry_for_day(execution_name, day_date_str)
					except Exception as e:
						# Never fail submit_for_review due to logging/title length limits
						try:
							frappe.log_error(
								message=(
									f"Consumption stock entry on submit for review failed.\n"
									f"Execution: {doc.name}\n"
									f"Day: {day_date_str}\n"
									f"Error: {str(e)}\n\n"
									f"{frappe.get_traceback()}"
								),
								title=f"Day consumption failed: {doc.name} {day_date_str}"[:140],
							)
						except Exception:
							# If even error logging fails, continue silently (status already moved to In Review)
							pass
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
def approve_execution(execution_name: str) -> str:
	"""
	Approve execution and transition status from In Review to Completed.
	Uses row locking to prevent concurrent modification errors.
	"""
	import time
	max_retries = 3
	
	for attempt in range(max_retries):
		try:
			# Begin transaction and lock the row to prevent concurrent modifications
			frappe.db.begin()
			doc = frappe.get_doc("Farm Task Execution", execution_name, for_update=True)
			
			if doc.status != "In Review":
				frappe.db.rollback()
				frappe.throw(f"Cannot approve execution. Current status is {doc.status}. Only 'In Review' executions can be approved and moved to 'Completed'.")
			
			doc.status = "Completed"
			# Ensure actual_end is set
			if not doc.actual_end:
				doc.actual_end = now_datetime()
			# consumed_qty is user input; no recompute
			doc._compute_consumed_qty()
			doc.save(ignore_permissions=True)
			
			# Explicitly update linked On Demand Activity status after save
			# This ensures the status is updated even if on_update didn't trigger correctly
			if doc.on_demand_activity_ref:
				try:
					activity_status = frappe.db.get_value("On Demand Activity", doc.on_demand_activity_ref, "status")
					if activity_status and activity_status not in ("Aborted", "Completed", "Archived"):
						frappe.db.set_value("On Demand Activity", doc.on_demand_activity_ref, "status", "Completed", update_modified=False)
				except Exception as e:
					# Log error but don't fail the approval
					frappe.log_error(f"Error updating On Demand Activity status for {doc.on_demand_activity_ref}: {str(e)}", "Approve Execution Error")
			
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
def get_pending_return_tickets_for_execution(execution_name: str) -> List[str]:
	"""
	Return list of Logistics Transfer Ticket names that are linked to this execution
	and have status "Pending Pickup" (initial state, not yet dispatched).
	"""
	if not (execution_name or "").strip():
		return []
	if not frappe.db.has_column("Logistics Transfer Ticket", "farm_task_execution"):
		return []
	tickets = frappe.get_all(
		"Logistics Transfer Ticket",
		filters={
			"farm_task_execution": execution_name.strip(),
			"status": "Pending Pickup",
		},
		fields=["name"],
		limit_page_length=100,
	)
	return [t["name"] for t in tickets if t.get("name")]


def get_non_cancelled_tickets_for_execution(execution_name: str) -> List[str]:
	"""
	Return list of Logistics Transfer Ticket names linked to this execution
	with status not Cancelled (any active ticket).
	"""
	if not (execution_name or "").strip():
		return []
	if not frappe.db.has_column("Logistics Transfer Ticket", "farm_task_execution"):
		return []
	tickets = frappe.get_all(
		"Logistics Transfer Ticket",
		filters={
			"farm_task_execution": execution_name.strip(),
			"status": ["!=", "Cancelled"],
		},
		fields=["name"],
		limit_page_length=500,
	)
	return [t["name"] for t in tickets if t.get("name")]


def _update_child_equipment_return_type(row_name: str, return_type: str) -> None:
	"""
	Find the equipment child row by name (in Farm Task Execution or Farm Task Execution Day)
	and set its return_type. Saves the parent doc.
	"""
	valid_types = ("Returnable", "Non Returnable", "Daily Returnable", "End Activity Returnable")
	return_type = (return_type or "").strip() or "Non Returnable"
	if return_type not in valid_types:
		return_type = "Non Returnable"
	for doctype in ("Farm Task Execution Equipment", "Farm Task Execution Day Equipment"):
		if not frappe.db.exists(doctype, row_name):
			continue
		parent = frappe.db.get_value(doctype, row_name, "parent")
		parenttype = frappe.db.get_value(doctype, row_name, "parenttype")
		if not parent or not parenttype:
			continue
		parent_doc = frappe.get_doc(parenttype, parent)
		for row in parent_doc.get("equipment") or []:
			if getattr(row, "name", None) == row_name:
				row.return_type = return_type
				parent_doc.save(ignore_permissions=True)
				frappe.db.commit()
				return
		break
	return


def _update_child_input_return_type(row_name: str, return_type: str) -> None:
	"""
	Find the input child row by name (in Farm Task Execution or Farm Task Execution Day)
	and set its return_type. Saves the parent doc.
	"""
	valid_types = ("Returnable", "Non Returnable", "Daily Returnable", "End Activity Returnable")
	return_type = (return_type or "").strip() or "Non Returnable"
	if return_type not in valid_types:
		return_type = "Non Returnable"
	for doctype in ("Farm Task Execution Input", "Farm Task Execution Day Input"):
		if not frappe.db.exists(doctype, row_name):
			continue
		parent = frappe.db.get_value(doctype, row_name, "parent")
		parenttype = frappe.db.get_value(doctype, row_name, "parenttype")
		if not parent or not parenttype:
			continue
		parent_doc = frappe.get_doc(parenttype, parent)
		child_attr = "inputs"
		for row in parent_doc.get(child_attr) or []:
			if getattr(row, "name", None) == row_name:
				row.return_type = return_type
				parent_doc.save(ignore_permissions=True)
				frappe.db.commit()
				return
		break
	return


@frappe.whitelist()
def update_input_return_types(execution_name: str, input_updates: List[Dict[str, Any]] | str = None) -> str:
	"""
	Update return_type on execution (and day) input rows.
	Allowed when execution status is In Review.
	input_updates: list of {"name": "<child_row_name>", "return_type": "Returnable"|...}.
	"""
	import json
	execution_name = (execution_name or "").strip()
	if not execution_name:
		frappe.throw("execution_name is required")
	if isinstance(input_updates, str):
		input_updates = json.loads(input_updates) if input_updates else []
	if not isinstance(input_updates, list):
		input_updates = []
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if doc.status != "In Review":
		frappe.throw(
			f"Cannot update input return types. Execution status is {doc.status}. Only 'In Review' executions are allowed."
		)
	# Resolve execution's own input row names and its days' input row names so we only update those
	valid_parents = {execution_name}
	for d in frappe.get_all(
		"Farm Task Execution Day",
		filters={"execution": execution_name},
		fields=["name"],
	):
		valid_parents.add(d["name"])
	for item in input_updates:
		row_name = (item.get("name") or "").strip() if isinstance(item, dict) else None
		return_type = (item.get("return_type") or "").strip() if isinstance(item, dict) else ""
		if not row_name:
			continue
		for doctype in ("Farm Task Execution Input", "Farm Task Execution Day Input"):
			if not frappe.db.exists(doctype, row_name):
				continue
			parent = frappe.db.get_value(doctype, row_name, "parent")
			if parent not in valid_parents:
				continue
			_update_child_input_return_type(row_name, return_type)
			break
	return execution_name


@frappe.whitelist()
def update_equipment_return_types_and_sync_tickets(
	execution_name: str,
	equipment_updates: List[Dict[str, Any]] | str = None,
) -> str:
	"""
	Update return_type on execution (and day) equipment rows. Then:
	- For each existing LTT linked to this execution: remove from the ticket any assets
	  that are now Non Returnable; cancel the ticket if no assets remain.
	- Create new return ticket(s) only for assets that are Returnable/End Activity Returnable
	  and do not already have a ticket.
	Allowed when execution status is In Review.
	equipment_updates: list of {"name": "<child_row_name>", "return_type": "Returnable"|...}.
	"""
	import json
	execution_name = (execution_name or "").strip()
	if not execution_name:
		frappe.throw("execution_name is required")
	if isinstance(equipment_updates, str):
		equipment_updates = json.loads(equipment_updates) if equipment_updates else []
	if not isinstance(equipment_updates, list):
		equipment_updates = []

	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if doc.status != "In Review":
		frappe.throw(f"Cannot update return equipment. Execution status is {doc.status}. Only 'In Review' executions can update return types and sync tickets.")

	for item in equipment_updates:
		row_name = (item.get("name") or "").strip() if isinstance(item, dict) else None
		return_type = (item.get("return_type") or "").strip() if isinstance(item, dict) else ""
		if row_name:
			_update_child_equipment_return_type(row_name, return_type)

	# Build current return_type per asset from execution + days (after updates)
	doc.reload()
	execution_doc = frappe.get_doc("Farm Task Execution", execution_name)
	days = frappe.get_all(
		"Farm Task Execution Day",
		filters={"execution": execution_name},
		fields=["name"],
		order_by="date asc",
	)
	seen_assets: Dict[str, str] = {}
	if days:
		for d in days:
			day_doc = frappe.get_doc("Farm Task Execution Day", d["name"])
			for row in day_doc.get("equipment") or []:
				asset = getattr(row, "asset", None)
				if asset:
					seen_assets[asset] = (getattr(row, "return_type", None) or "").strip() or "Non Returnable"
	else:
		for row in execution_doc.get("equipment") or []:
			asset = getattr(row, "asset", None)
			if asset:
				seen_assets[asset] = (getattr(row, "return_type", None) or "").strip() or "Non Returnable"

	# Update existing LTTs: remove assets that are now Non Returnable; cancel ticket if no assets left
	from f2c.inventory.logistics_transfer_ticket_api import mark_cancelled
	linked_ticket_names = get_non_cancelled_tickets_for_execution(execution_name)
	non_returnable_assets = {a for a, rtype in seen_assets.items() if rtype == "Non Returnable"}
	for ticket_name in linked_ticket_names:
		try:
			ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
			to_remove = [
				row for row in (ticket_doc.get("asset_items") or [])
				if getattr(row, "asset", None) in non_returnable_assets
			]
			for row in to_remove:
				ticket_doc.remove(row)
			if not (ticket_doc.get("asset_items") or []):
				mark_cancelled(ticket_name, reason="Return types updated: all equipment set to Non Returnable")
			else:
				ticket_doc.save(ignore_permissions=True)
				frappe.db.commit()
		except Exception as e:
			frappe.log_error(
				title="Return Equipment Sync",
				message=f"Failed to update LTT {ticket_name}: {e}",
			)

	# Fallback: process same-day same-route LTTs not linked to this execution (e.g. single-item ticket missing link)
	# Remove non-returnable assets from them; cancel if no assets left. Handles tickets that were not found by get_non_cancelled_tickets_for_execution.
	if non_returnable_assets:
		field = getattr(execution_doc, "field", None)
		if field:
			try:
				from f2c.farm_execution.equipment_transfer_on_completion import (
					get_target_warehouse_for_field,
					get_cluster_warehouse_for_field,
				)
				from_warehouse = get_target_warehouse_for_field(field)
				to_warehouse = get_cluster_warehouse_for_field(field)
				if from_warehouse and to_warehouse:
					today_obj = getdate(now_datetime())
					linked_set = set(linked_ticket_names)
					candidates = frappe.get_all(
						"Logistics Transfer Ticket",
						filters={
							"from_warehouse": from_warehouse,
							"to_warehouse": to_warehouse,
							"status": ["!=", "Cancelled"],
						},
						fields=["name", "creation"],
						limit_page_length=200,
					)
					for t in candidates:
						if not t.get("name") or t["name"] in linked_set:
							continue
						if getdate(t.get("creation")) != today_obj:
							continue
						try:
							ticket_doc = frappe.get_doc("Logistics Transfer Ticket", t["name"])
							to_remove = [
								row for row in (ticket_doc.get("asset_items") or [])
								if getattr(row, "asset", None) in non_returnable_assets
							]
							for row in to_remove:
								ticket_doc.remove(row)
							if not (ticket_doc.get("asset_items") or []):
								mark_cancelled(
									t["name"],
									reason="Return types updated: all equipment set to Non Returnable (same-day same-route)",
								)
							elif to_remove:
								ticket_doc.save(ignore_permissions=True)
								frappe.db.commit()
						except Exception as unlink_err:
							frappe.log_error(
								title="Return Equipment Sync",
								message=f"Failed to update unlinked LTT {t.get('name')}: {unlink_err}",
							)
			except Exception as e:
				frappe.log_error(
					title="Return Equipment Sync",
					message=f"Fallback same-day same-route LTT update failed: {e}",
				)

	# Build execution_doc.equipment for create_equipment_transfer_tickets
	if days:
		execution_doc.equipment = []
		for asset, rtype in seen_assets.items():
			execution_doc.append("equipment", {"asset": asset, "return_type": rtype})
	try:
		from f2c.farm_execution.equipment_transfer_on_completion import create_equipment_transfer_tickets_for_execution
		# Returnable / End Activity Returnable: create return ticket (field -> next field or cluster)
		create_equipment_transfer_tickets_for_execution(execution_doc)
		# Daily Returnable: create return ticket (field -> cluster) so equipment ticket shows on Approve
		create_equipment_transfer_tickets_for_execution(execution_doc, on_end_of_day=True)
	except Exception as e:
		frappe.log_error(
			title="Return Equipment Sync",
			message=f"create_equipment_transfer_tickets_for_execution failed after return type update: {e}",
		)
	return execution_name


@frappe.whitelist()
def create_stock_return_ticket_for_execution(execution_name: str, stock_items: List[Dict[str, Any]] | str = None) -> str:
	"""
	Create a stock-only Logistics Transfer Ticket (field -> cluster) for the execution
	and link it to the execution. Allowed when execution status is In Review.
	stock_items: list of {"item_code": str, "qty": float}, or JSON string. At least one item with qty > 0 required.
	"""
	import json
	execution_name = (execution_name or "").strip()
	if not execution_name:
		frappe.throw("execution_name is required")
	if isinstance(stock_items, str):
		stock_items = json.loads(stock_items) if stock_items else []
	if not isinstance(stock_items, list):
		stock_items = []
	payload = []
	for row in stock_items:
		item_code = (row.get("item_code") or "").strip() if isinstance(row, dict) else ""
		qty = flt(row.get("qty"), 3) if isinstance(row, dict) else 0
		if item_code and qty > 0:
			payload.append({"item_code": item_code, "qty": qty})
	if not payload:
		frappe.throw("At least one stock item with qty > 0 is required")
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	# Optional: only allow items that are execution inputs with return_type Returnable or End Activity Returnable
	allowed_item_codes = set()
	for inp in doc.get("inputs") or []:
		item = getattr(inp, "item", None) or (inp.get("item") if isinstance(inp, dict) else None)
		rtype = getattr(inp, "return_type", None) or (inp.get("return_type") if isinstance(inp, dict) else None) or ""
		if item and (rtype or "").strip() in ("Returnable", "End Activity Returnable"):
			allowed_item_codes.add(str(item).strip())
	for d in frappe.get_all("Farm Task Execution Day", filters={"execution": execution_name}, fields=["name"]):
		day_doc = frappe.get_doc("Farm Task Execution Day", d["name"])
		for inp in day_doc.get("inputs") or []:
			item = getattr(inp, "item", None) or (inp.get("item") if isinstance(inp, dict) else None)
			rtype = getattr(inp, "return_type", None) or (inp.get("return_type") if isinstance(inp, dict) else None) or ""
			if item and (rtype or "").strip() in ("Returnable", "End Activity Returnable"):
				allowed_item_codes.add(str(item).strip())
	if allowed_item_codes:
		payload = [p for p in payload if p.get("item_code") in allowed_item_codes]
		if not payload:
			frappe.throw(
				"After filtering by input return type (Returnable / End Activity Returnable), no stock items remain. "
				"Add at least one input with return type Returnable or End Activity Returnable."
			)
	if doc.status != "In Review":
		frappe.throw(
			f"Cannot create stock return ticket. Execution status is {doc.status}. Only 'In Review' executions are allowed."
		)
	field = getattr(doc, "field", None) or ""
	if not field:
		frappe.throw("Execution has no field; cannot resolve warehouses.")
	from f2c.farm_execution.equipment_transfer_on_completion import (
		get_target_warehouse_for_field,
		get_cluster_warehouse_for_field,
	)
	from_warehouse = get_target_warehouse_for_field(field)
	to_warehouse = get_cluster_warehouse_for_field(field)
	if not from_warehouse or not to_warehouse:
		frappe.throw("Could not resolve field or cluster warehouse for this execution.")
	if from_warehouse == to_warehouse:
		frappe.throw("Field and cluster warehouse are the same; cannot create return ticket.")
	from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket
	result = create_logistics_transfer_ticket(
		from_warehouse=from_warehouse,
		to_warehouse=to_warehouse,
		stock_items=payload,
		assets=[],
	)
	ticket_name = result.get("ticket") if result else None
	if ticket_name:
		try:
			ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
			ticket_doc.farm_task_execution = execution_name
			ticket_doc.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception as link_err:
			frappe.log_error(
				title="Stock Return Ticket Link",
				message=f"Failed to link LTT {ticket_name} to execution {execution_name}: {link_err}",
			)
	return ticket_name or ""


NON_RETURNABLE = "Non Returnable"


@frappe.whitelist()
def create_input_return_ticket_for_execution(execution_name: str) -> str:
	"""
	Create a stock-only Logistics Transfer Ticket (field -> cluster) for inputs that have
	return_type other than "Non Returnable". Qty to return = issued_qty - consumed_qty per row.
	Only items with return_type in ("Returnable", "Daily Returnable", "End Activity Returnable") are included.
	Allowed when execution status is In Review. Returns ticket name or empty string if no items to return.
	"""
	execution_name = (execution_name or "").strip()
	if not execution_name:
		frappe.throw("execution_name is required")
	doc = frappe.get_doc("Farm Task Execution", execution_name)
	if doc.status != "In Review":
		frappe.throw(
			f"Cannot create input return ticket. Execution status is {doc.status}. Only 'In Review' executions are allowed."
		)
	# Collect returnable qty by item from execution and day inputs (exclude Non Returnable only)
	item_qty: Dict[str, float] = {}
	for inp in doc.get("inputs") or []:
		item = getattr(inp, "item", None) or (inp.get("item") if isinstance(inp, dict) else None)
		rtype = (getattr(inp, "return_type", None) or (inp.get("return_type") if isinstance(inp, dict) else None) or "").strip()
		if not item or rtype == NON_RETURNABLE:
			continue
		issued = flt(getattr(inp, "issued_qty", None) or (inp.get("issued_qty") if isinstance(inp, dict) else 0), 3)
		consumed = flt(getattr(inp, "consumed_qty", None) or (inp.get("consumed_qty") if isinstance(inp, dict) else 0), 3)
		qty = max(0, issued - consumed)
		if qty > 0:
			item = str(item).strip()
			item_qty[item] = item_qty.get(item, 0) + qty
	for d in frappe.get_all("Farm Task Execution Day", filters={"execution": execution_name}, fields=["name"]):
		day_doc = frappe.get_doc("Farm Task Execution Day", d["name"])
		for inp in day_doc.get("inputs") or []:
			item = getattr(inp, "item", None) or (inp.get("item") if isinstance(inp, dict) else None)
			rtype = (getattr(inp, "return_type", None) or (inp.get("return_type") if isinstance(inp, dict) else None) or "").strip()
			if not item or rtype == NON_RETURNABLE:
				continue
			issued = flt(getattr(inp, "issued_qty", None) or (inp.get("issued_qty") if isinstance(inp, dict) else 0), 3)
			consumed = flt(getattr(inp, "consumed_qty", None) or (inp.get("consumed_qty") if isinstance(inp, dict) else 0), 3)
			qty = max(0, issued - consumed)
			if qty > 0:
				item = str(item).strip()
				item_qty[item] = item_qty.get(item, 0) + qty
	if not item_qty:
		return ""
	payload = [{"item_code": item, "qty": flt(qty, 3)} for item, qty in item_qty.items()]
	field = getattr(doc, "field", None) or ""
	if not field:
		frappe.throw("Execution has no field; cannot resolve warehouses.")
	from f2c.farm_execution.equipment_transfer_on_completion import (
		get_target_warehouse_for_field,
		get_cluster_warehouse_for_field,
	)
	from_warehouse = get_target_warehouse_for_field(field)
	to_warehouse = get_cluster_warehouse_for_field(field)
	if not from_warehouse or not to_warehouse:
		frappe.throw("Could not resolve field or cluster warehouse for this execution.")
	if from_warehouse == to_warehouse:
		frappe.throw("Field and cluster warehouse are the same; cannot create return ticket.")
	from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket
	result = create_logistics_transfer_ticket(
		from_warehouse=from_warehouse,
		to_warehouse=to_warehouse,
		stock_items=payload,
		assets=[],
	)
	ticket_name = result.get("ticket") if result else None
	if ticket_name:
		try:
			ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
			ticket_doc.farm_task_execution = execution_name
			ticket_doc.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception as link_err:
			frappe.log_error(
				title="Input Return Ticket Link",
				message=f"Failed to link LTT {ticket_name} to execution {execution_name}: {link_err}",
			)
	return ticket_name or ""


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


def _create_consumption_stock_entry_for_day(execution_name: str, day_date: str) -> Optional[str]:
	"""
	Create a Material Issue Stock Entry from target_warehouse for one Farm Task Execution Day's consumed qty.
	Idempotent: if day already has consumption_stock_entry, returns without creating.
	Returns Stock Entry name if created, else None.
	"""
	fte = frappe.get_doc("Farm Task Execution", execution_name)
	if not fte.approved_input_mix:
		return None

	day_name = frappe.db.get_value(
		"Farm Task Execution Day",
		{"execution": execution_name, "date": day_date},
		"name",
	)
	if not day_name:
		return None
	day_doc = frappe.get_doc("Farm Task Execution Day", day_name)
	if day_doc.get("consumption_stock_entry"):
		return None

	target_warehouse = fte.target_warehouse
	if not target_warehouse and getattr(fte, "field", None):
		from f2c.farm_execution.equipment_transfer_on_completion import get_target_warehouse_for_field
		target_warehouse = get_target_warehouse_for_field(fte.field)
	if not target_warehouse:
		frappe.log_error(
			message=f"Execution {execution_name} day {day_date}: Target Warehouse not set. Consumption skipped.",
			title="Execution day consumption skipped (no target warehouse)",
		)
		return None

	items = []
	for row in day_doc.get("inputs") or []:
		qty = flt(row.get("consumed_qty"))
		if qty <= 0:
			continue
		items.append({
			"item_code": row.get("item"),
			"qty": qty,
			"s_warehouse": target_warehouse,
			"batch_no": row.get("batch_no"),
			# Allow consumption even when item has no valuation rate (ERPNext validation).
			# This aligns with field operations where accounting can be reconciled later.
			"allow_zero_valuation_rate": 1,
		})
	if not items:
		return None

	company = frappe.db.get_value("Warehouse", target_warehouse, "company")
	if not company:
		frappe.log_error(
			message=f"Execution {execution_name}: Warehouse {target_warehouse} has no Company. Consumption skipped.",
			title="Execution day consumption skipped (no company on warehouse)",
		)
		return None

	se = frappe.get_doc({
		"doctype": "Stock Entry",
		"stock_entry_type": "Material Issue",
		"company": company,
		"from_warehouse": target_warehouse,
		"items": items,
	})
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value(
		"Farm Task Execution Day",
		day_name,
		"consumption_stock_entry",
		se.name,
		update_modified=False,
	)
	frappe.db.commit()

	try:
		ws_docname = _get_ws_docname_for_warehouse(target_warehouse)
		if ws_docname:
			refresh_from_ledger(ws_docname)
		else:
			from f2c.inventory.doctype.warehouse_stock.warehouse_stock import sync_warehouse_stock
			sync_warehouse_stock(refresh_existing=0)
			ws_docname = _get_ws_docname_for_warehouse(target_warehouse)
			if ws_docname:
				refresh_from_ledger(ws_docname)
	except Exception as e:
		frappe.log_error(
			message=f"Failed to refresh warehouse stock for {target_warehouse} after day consumption: {str(e)}",
			title="Warehouse Stock Refresh Error",
		)
	return se.name


def _create_consumption_stock_entry_if_applicable(doc: Document) -> None:
	"""
	When execution is completed and used approved inputs (consumed_qty > 0),
	create a Material Issue Stock Entry from target_warehouse to reduce stock.
	Only runs once per execution (idempotent via consumption_stock_entry).
	If execution has Farm Task Execution Days, consume per day (any day not yet consumed); else use FTE inputs.
	"""
	if not doc.approved_input_mix:
		return
	if doc.get("consumption_stock_entry"):
		return

	# If execution has days, create consumption for each day that does not have one yet (then return)
	days_list = frappe.get_all(
		"Farm Task Execution Day",
		filters={"execution": doc.name},
		fields=["name", "date", "consumption_stock_entry"],
		order_by="date asc",
	)
	if days_list:
		for d in days_list:
			if d.get("consumption_stock_entry"):
				continue
			day_date_str = d.get("date")
			if day_date_str:
				try:
					_create_consumption_stock_entry_for_day(doc.name, day_date_str)
				except Exception as e:
					frappe.log_error(
						message=f"Consumption on completion failed for {doc.name} day {day_date_str}: {str(e)}",
						title="Farm Task Execution Day Consumption",
					)
		return

	# No days: use FTE inputs (existing single-day / no-days flow)
	items = []
	for row in doc.get("inputs") or []:
		qty = flt(row.consumed_qty)
		if qty <= 0:
			continue
		items.append(
			{
				"item_code": row.item,
				"qty": qty,
				"s_warehouse": doc.target_warehouse,
				"batch_no": row.batch_no,
				"allow_zero_valuation_rate": 1,
			}
		)
	if not items:
		return

	# Auto-resolve target warehouse from field if not set (so consumption always runs for field warehouse)
	if not doc.target_warehouse and getattr(doc, "field", None):
		from f2c.farm_execution.equipment_transfer_on_completion import get_target_warehouse_for_field

		resolved = get_target_warehouse_for_field(doc.field)
		if resolved:
			doc.target_warehouse = resolved
			frappe.db.set_value(
				"Farm Task Execution",
				doc.name,
				"target_warehouse",
				resolved,
				update_modified=False,
			)

	if not doc.target_warehouse:
		frappe.log_error(
			message=f"Execution {doc.name}: Target Warehouse not set. Consumption stock entry skipped. Set Target Warehouse and create Material Issue manually if needed.",
			title="Execution consumption skipped (no target warehouse)",
		)
		return
	company = frappe.db.get_value("Warehouse", doc.target_warehouse, "company")
	if not company:
		frappe.log_error(
			message=f"Execution {doc.name}: Warehouse {doc.target_warehouse} has no Company. Consumption stock entry skipped.",
			title="Execution consumption skipped (no company on warehouse)",
		)
		return

	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Issue",
			"company": company,
			"from_warehouse": doc.target_warehouse,
			"items": items,
		}
	)
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value(
		"Farm Task Execution",
		doc.name,
		"consumption_stock_entry",
		se.name,
		update_modified=False,
	)

	try:
		# Refresh warehouse stock snapshot immediately so Warehouse Inventory shows reduced stock
		ws_docname = _get_ws_docname_for_warehouse(doc.target_warehouse)
		if ws_docname:
			refresh_from_ledger(ws_docname)
		else:
			# Create Warehouse Stock doc if it doesn't exist
			from f2c.inventory.doctype.warehouse_stock.warehouse_stock import sync_warehouse_stock

			sync_warehouse_stock(refresh_existing=0)
			ws_docname = _get_ws_docname_for_warehouse(doc.target_warehouse)
			if ws_docname:
				refresh_from_ledger(ws_docname)
	except Exception as e:
		frappe.log_error(
			message=f"Failed to refresh warehouse stock for {doc.target_warehouse} after consumption: {str(e)}",
			title="Warehouse Stock Refresh Error",
		)


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
def get_execution_equipment_list(parent_names) -> List[Dict[str, Any]]:
	"""
	Return list of Farm Task Execution Equipment rows (parent, asset, asset_name) for given execution names.
	Used by the frontend to avoid direct getDocList on the child doctype (which can 403 without permissions).
	"""
	if not parent_names:
		return []
	if isinstance(parent_names, str):
		import json
		try:
			parent_names = json.loads(parent_names)
		except Exception:
			parent_names = [parent_names]
	names = [str(n).strip() for n in parent_names if n and str(n).strip()]
	if not names:
		return []
	rows = frappe.get_all(
		"Farm Task Execution Equipment",
		filters={"parent": ["in", names]},
		fields=["parent", "asset", "asset_name"],
		limit=2000,
	)
	return rows or []


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
		
		# Don't return yet — we may still have punched-in Employees (Attendance Log)
		# that are linked to Farm Worker Details via the `employee` field.
		present_attendance = []
	
	# Step 2: Get Farm Worker Details for each Present attendance record
	available_labour = []

	# Collect candidate Farm Worker Details IDs from two sources:
	# 1) Farm Worker Attendance with status "Present"
	# 2) Attendance Log entries ("punched in") mapped to Farm Worker Details via Farm Worker Details.employee
	farm_worker_names: List[str] = []
	if present_attendance:
		farm_worker_names.extend([att.get("farm_worker") for att in present_attendance if att.get("farm_worker")])

	attendance_log_employee_ids: List[str] = []
	try:
		# Fetch Attendance Log for the same dates we tried for Farm Worker Attendance.
		for date_str_check, _date_obj_check in dates_to_try:
			log_rows = frappe.get_all(
				"Attendance Log",
				filters={"attendance_date": date_str_check},
				fields=["employee", "check_in"],
				limit=1000,
			)
			for r in (log_rows or []):
				emp = r.get("employee")
				# "Punched in": keep rows that have a check_in timestamp.
				if emp and r.get("check_in"):
					attendance_log_employee_ids.append(emp)
	except Exception as e:
		frappe.log_error(f"Error fetching Attendance Log: {str(e)}", "Get Available Labour Attendance Log Error")

	# Map Attendance Log employees to Farm Worker Details via the employee link.
	if attendance_log_employee_ids:
		# De-dupe employee IDs
		emp_ids = list(dict.fromkeys([e for e in attendance_log_employee_ids if e]))
		try:
			fw_from_employees = frappe.get_all(
				"Farm Worker Details",
				filters={"employee": ["in", emp_ids]},
				fields=["name", "employee"],
				limit=2000,
			)
			if fw_from_employees:
				farm_worker_names.extend([r.get("name") for r in fw_from_employees if r.get("name")])
		except Exception as e:
			frappe.log_error(f"Error mapping Attendance Log employees to Farm Worker Details: {str(e)}", "Get Available Labour Attendance Log Map Error")

	# De-dupe farm worker names while preserving order
	farm_worker_names = list(dict.fromkeys([n for n in farm_worker_names if n]))
	print(f"[get_available_labour] Processing {len(farm_worker_names)} total workers (Present + punched-in employees): {farm_worker_names}")

	if not farm_worker_names:
		print(f"[get_available_labour] No Farm Worker Details found from Present attendance or Attendance Log punched-in employees")
		return []

	# Fetch Farm Worker Details with all needed fields (including employee + worker_roles for role column)
	farm_workers_data = frappe.get_all(
		"Farm Worker Details",
		filters={"name": ["in", farm_worker_names]},
		fields=["name", "worker_name", "aadhaar_number", "dob", "address", "gender", "employee", "worker_roles"],
		order_by="worker_name",
		limit=2000,
	)

	print(f"[get_available_labour] Found {len(farm_workers_data)} Farm Worker Details records for {len(farm_worker_names)} candidate workers")

	if len(farm_workers_data) != len(farm_worker_names):
		missing = set(farm_worker_names) - {fw.get("name") for fw in farm_workers_data}
		if missing:
			print(f"[get_available_labour] WARNING: Missing Farm Worker Details for: {missing}")

	# Return Farm Worker Details directly (no Labour Details needed)
	for farm_worker in (farm_workers_data or []):
		available_labour.append({
			"name": farm_worker.get("name"),
			"worker_name": farm_worker.get("worker_name"),
			"labour_name": farm_worker.get("worker_name"),  # Keep for backward compatibility with frontend
			"aadhaar_number": farm_worker.get("aadhaar_number"),
			"dob": farm_worker.get("dob"),
			"address": farm_worker.get("address"),
			"gender": farm_worker.get("gender"),
			"employee": farm_worker.get("employee"),
			"worker_roles": farm_worker.get("worker_roles"),
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


@frappe.whitelist()
def sync_on_demand_activity_statuses() -> Dict[str, Any]:
	"""
	Utility function to sync On Demand Activity statuses for completed executions.
	This can be used to fix existing records that may have been missed.
	
	Returns:
		Dictionary with count of updated activities
	"""
	updated_count = 0
	errors = []
	
	# Get all completed executions with on_demand_activity_ref
	completed_executions = frappe.get_all(
		"Farm Task Execution",
		filters={
			"status": "Completed",
			"on_demand_activity_ref": ["!=", ""]
		},
		fields=["name", "on_demand_activity_ref"]
	)
	
	for exec_doc in completed_executions:
		try:
			activity_name = exec_doc.on_demand_activity_ref
			if not activity_name:
				continue
			
			# Check current activity status
			activity_status = frappe.db.get_value("On Demand Activity", activity_name, "status")
			
			# Only update if activity is not already in a terminal state
			if activity_status and activity_status not in ("Aborted", "Completed", "Archived"):
				frappe.db.set_value("On Demand Activity", activity_name, "status", "Completed", update_modified=False)
				updated_count += 1
		except Exception as e:
			errors.append(f"Error updating activity {exec_doc.on_demand_activity_ref}: {str(e)}")
			frappe.log_error(f"Error syncing On Demand Activity status for execution {exec_doc.name}: {str(e)}", "Sync On Demand Activity Status Error")
	
	# Commit all changes
	if updated_count > 0:
		frappe.db.commit()
	
	return {
		"updated_count": updated_count,
		"total_checked": len(completed_executions),
		"errors": errors
	}


@frappe.whitelist()
def get_labour_checkin_times(checkin_names: str) -> dict:
	"""
	Return a map of Employee Checkin name -> time (datetime string) for display.
	Used by the frontend to show check-in/check-out time values in the Labour table.
	"""
	names = json.loads(checkin_names) if isinstance(checkin_names, str) else (checkin_names or [])
	names = [n for n in names if n and isinstance(n, str)]
	if not names:
		return {}
	try:
		rows = frappe.db.get_all(
			"Employee Checkin",
			filters={"name": ["in", names]},
			fields=["name", "time"]
		)
		return {r["name"]: (r.get("time") and str(r["time"])) or "" for r in (rows or [])}
	except Exception:
		return {}



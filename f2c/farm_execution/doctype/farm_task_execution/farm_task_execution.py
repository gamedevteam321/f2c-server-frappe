# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

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
		self._compute_consumed_qty()

	def _sync_and_validate_progress_images(self):
		"""
		Progress images are uploaded as a child table.
		- Keep progress_image_count in sync
		- Enforce max 5 images
		"""
		rows = self.get("progress_images") or []
		if len(rows) > 5:
			frappe.throw("Maximum 5 progress images are allowed.")
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

			# Create equipment transfer tickets when execution completes: same-cluster next field or return to cluster
			if self.status == "Completed":
				try:
					from f2c.farm_execution.equipment_transfer_on_completion import create_equipment_transfer_tickets_for_execution
					create_equipment_transfer_tickets_for_execution(self)
				except Exception as e:
					frappe.log_error(
						f"Equipment transfer tickets on completion failed for {self.name}: {str(e)}",
						"Farm Task Execution Equipment Transfer",
					)

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

		# Get old status for transition validation
		old_status = None
		if self.has_value_changed("status"):
			if hasattr(self, "_doc_before_save") and self._doc_before_save:
				old_status = self._doc_before_save.status
			elif not self.is_new():
				# Fallback: fetch from database if _doc_before_save is not available
				old_status = frappe.db.get_value(self.doctype, self.name, "status")

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

			# Require at least 3 progress images before submitting for review
			if new_status == "In Review":
				# Allow explicit skip from API when user chooses "Submit for review without images"
				if not getattr(frappe.flags, "skip_progress_image_min", False):
					rows = self.get("progress_images") or []
					if len(rows) < 3:
						frappe.throw("Please upload at least 3 progress images before submitting for review.")
			
			# Only allow specific transitions
			valid_transitions = {
				"Ready": ["In Progress", "Reported", "Aborted"],
				"In Progress": ["In Review", "Reported", "Aborted"],
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
		exec_doc.status = "Ready"
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

		# Copy equipment (planned) from machinery, implements, hand_tools, other_tools
		for eq in schedule.get("machinery") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
				},
			)
		for eq in schedule.get("implements") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
				},
			)
		for eq in schedule.get("hand_tools") or []:
			exec_doc.append(
				"equipment",
				{
					"asset": eq.asset,
					"asset_name": eq.asset_name,
					"planned_hours": eq.planned_hours,
				},
			)
		for eq in schedule.get("other_tools") or []:
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
	exec_doc.status = "Ready"
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


@frappe.whitelist()
def start_execution(
	execution_name: str,
	equipment_checklist: Optional[str] = None,
	input_checklist: Optional[str] = None,
	equipment_photo: Optional[str] = None,
	equipment_photo_urls=None,
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
				equipment.append({"asset": asset, "asset_name": getattr(row, "asset_name") or asset, "available": False})
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
		equipment.append({
			"asset": asset,
			"asset_name": getattr(row, "asset_name") or asset,
			"available": available,
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
def update_execution_data(
	execution_name: str,
	inputs: List[Dict[str, Any]] | str | None = None,
	equipment: List[Dict[str, Any]] | str | None = None,
	labour_attendance: List[Dict[str, Any]] | str | None = None,
	planned_male_count: int | None = None,
	planned_female_count: int | None = None,
) -> str:
	"""
	Update execution child tables (inputs, equipment, labour_attendance) and optional planned counts.
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
	if doc.status != "In Progress":
		frappe.throw(f"Cannot update execution data. Current status is {doc.status}. Only 'In Progress' executions can be updated.")

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
			if "remarks" in row:
				child.remarks = str(row["remarks"]) if row["remarks"] is not None else ""

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
				frappe.throw(f"Cannot submit for review. Current status is {doc.status}. Only 'In Progress' executions can be moved to 'In Review'.")
			
			# Optional bypass for progress-image requirement (requested UX)
			frappe.flags.skip_progress_image_min = bool(int(skip_images or 0))
			
			doc.status = "In Review"
			# Set actual_end when submitting for review
			if not doc.actual_end:
				doc.actual_end = now_datetime()
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
			# Recompute consumed_qty (issued - returned) for all approved inputs so consumption stock entry uses correct values
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


def _create_consumption_stock_entry_if_applicable(doc: Document) -> None:
	"""
	When execution is completed and used approved inputs (consumed_qty > 0),
	create a Material Issue Stock Entry from target_warehouse to reduce stock.
	Only runs once per execution (idempotent via consumption_stock_entry).
	"""
	if not doc.approved_input_mix:
		return
	if doc.get("consumption_stock_entry"):
		return

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



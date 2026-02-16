# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import frappe
from frappe.model.document import Document


class FarmReportTicket(Document):
	def validate(self):
		self._validate_report_type()
		self._validate_report_module()
		self._validate_required_fields()
		self._autofill_fields()

	def _validate_report_module(self):
		"""Validate that report_module is set and is a valid option when provided."""
		valid_modules = ["Execution", "Scheduling", "On Demand", "Labour"]
		report_module = getattr(self, "report_module", None) or ""
		if not report_module or not str(report_module).strip():
			frappe.throw("Report Module is required.")
		if report_module not in valid_modules:
			frappe.throw(f"Report Module must be one of: {', '.join(valid_modules)}")

	def _validate_report_type(self):
		"""Validate that report_type is set and is a valid option."""
		if not self.report_type:
			frappe.throw("Report Type is required.")
		
		valid_types = ["Delay", "Inventory Failure", "Farm Worker", "Stock Issue"]
		if self.report_type not in valid_types:
			frappe.throw(f"Report Type must be one of: {', '.join(valid_types)}")

	def _validate_required_fields(self):
		"""Validate required fields based on report_type."""
		if not self.report_reason or not self.report_reason.strip():
			frappe.throw("Report Reason is required.")
		
		if self.report_type == "Inventory Failure":
			if not self.get("equipment") or len(self.equipment) == 0:
				frappe.throw("At least one Equipment is required for Inventory Failure reports.")
		
		elif self.report_type == "Farm Worker":
			if not self.get("list_of_labours") or len(self.list_of_labours) == 0:
				frappe.throw("At least one Farm Worker is required for Farm Worker reports.")

	def _autofill_fields(self):
		"""Auto-fill read-only fields from linked documents."""
		if self.block:
			block_doc = frappe.get_doc("Geo Fencing Area", self.block)
			self.block_name = block_doc.area_name
		
		if self.stage:
			stage_doc = frappe.get_doc("Crop Stage", self.stage)
			self.stage_name = stage_doc.stage
		
		if self.activity:
			activity_doc = frappe.get_doc("Farm Activity", self.activity)
			self.activity_name = activity_doc.activity_name
		
		# Auto-fill asset_name and asset_type in equipment child table
		if self.get("equipment"):
			for equipment_row in self.equipment:
				if equipment_row.asset:
					try:
						asset_doc = frappe.get_doc("Asset", equipment_row.asset)
						if not equipment_row.asset_name:
							equipment_row.asset_name = asset_doc.asset_name
						if not equipment_row.asset_type:
							equipment_row.asset_type = asset_doc.asset_category or ""
					except frappe.DoesNotExistError:
						pass
		
		# Auto-fill worker_name in farm worker child table
		if self.get("list_of_labours"):
			for labour_row in self.list_of_labours:
				if labour_row.farm_worker and not labour_row.worker_name:
					try:
						worker_doc = frappe.get_doc("Farm Worker Details", labour_row.farm_worker)
						labour_row.worker_name = worker_doc.worker_name
					except frappe.DoesNotExistError:
						pass

		# Auto-fill item_name in stock details child table
		if self.get("stock_details"):
			for stock_row in self.stock_details:
				if stock_row.item_code and not stock_row.item_name:
					try:
						item_doc = frappe.get_doc("Item", stock_row.item_code)
						stock_row.item_name = item_doc.item_name or stock_row.item_code
					except frappe.DoesNotExistError:
						pass


def _parse_json_list(val: Any) -> List[Dict]:
	"""
	Accepts a Python list[dict] or a JSON string representing that list.
	Returns a list[dict] (empty list if val is falsy).
	"""
	if not val:
		return []
	if isinstance(val, list):
		return val
	if isinstance(val, str):
		try:
			parsed = json.loads(val)
			return parsed if isinstance(parsed, list) else []
		except Exception:
			return []
	return []


def _resolve_stage_from_schedule(schedule_name: str) -> Optional[str]:
	schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
	if not schedule.crop_plan_activity:
		return None
	return frappe.db.get_value("Crop Plan Activity", schedule.crop_plan_activity, "crop_stage")


def _derive_context_from_execution(execution_name: str) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
	exec_doc = frappe.get_doc("Farm Task Execution", execution_name)

	schedule_ref = getattr(exec_doc, "schedule_ref", None) or None
	on_demand_activity_ref = getattr(exec_doc, "on_demand_activity_ref", None) or None

	block = None
	activity = getattr(exec_doc, "farm_activity", None) or None

	if schedule_ref:
		schedule = frappe.get_doc("Crop Plan Schedule", schedule_ref)
		block = schedule.block
	elif getattr(exec_doc, "blocks", None):
		block = exec_doc.blocks[0].block if exec_doc.blocks and exec_doc.blocks[0].block else None

	context = {
		"block": block,
		"activity": activity,
	}
	refs = {
		"execution_ref": exec_doc.name,
		"schedule_ref": schedule_ref,
		"on_demand_activity_ref": on_demand_activity_ref,
	}
	return context, refs


def _derive_context_from_schedule(schedule_name: str) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
	schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
	execution_ref = getattr(schedule, "execution_ref", None) or None
	context = {
		"block": schedule.block,
		"activity": schedule.farm_activity,
	}
	refs = {
		"execution_ref": execution_ref,
		"schedule_ref": schedule.name,
		"on_demand_activity_ref": None,
	}
	return context, refs


def _derive_context_from_on_demand(activity_name: str) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
	activity_doc = frappe.get_doc("On Demand Activity", activity_name)
	execution_ref = getattr(activity_doc, "execution_ref", None) or None

	block = None
	if getattr(activity_doc, "blocks", None):
		block = activity_doc.blocks[0].block if activity_doc.blocks and activity_doc.blocks[0].block else None

	context = {
		"block": block,
		"activity": getattr(activity_doc, "activity", None) or None,
	}
	refs = {
		"execution_ref": execution_ref,
		"schedule_ref": None,
		"on_demand_activity_ref": activity_doc.name,
	}
	return context, refs


def _default_report_status(report_type: str) -> str:
	rt = (report_type or "").strip()
	if rt == "Delay":
		return "Delayed"
	if rt == "Inventory Failure":
		return "Unresolved"
	if rt == "Farm Worker":
		return "In Progress"
	return "Reported"


@frappe.whitelist()
def create_report_and_mark_reported(
	report_type: str,
	report_reason: str,
	execution_ref: str | None = None,
	schedule_ref: str | None = None,
	on_demand_activity_ref: str | None = None,
	stage: str | None = None,
	equipment: Any = None,
	list_of_labours: Any = None,
	stock_details: Any = None,
	image_upload: str | None = None,
	reorder_inventory: bool | None = None,
) -> Dict[str, Any]:
	"""
	Create a Farm Report Ticket only. Does not change the status of any linked
	execution, schedule, or on-demand activity. The Reported tab on Execution,
	Schedule, and On Demand pages shows records that have an open (non-Resolved)
	report ticket for that ref.
	"""
	refs_provided = [bool(execution_ref), bool(schedule_ref), bool(on_demand_activity_ref)]
	if sum(refs_provided) != 1:
		frappe.throw("Provide exactly one of execution_ref, schedule_ref, or on_demand_activity_ref.")

	if not (report_reason or "").strip():
		frappe.throw("Report Reason is required.")

	context: Dict[str, Any] = {}
	refs: Dict[str, Optional[str]] = {"execution_ref": None, "schedule_ref": None, "on_demand_activity_ref": None}

	if execution_ref:
		context, refs = _derive_context_from_execution(execution_ref)
	elif schedule_ref:
		context, refs = _derive_context_from_schedule(schedule_ref)
	else:
		context, refs = _derive_context_from_on_demand(on_demand_activity_ref)

	# Resolve stage:
	# - If schedule_ref exists, derive from Crop Plan Activity
	# - Otherwise (on-demand without schedule), require stage input
	resolved_stage = None
	if refs.get("schedule_ref"):
		resolved_stage = _resolve_stage_from_schedule(refs["schedule_ref"])
	else:
		resolved_stage = stage

	if not context.get("block"):
		frappe.throw("Block is required to create a Farm Report Ticket. Please ensure the task has a Block selected.")
	if not context.get("activity"):
		frappe.throw("Activity is required to create a Farm Report Ticket.")
	if not resolved_stage:
		frappe.throw("Stage is required to create a Farm Report Ticket.")

	report_doc = frappe.get_doc({"doctype": "Farm Report Ticket"})
	report_doc.report_type = report_type
	report_doc.status = _default_report_status(report_type)
	report_doc.report_reason = report_reason

	report_doc.block = context["block"]
	report_doc.stage = resolved_stage
	report_doc.activity = context["activity"]

	# Link refs (read-only in UI, but settable via backend)
	report_doc.execution_ref = refs.get("execution_ref")
	report_doc.schedule_ref = refs.get("schedule_ref")
	report_doc.on_demand_activity_ref = refs.get("on_demand_activity_ref")

	# Set report_module from which ref was provided
	if refs.get("execution_ref"):
		report_doc.report_module = "Execution"
	elif refs.get("schedule_ref"):
		report_doc.report_module = "Scheduling"
	else:
		report_doc.report_module = "On Demand"

	if image_upload:
		report_doc.image_upload = image_upload

	if report_type == "Stock Issue":
		report_doc.reorder_inventory = 1 if (reorder_inventory in (True, 1) or str(reorder_inventory or "").strip().lower() in ("1", "true", "yes")) else 0

	# Conditional child tables
	for row in _parse_json_list(equipment):
		asset = (row.get("asset") or "").strip()
		if not asset:
			continue
		report_doc.append("equipment", {"asset": asset, "remarks": row.get("remarks")})

	for row in _parse_json_list(list_of_labours):
		farm_worker = (row.get("farm_worker") or row.get("labour") or row.get("name") or "").strip()
		if not farm_worker:
			continue
		report_doc.append("list_of_labours", {"farm_worker": farm_worker})

	for row in _parse_json_list(stock_details):
		item_code = (row.get("item_code") or row.get("item") or "").strip()
		if not item_code:
			continue
		report_doc.append("stock_details", {"item_code": item_code, "remarks": row.get("remarks")})

	report_doc.insert(ignore_permissions=True)

	frappe.db.commit()
	return {"farm_report_name": report_doc.name}


@frappe.whitelist()
def get_latest_report_for_ref(
	execution_ref: str | None = None,
	schedule_ref: str | None = None,
	on_demand_activity_ref: str | None = None,
) -> Dict[str, Any]:
	refs_provided = [bool(execution_ref), bool(schedule_ref), bool(on_demand_activity_ref)]
	if sum(refs_provided) != 1:
		frappe.throw("Provide exactly one of execution_ref, schedule_ref, or on_demand_activity_ref.")

	# Build primary filters and fallback filters.
	# This is important because a report may be created against a Schedule before an Execution exists.
	primary_filters: Dict[str, Any] = {}
	fallback_filters: List[Dict[str, Any]] = []
	if execution_ref:
		primary_filters["execution_ref"] = execution_ref
		try:
			exec_doc = frappe.get_doc("Farm Task Execution", execution_ref)
			if getattr(exec_doc, "schedule_ref", None):
				fallback_filters.append({"schedule_ref": exec_doc.schedule_ref})
			if getattr(exec_doc, "on_demand_activity_ref", None):
				fallback_filters.append({"on_demand_activity_ref": exec_doc.on_demand_activity_ref})
		except Exception:
			pass
	elif schedule_ref:
		primary_filters["schedule_ref"] = schedule_ref
		try:
			sch = frappe.get_doc("Crop Plan Schedule", schedule_ref)
			if getattr(sch, "execution_ref", None):
				fallback_filters.append({"execution_ref": sch.execution_ref})
		except Exception:
			pass
	else:
		primary_filters["on_demand_activity_ref"] = on_demand_activity_ref
		try:
			oda = frappe.get_doc("On Demand Activity", on_demand_activity_ref)
			if getattr(oda, "execution_ref", None):
				fallback_filters.append({"execution_ref": oda.execution_ref})
		except Exception:
			pass

	def _query(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
		return frappe.get_all(
			"Farm Report Ticket",
			filters=filters,
			fields=["name", "modified"],
			order_by="modified desc",
			limit=1,
		) or []

	rows = _query(primary_filters)
	if not rows:
		for f in fallback_filters:
			rows = _query(f)
			if rows:
				break

	return {"farm_report_name": rows[0]["name"] if rows else None}

# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import compute_total_qty


class FarmTaskExecution(Document):
	def validate(self):
		self._validate_status_rules()
		self._compute_consumed_qty()

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
	"""
	schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)

	if schedule.execution_ref:
		return schedule.execution_ref

	exec_doc = frappe.get_doc({"doctype": "Farm Task Execution"})
	exec_doc.schedule_ref = schedule.name

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

	exec_doc.insert(ignore_permissions=True)

	schedule.db_set("execution_ref", exec_doc.name, update_modified=False)
	return exec_doc.name


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



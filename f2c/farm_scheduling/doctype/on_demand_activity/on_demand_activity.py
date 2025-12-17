# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.model.document import Document
from frappe.utils import flt

SQ_METERS_TO_ACRES = 0.000247105


class OnDemandActivity(Document):
	def validate(self):
		self._validate_reason()
		self._validate_dates()
		self._validate_blocks_belong_to_field()
		self._validate_activity_belongs_to_group()
		self._autofill_activity_fields()
		self._validate_approved_input_mix_required()
		self._compute_totals()
		self._compute_labour_total()
		self._compute_water()
		self._validate_spray_requirements()
		self._recompute_input_totals_if_needed()

	def _validate_reason(self):
		if not (self.reason or "").strip():
			frappe.throw("Reason is required for On Demand Activity.")

	def _validate_dates(self):
		if self.planned_start and self.planned_end:
			if self.planned_end <= self.planned_start:
				frappe.throw("Planned End must be after Planned Start.")

	def _validate_blocks_belong_to_field(self):
		if not self.field or not self.get("blocks"):
			return
		
		field_doc = frappe.get_doc("Geo Fencing Area", self.field)
		if field_doc.geo_fencing_type != "Field":
			frappe.throw("Selected field must be of type 'Field'.")
		
		# Get all blocks that belong to this field
		field_blocks = frappe.get_all(
			"Geo Fencing Area",
			filters={
				"parent_area": self.field,
				"geo_fencing_type": "Block"
			},
			fields=["name"]
		)
		allowed_block_names = {b.name for b in field_blocks}
		
		for block_row in self.blocks:
			if block_row.block and block_row.block not in allowed_block_names:
				frappe.throw(f"Block '{block_row.block_name or block_row.block}' does not belong to the selected field '{self.field_name or self.field}'.")

	def _validate_activity_belongs_to_group(self):
		if not self.activity or not self.activity_group_type:
			return
		
		activity_doc = frappe.get_doc("Farm Activity", self.activity)
		if activity_doc.activity_group_type != self.activity_group_type:
			frappe.throw(f"Selected Activity '{self.activity_name or self.activity}' does not belong to the selected Activity Group Type '{self.activity_group_type}'.")

	def _autofill_activity_fields(self):
		if not self.activity:
			return
		
		activity_doc = frappe.get_doc("Farm Activity", self.activity)
		self.activity_name = activity_doc.activity_name
		
		# Determine if spray based on activity group type and activity name
		agt = (activity_doc.activity_group_type or "").lower()
		lbl = (self.activity_name or "").lower()
		self.is_spray = 1 if ("plant protection" in agt or "spray" in lbl) else 0

	def _validate_approved_input_mix_required(self):
		"""Check if activity has farm_tasks (inputs), and if so, require approved_input_mix."""
		if not self.activity:
			return
		
		activity_doc = frappe.get_doc("Farm Activity", self.activity)
		has_farm_tasks = bool(activity_doc.get("farm_tasks") and len(activity_doc.farm_tasks) > 0)
		
		if has_farm_tasks and not self.approved_input_mix:
			frappe.throw("Approved Input Mix is required because the selected activity has inputs (farm tasks).")

	def _compute_totals(self):
		# Sum all blocks
		total_acres = 0.0
		total_seedlings = 0
		
		for block_row in self.get("blocks") or []:
			total_acres += flt(block_row.block_area_acres or 0, 3)
			total_seedlings += int(block_row.no_of_seedlings or 0)
		
		self.total_acres = flt(total_acres, 3)
		self.total_seedlings = total_seedlings

	def _compute_labour_total(self):
		self.total_labour_count = int(self.male_count or 0) + int(self.female_count or 0)

	def _compute_water(self):
		water_to_use = 0.0
		if self.water_requirement_basis == "Per Acre":
			water_to_use = flt(self.total_acres) * flt(self.water_rate)
		elif self.water_requirement_basis == "Per Plant":
			water_to_use = flt(self.total_seedlings) * flt(self.water_rate)

		self.water_to_be_used_liters = flt(water_to_use, 3)

	def _validate_spray_requirements(self):
		# Spray requires water planning; irrigation estimate remains optional
		if not self.is_spray:
			return

		if not self.water_requirement_basis:
			frappe.throw("Water Requirement is mandatory for Spray activities.")
		if not flt(self.water_rate):
			frappe.throw("Water Quantity is mandatory for Spray activities.")
		if flt(self.water_to_be_used_liters) <= 0:
			frappe.throw("Water to be Used (Liters) must be greater than 0 for Spray activities.")

	def _recompute_input_totals_if_needed(self):
		# For spray activities, compute total quantity for items based on water_to_be_used_liters
		if not self.get("inputs"):
			return

		if not self.is_spray:
			for row in self.inputs:
				row.total_quantity_to_use = flt(0, 3)
				row.quantity_to_use_display = ""
			return

		water_liters = flt(self.water_to_be_used_liters)
		for row in self.inputs:
			unit_raw = (row.unit or "").strip()
			base_unit = unit_raw
			unit_l = unit_raw.lower()
			if unit_l == "ml/l":
				base_unit = "ml"
			elif unit_l == "g/l":
				base_unit = "g"
			elif unit_l == "bags/acre":
				base_unit = "Bags"

			total = compute_total_qty(
				water_liters=water_liters,
				total_acres=flt(self.total_acres),
				rate=flt(row.rate_quantity),
				unit=unit_raw,
			)
			row.total_quantity_to_use = total
			if total and unit_raw:
				row.quantity_to_use_display = f"{flt(total, 3)} {base_unit} / {flt(water_liters, 3)} L"
			else:
				row.quantity_to_use_display = ""


def compute_total_qty(*, water_liters: float, total_acres: float, rate: float, unit: str) -> float:
	"""
	Compute total qty based on unit conventions used in Farm Tasks:
	- ml/L, g/L: rate per liter of water
	- Bags/Acre: rate per acre
	- Per Manufacturer / others: do not auto-scale (keep as 0 to force manual or just copy rate)
	"""
	unit_l = unit.lower()
	if unit_l in ("ml/l", "g/l"):
		return flt(rate * water_liters, 3)
	if unit_l == "bags/acre":
		return flt(rate * total_acres, 3)
	# For other units (kg, g, ml, L, etc.) we do not auto-scale in scheduling (ambiguous)
	return flt(0, 3)


@frappe.whitelist()
def get_block_details(block: str) -> Dict[str, Any]:
	"""
	Return block_name, block_area_acres and no_of_seedlings for a given block.
	"""
	if not block:
		return {}
	
	block_doc = frappe.get_doc("Geo Fencing Area", block)
	area_acres = 0.0
	if block_doc.area:
		area_acres = flt(block_doc.area) * SQ_METERS_TO_ACRES
	
	# Get no_of_seedlings from block if available (might be stored in custom field or related table)
	# For now, default to 0 if not available
	no_of_seedlings = getattr(block_doc, "no_of_seedlings", 0) or 0
	
	return {
		"block": block,
		"block_name": block_doc.area_name or block,
		"block_area_acres": flt(area_acres, 3),
		"no_of_seedlings": int(no_of_seedlings),
	}


@frappe.whitelist()
def get_activity_details(activity: str) -> Dict[str, Any]:
	"""
	Return activity details including whether it has farm_tasks (inputs).
	"""
	if not activity:
		return {}
	
	activity_doc = frappe.get_doc("Farm Activity", activity)
	has_farm_tasks = bool(activity_doc.get("farm_tasks") and len(activity_doc.farm_tasks) > 0)
	
	agt = (activity_doc.activity_group_type or "").lower()
	lbl = (activity_doc.activity_name or "").lower()
	is_spray = 1 if ("plant protection" in agt or "spray" in lbl) else 0
	
	return {
		"activity_name": activity_doc.activity_name or "",
		"activity_group_type": activity_doc.activity_group_type or "",
		"is_spray": is_spray,
		"has_farm_tasks": has_farm_tasks,
	}


@frappe.whitelist()
def get_farm_task_items(farm_task: str) -> List[Dict[str, Any]]:
	"""
	Return approved input mix items from Farm Tasks.
	"""
	doc = frappe.get_doc("Farm Tasks", farm_task)
	items: List[Dict[str, Any]] = []
	for row in doc.items or []:
		items.append(
			{
				"item": row.item,
				"item_name": row.item_name,
				"rate_quantity": flt(row.quantity, 3),
				"unit": row.unit,
			}
		)
	return items


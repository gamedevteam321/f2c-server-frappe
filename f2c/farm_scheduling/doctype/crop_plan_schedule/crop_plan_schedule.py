# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

import frappe
from frappe.model.document import Document
from frappe.utils import flt

SQ_METERS_TO_ACRES = 0.000247105


class CropPlanSchedule(Document):
	def validate(self):
		self._validate_status_cancel_reason()
		self._validate_rescheduled_readonly()
		self._validate_block_belongs_to_crop_plan()
		self._validate_activity_matches_block()
		self._autofill_activity_fields()
		self._autofill_approved_input_mix_from_plan()
		self._compute_totals()
		self._compute_labour_total()
		self._compute_water()
		self._validate_spray_requirements()
		self._recompute_input_totals_if_needed()

	def _validate_status_cancel_reason(self):
		if self.status == "Aborted" and not (self.cancel_reason or "").strip():
			frappe.throw("Cancel Reason is required when Status is Aborted.")

	def _validate_rescheduled_readonly(self):
		"""Prevent editing schedules with Rescheduled status (historical record)."""
		if self.status == "Rescheduled" and not self.is_new():
			# Allow status change (e.g., if manually changing status)
			if self.has_value_changed("status"):
				return
			# Check if any other fields have changed
			changed_fields = [field for field in self.as_dict() if self.has_value_changed(field)]
			if changed_fields:
				frappe.throw("Cannot edit a schedule with Rescheduled status. It is a historical record.")

	def _validate_block_belongs_to_crop_plan(self):
		if not self.crop_plan or not self.block:
			return
		# Ensure block exists in crop plan blocks table
		exists = frappe.db.exists(
			"Crop Plan Block",
			{"parent": self.crop_plan, "parenttype": "Crop Plan", "parentfield": "blocks", "block": self.block},
		)
		if not exists:
			frappe.throw("Selected Block does not belong to the selected Crop Plan.")

	def _get_block_idx_in_crop_plan(self) -> str | None:
		if not self.crop_plan or not self.block:
			return None
		# We need the idx of the Crop Plan Block row, because activities store block_reference as that idx (string).
		row = frappe.get_all(
			"Crop Plan Block",
			filters={"parent": self.crop_plan, "parenttype": "Crop Plan", "parentfield": "blocks", "block": self.block},
			fields=["idx"],
			limit=1,
		)
		return str(row[0]["idx"]) if row else None

	def _validate_activity_matches_block(self):
		"""
		Ensure selected crop_plan_activity row belongs to this crop_plan and matches the selected block.
		"""
		if not self.crop_plan or not self.block or not self.crop_plan_activity:
			return
		block_idx = self._get_block_idx_in_crop_plan()
		if not block_idx:
			return
		act = frappe.db.get_value(
			"Crop Plan Activity",
			self.crop_plan_activity,
			["parent", "parenttype", "parentfield", "block_reference"],
			as_dict=True,
		)
		if not act:
			frappe.throw("Invalid Crop Plan Activity selected.")
		if act.parent != self.crop_plan or act.parenttype != "Crop Plan" or act.parentfield != "activities":
			frappe.throw("Selected Crop Plan Activity does not belong to the selected Crop Plan.")
		if str(act.block_reference) != str(block_idx):
			frappe.throw("Selected Crop Plan Activity does not belong to the selected Block in this Crop Plan.")

	def _autofill_activity_fields(self):
		if not self.crop_plan_activity:
			return
		row = frappe.db.get_value(
			"Crop Plan Activity",
			self.crop_plan_activity,
			["sequence", "activity", "activity_name", "activity_group_type"],
			as_dict=True,
		)
		if not row:
			return
		self.sequence = int(row.sequence or 0)
		self.farm_activity = row.activity
		self.activity_name = row.activity_name
		agt = (row.activity_group_type or "").lower()
		lbl = (self.activity_name or "").lower()
		self.is_spray = 1 if ("plant protection" in agt or "spray" in lbl) else 0

	def _autofill_approved_input_mix_from_plan(self):
		if not self.crop_plan or not self.crop_plan_activity:
			return
		# If already set, don't override
		if self.approved_input_mix:
			return
		farm_task = frappe.db.get_value(
			"Crop Plan Approved Input Mix",
			{
				"parent": self.crop_plan,
				"parenttype": "Crop Plan",
				"parentfield": "approved_input_mixes",
				"activity_reference": self.crop_plan_activity,
			},
			"farm_task",
		)
		if farm_task:
			self.approved_input_mix = farm_task

	def _compute_totals(self):
		# Single block
		self.total_acres = flt(self.block_area_acres, 3)
		self.total_seedlings = int(self.no_of_seedlings or 0)

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
def get_crop_plan_blocks(crop_plan: str) -> List[Dict[str, Any]]:
	"""
	Return blocks from Crop Plan along with derived area in acres and no_of_seedlings.
	"""
	doc = frappe.get_doc("Crop Plan", crop_plan)
	out: List[Dict[str, Any]] = []
	for b in doc.blocks or []:
		block_doc = frappe.get_doc("Geo Fencing Area", b.block) if b.block else None
		area_acres = 0.0
		if block_doc and block_doc.area:
			area_acres = flt(block_doc.area) * SQ_METERS_TO_ACRES

		out.append(
			{
				"block": b.block,
				"block_name": getattr(block_doc, "area_name", None) if block_doc else None,
				"block_area_acres": flt(area_acres, 3),
				"no_of_seedlings": int(b.no_of_seedlings or 0),
			}
		)
	return out


@frappe.whitelist()
def get_block_details(crop_plan: str, block: str) -> Dict[str, Any]:
	"""
	Return block_name, block_area_acres and no_of_seedlings from the crop plan.
	"""
	doc = frappe.get_doc("Crop Plan", crop_plan)
	for b in doc.blocks or []:
		if b.block != block:
			continue
		block_doc = frappe.get_doc("Geo Fencing Area", b.block) if b.block else None
		area_acres = 0.0
		if block_doc and block_doc.area:
			area_acres = flt(block_doc.area) * SQ_METERS_TO_ACRES
		return {
			"block": b.block,
			"block_name": getattr(block_doc, "area_name", None) if block_doc else None,
			"block_area_acres": flt(area_acres, 3),
			"no_of_seedlings": int(b.no_of_seedlings or 0),
		}
	return {}

@frappe.whitelist()
def get_schedule_defaults(crop_plan: str, crop_plan_activity: str) -> Dict[str, Any]:
	"""
	Return activity fields and the approved input mix (farm_task) from the Crop Plan for a given Crop Plan Activity row.

	This avoids using frappe.client.get_value from the browser (which can 403 due to parent permission checks).
	"""
	if not crop_plan or not crop_plan_activity:
		return {}

	# Ensure user can read the crop plan (basic guard)
	if not frappe.has_permission("Crop Plan", "read", crop_plan):
		frappe.throw("Not permitted", frappe.PermissionError)

	act = frappe.db.get_value(
		"Crop Plan Activity",
		crop_plan_activity,
		["parent", "parenttype", "parentfield", "sequence", "activity", "activity_name", "activity_group_type"],
		as_dict=True,
	)
	if not act:
		return {}
	if act.parent != crop_plan or act.parenttype != "Crop Plan" or act.parentfield != "activities":
		# Don't leak unrelated child rows
		frappe.throw("Invalid Crop Plan Activity selected.")

	farm_task = frappe.db.get_value(
		"Crop Plan Approved Input Mix",
		{
			"parent": crop_plan,
			"parenttype": "Crop Plan",
			"parentfield": "approved_input_mixes",
			"activity_reference": crop_plan_activity,
		},
		"farm_task",
	)

	agt = (act.activity_group_type or "").lower()
	lbl = (act.activity_name or "").lower()
	is_spray = 1 if ("plant protection" in agt or "spray" in lbl) else 0

	return {
		"sequence": int(act.sequence or 0),
		"farm_activity": act.activity,
		"activity_name": act.activity_name or "",
		"is_spray": is_spray,
		"approved_input_mix": farm_task or "",
	}


@frappe.whitelist()
def get_activity_options(crop_plan: str, blocks_json: str) -> List[Dict[str, Any]]:
	"""
	Return activities (sequence + farm_activity) that exist for ALL selected blocks in the Crop Plan.
	blocks_json: JSON array of Geo Fencing Area names.
	"""
	# Legacy: multi-block scheduling. Kept for backward compatibility.
	try:
		selected_blocks: List[str] = json.loads(blocks_json) if blocks_json else []
	except Exception:
		selected_blocks = []

	if not selected_blocks:
		return []

	doc = frappe.get_doc("Crop Plan", crop_plan)

	# Map GeoFencingArea block -> CropPlanBlock idx (1-based stored by Frappe as idx)
	block_to_idx: Dict[str, str] = {}
	for b in doc.blocks or []:
		if b.block:
			block_to_idx[b.block] = str(b.idx)

	selected_block_idxs = [block_to_idx.get(b) for b in selected_blocks if block_to_idx.get(b)]
	if len(selected_block_idxs) != len(selected_blocks):
		# Some blocks are not part of the crop plan, return empty so UI forces correct selection
		return []

	# Build per-block sets of activity keys (sequence + activity link)
	per_block_sets: List[Set[Tuple[int, str]]] = []
	for blk_idx in selected_block_idxs:
		keys: Set[Tuple[int, str]] = set()
		for act in doc.activities or []:
			if str(act.block_reference) != str(blk_idx):
				continue
			if act.activity:
				keys.add((int(act.sequence or 0), str(act.activity)))
		per_block_sets.append(keys)

	if not per_block_sets:
		return []

	common: Set[Tuple[int, str]] = set.intersection(*per_block_sets)
	if not common:
		return []

	# Precompute mix mapping: activity_reference -> farm_task
	activity_ref_to_farm_task: Dict[str, str] = {}
	for mix in doc.approved_input_mixes or []:
		if mix.activity_reference and mix.farm_task:
			activity_ref_to_farm_task[str(mix.activity_reference)] = str(mix.farm_task)

	# For labels, pull activity_name from the Crop Plan activities (first match)
	options: List[Dict[str, Any]] = []
	for seq, activity in sorted(common, key=lambda x: x[0]):
		activity_name = None
		activity_group_type = None

		# Determine if a common approved input mix exists across selected blocks for this activity
		farm_tasks_for_blocks: List[str] = []
		missing_mix_blocks: List[str] = []
		for blk, blk_idx in zip(selected_blocks, selected_block_idxs):
			act_ref = None
			for act in doc.activities or []:
				if str(act.block_reference) != str(blk_idx):
					continue
				if str(act.activity) != str(activity):
					continue
				if int(act.sequence or 0) != int(seq):
					continue
				act_ref = act.name
				break
			if not act_ref:
				missing_mix_blocks.append(blk)
				continue
			farm_task = activity_ref_to_farm_task.get(str(act_ref))
			if not farm_task:
				missing_mix_blocks.append(blk)
				continue
			farm_tasks_for_blocks.append(farm_task)

		unique_tasks = sorted(set(farm_tasks_for_blocks))
		common_farm_task = unique_tasks[0] if unique_tasks and len(unique_tasks) == 1 and not missing_mix_blocks else ""

		for act in doc.activities or []:
			if int(act.sequence or 0) == int(seq) and str(act.activity) == str(activity):
				activity_name = act.activity_name or activity_name
				activity_group_type = act.activity_group_type or activity_group_type
				break

		label = f"{seq} - {activity_name or activity}"
		options.append(
			{
				"label": label,
				"sequence": int(seq),
				"farm_activity": activity,
				"activity_name": activity_name or "",
				"activity_group_type": activity_group_type or "",
				"common_farm_task": common_farm_task,
				"has_common_mix": 1 if common_farm_task else 0,
			}
		)

	return options


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


@frappe.whitelist()
def get_common_approved_input_mix(
	crop_plan: str,
	blocks_json: str,
	sequence: int | str,
	farm_activity: str,
) -> Dict[str, Any]:
	"""
	Find a common Farm Task (Approved Input Mix) for the selected blocks + activity.

	We match using the Crop Plan's `approved_input_mixes` child table which stores `activity_reference`
	(the name of the Crop Plan Activity row).

	Returns:
	{
	  "common_farm_task": "FT-xxx" | null,
	  "farm_tasks": ["FT-xxx", ...],
	  "missing_blocks": ["BlockName", ...]
	}
	"""
	try:
		selected_blocks: List[str] = json.loads(blocks_json) if blocks_json else []
	except Exception:
		selected_blocks = []

	if not selected_blocks:
		return {"common_farm_task": None, "farm_tasks": [], "missing_blocks": []}

	seq = int(sequence or 0)
	doc = frappe.get_doc("Crop Plan", crop_plan)

	# Map GeoFencingArea block -> CropPlanBlock idx (1-based stored by Frappe as idx)
	block_to_idx: Dict[str, str] = {}
	for b in doc.blocks or []:
		if b.block:
			block_to_idx[b.block] = str(b.idx)

	selected_block_idxs = [block_to_idx.get(b) for b in selected_blocks if block_to_idx.get(b)]
	if len(selected_block_idxs) != len(selected_blocks):
		return {"common_farm_task": None, "farm_tasks": [], "missing_blocks": selected_blocks}

	# Find activity row names for each block (activity_reference)
	block_idx_to_activity_ref: Dict[str, str] = {}
	for blk_idx in selected_block_idxs:
		for act in doc.activities or []:
			if str(act.block_reference) != str(blk_idx):
				continue
			if str(act.activity) != str(farm_activity):
				continue
			if int(act.sequence or 0) != seq:
				continue
			block_idx_to_activity_ref[str(blk_idx)] = act.name
			break

	missing_blocks: List[str] = []
	farm_tasks: List[str] = []

	for blk, blk_idx in zip(selected_blocks, selected_block_idxs):
		act_ref = block_idx_to_activity_ref.get(str(blk_idx))
		if not act_ref:
			missing_blocks.append(blk)
			continue

		found_task = None
		for mix in doc.approved_input_mixes or []:
			if mix.activity_reference == act_ref and mix.farm_task:
				found_task = mix.farm_task
				break

		if not found_task:
			missing_blocks.append(blk)
			continue

		farm_tasks.append(found_task)

	unique_tasks = sorted(set(farm_tasks))
	common = unique_tasks[0] if unique_tasks and len(unique_tasks) == 1 and not missing_blocks else None

	return {"common_farm_task": common, "farm_tasks": unique_tasks, "missing_blocks": missing_blocks}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def crop_plan_activity_query(doctype, txt, searchfield, start, page_len, filters):
	"""
	Query Crop Plan Activity child rows for a given crop_plan + block.
	"""
	crop_plan = (filters or {}).get("crop_plan")
	block = (filters or {}).get("block")
	if not crop_plan or not block:
		return []

	# Find block idx in crop plan blocks table
	row = frappe.get_all(
		"Crop Plan Block",
		filters={"parent": crop_plan, "parenttype": "Crop Plan", "parentfield": "blocks", "block": block},
		fields=["idx"],
		limit=1,
	)
	if not row:
		return []
	blk_idx = str(row[0]["idx"])

	return frappe.db.sql(
		"""
		SELECT
			name,
			activity_name
		FROM `tabCrop Plan Activity`
		WHERE parent = %(crop_plan)s
		  AND parenttype = 'Crop Plan'
		  AND parentfield = 'activities'
		  AND block_reference = %(blk_idx)s
		  AND (activity_name LIKE %(txt)s OR activity LIKE %(txt)s OR name LIKE %(txt)s)
		ORDER BY sequence ASC
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"crop_plan": crop_plan,
			"blk_idx": blk_idx,
			"txt": f"%{txt}%",
			"start": start,
			"page_len": page_len,
		},
		as_dict=False,
	)


@frappe.whitelist()
def create_reschedule(
	schedule_name: str,
	reschedule_reason: str | None = None,
	planned_start: str | None = None,
	planned_end: str | None = None,
) -> str:
	"""
	Create a new Crop Plan Schedule by copying the given schedule.
	The new document is saved as Draft and points back via rescheduled_from.
	The original schedule is marked as "Rescheduled".
	
	Args:
		schedule_name: Name of the schedule to reschedule
		reschedule_reason: Optional reason for rescheduling
		planned_start: Optional new planned start datetime (if not provided, copies from original)
		planned_end: Optional new planned end datetime (if not provided, copies from original)
	
	Returns:
		Name of the newly created schedule document
	"""
	src = frappe.get_doc("Crop Plan Schedule", schedule_name)

	new_doc = frappe.get_doc({"doctype": "Crop Plan Schedule"})
	for field in [
		"crop_plan",
		"crop_plan_activity",
		"sequence",
		"farm_activity",
		"activity_name",
		"is_spray",
		"water_requirement_basis",
		"water_rate",
		"estimated_irrigation_water_liters",
		"approved_input_mix",
		"male_count",
		"female_count",
		"field",
		"block",
		"block_area_acres",
		"no_of_seedlings",
	]:
		new_doc.set(field, src.get(field))

	# Use new dates if provided, otherwise copy from original
	new_doc.planned_start = planned_start or src.planned_start
	new_doc.planned_end = planned_end or src.planned_end

	# Set status to Scheduled since user has provided new dates
	new_doc.status = "Scheduled"
	new_doc.rescheduled_from = src.name
	new_doc.reschedule_reason = reschedule_reason or src.reschedule_reason

	# Copy child tables
	for b in src.get("blocks") or []:
		new_doc.append(
			"blocks",
			{
				"block": b.block,
				"block_name": b.block_name,
				"block_area_acres": b.block_area_acres,
				"no_of_seedlings": b.no_of_seedlings,
			},
		)

	for it in src.get("inputs") or []:
		new_doc.append(
			"inputs",
			{
				"item": it.item,
				"item_name": it.item_name,
				"rate_quantity": it.rate_quantity,
				"unit": it.unit,
				"total_quantity_to_use": it.total_quantity_to_use,
			},
		)

	# Copy machinery
	for m in src.get("machinery") or []:
		new_doc.append(
			"machinery",
			{
				"asset": m.asset,
				"asset_name": m.asset_name,
				"planned_hours": m.planned_hours,
				"remarks": m.remarks,
			},
		)
	# Copy implements
	for imp in src.get("implements") or []:
		new_doc.append(
			"implements",
			{
				"asset": imp.asset,
				"asset_name": imp.asset_name,
				"planned_hours": imp.planned_hours,
				"remarks": imp.remarks,
			},
		)
	# Copy hand tools
	for ht in src.get("hand_tools") or []:
		new_doc.append(
			"hand_tools",
			{
				"asset": ht.asset,
				"asset_name": ht.asset_name,
				"planned_hours": ht.planned_hours,
				"remarks": ht.remarks,
			},
		)
	# Copy other tools
	for ot in src.get("other_tools") or []:
		new_doc.append(
			"other_tools",
			{
				"asset": ot.asset,
				"asset_name": ot.asset_name,
				"planned_hours": ot.planned_hours,
				"remarks": ot.remarks,
			},
		)

	new_doc.insert(ignore_permissions=True)
	
	# Mark original schedule as "Rescheduled"
	src.reload()
	src.db_set("status", "Rescheduled", update_modified=False)
	frappe.db.commit()
	
	return new_doc.name


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def crop_plan_block_query(doctype, txt, searchfield, start, page_len, filters):
	"""
	Custom query for selecting blocks that belong to a given Crop Plan.
	Used by the blocks child table Link field.
	"""
	crop_plan = (filters or {}).get("crop_plan")
	if not crop_plan:
		return []

	doc = frappe.get_doc("Crop Plan", crop_plan)
	allowed_blocks = [b.block for b in (doc.blocks or []) if b.block]
	if not allowed_blocks:
		return []

	# Limit list size defensively for SQL IN clause
	allowed_blocks = allowed_blocks[:500]

	return frappe.db.sql(
		"""
		SELECT
			name,
			area_name
		FROM `tabGeo Fencing Area`
		WHERE name IN %(allowed)s
		  AND (area_name LIKE %(txt)s OR name LIKE %(txt)s)
		ORDER BY area_name
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"allowed": tuple(allowed_blocks),
			"txt": f"%{txt}%",
			"start": start,
			"page_len": page_len,
		},
		as_dict=False,
	)



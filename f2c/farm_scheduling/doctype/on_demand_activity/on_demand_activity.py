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
		self._validate_activity_type()
		
		# Set default status based on activity type (before other validations)
		if not self.status:
			if self.activity_type == "Campaign":
				self.status = "Draft"
			elif self.activity_type == "Land":
				self.status = "Scheduled"
		
		if self.activity_type == "Campaign":
			self._validate_campaign_structure()
		elif self.activity_type == "Land":
			self._validate_land_structure()
		
		# Only validate dates if they are provided
		self._validate_dates()
		
		# Only validate blocks if they exist (for Land or Campaign child entries)
		if self.activity_type == "Land" or (self.activity_type == "Campaign" and self.campaign_parent):
			self._validate_blocks_belong_to_field()
		
		# Only validate activity belongs to group if activity is set
		if self.activity and self.activity_group_type:
			self._validate_activity_belongs_to_group()
		
		# Only autofill if activity is set
		if self.activity:
			self._autofill_activity_fields()
			self._validate_approved_input_mix_required()
		
		# Only compute totals if blocks exist
		if self.get("blocks") and len(self.get("blocks", [])) > 0:
			self._compute_totals()
		
		self._compute_labour_total()
		self._compute_water()
		self._validate_spray_requirements()
		self._recompute_input_totals_if_needed()

	def _validate_reason(self):
		if not (self.reason or "").strip():
			frappe.throw("Reason is required for On Demand Activity.")
	
	def _validate_activity_type(self):
		if not self.activity_type:
			self.activity_type = "Land"  # Default for backward compatibility
		if self.activity_type not in ["Campaign", "Land"]:
			frappe.throw("Activity Type must be either 'Campaign' or 'Land'.")
	
	def _validate_campaign_structure(self):
		"""Validate campaign structure - for Draft campaigns, only need reason and activities."""
		if self.status == "Draft":
			# Check if campaign_activities table has entries
			has_campaign_activities = self.get("campaign_activities") and len(self.campaign_activities) > 0
			
			if has_campaign_activities:
				# Validate each activity in campaign_activities
				for act_row in self.campaign_activities:
					if not act_row.activity:
						frappe.throw("Activity is required in Campaign Activities.")
					if not act_row.activity_group_type:
						frappe.throw("Activity Group Type is required in Campaign Activities.")
				
				# Set activity and activity_group_type from first entry for backward compatibility
				if not self.activity:
					self.activity = self.campaign_activities[0].activity
				if not self.activity_group_type:
					self.activity_group_type = self.campaign_activities[0].activity_group_type
			else:
				# Fall back to single activity field for backward compatibility
				if not self.activity:
					frappe.throw("Activity is required for Campaign. Please add at least one activity in Campaign Activities.")
				if not self.activity_group_type:
					frappe.throw("Activity Group Type is required for Campaign.")
		elif self.status == "Scheduled":
			# For scheduled campaigns, validate scheduling fields
			if not self.planned_start:
				frappe.throw("Planned Start is required for scheduled Campaign.")
			if not self.planned_end:
				frappe.throw("Planned End is required for scheduled Campaign.")
			# If this is a parent campaign, it should have campaign_areas
			# If this is a child campaign, it should have field and blocks
			if not self.campaign_parent:
				# Parent campaign - should have campaign_areas (handled during scheduling)
				pass
			else:
				# Child campaign - should have field and blocks
				if not self.field:
					frappe.throw("Field is required for campaign child entries.")
				if not self.get("blocks") or len(self.blocks) == 0:
					frappe.throw("At least one block is required for campaign child entries.")
	
	def _validate_land_structure(self):
		"""Validate land structure - requires field, blocks, dates, and all required fields."""
		if not self.field:
			frappe.throw("Field is required for Land type activity.")
		if not self.get("blocks") or len(self.blocks) == 0:
			frappe.throw("At least one block is required for Land type activity.")
		if not self.planned_start:
			frappe.throw("Planned Start is required for Land type activity.")
		if not self.planned_end:
			frappe.throw("Planned End is required for Land type activity.")
		if not self.activity:
			frappe.throw("Activity is required for Land type activity.")
		if not self.activity_group_type:
			frappe.throw("Activity Group Type is required for Land type activity.")

	def _validate_dates(self):
		# Only validate dates if they are provided (not required for Campaign Draft)
		if self.planned_start and self.planned_end:
			if self.planned_end <= self.planned_start:
				frappe.throw("Planned End must be after Planned Start.")

	def _validate_blocks_belong_to_field(self):
		# Only validate for Land type or Campaign child entries
		if self.activity_type == "Campaign" and not self.campaign_parent:
			return  # Parent campaigns don't have blocks
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
		# Sum all blocks (only for Land or Campaign child entries)
		if self.activity_type == "Campaign" and not self.campaign_parent:
			return  # Parent campaigns don't have blocks
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


@frappe.whitelist()
def schedule_campaign(
	campaign_name: str,
	planned_start: str,
	planned_end: str,
	campaign_level: str,
	selected_areas: List[str]
) -> Dict[str, Any]:
	"""
	Schedule a campaign by creating block-level On Demand Activity entries.
	
	Args:
		campaign_name: Name of the parent campaign
		planned_start: Start datetime for the campaign
		planned_end: End datetime for the campaign
		campaign_level: Level of campaign (Farm/Cluster/Field)
		selected_areas: List of area names at the selected level
		
	Returns:
		Dictionary with created_entries count and list of created entry names
	"""
	# Get the parent campaign
	parent_campaign = frappe.get_doc("On Demand Activity", campaign_name)
	
	if parent_campaign.activity_type != "Campaign":
		frappe.throw("Only Campaign type activities can be scheduled.")
	
	if parent_campaign.status != "Draft":
		frappe.throw("Only Draft campaigns can be scheduled.")
	
	# Validate dates
	if planned_end <= planned_start:
		frappe.throw("Planned End must be after Planned Start.")
	
	# Get activities from campaign_activities table, or fall back to single activity field
	campaign_activities = []
	if parent_campaign.get("campaign_activities") and len(parent_campaign.campaign_activities) > 0:
		for act_row in parent_campaign.campaign_activities:
			campaign_activities.append({
				"activity": act_row.activity,
				"activity_name": act_row.activity_name or "",
				"activity_group_type": act_row.activity_group_type or parent_campaign.activity_group_type
			})
	else:
		# Fall back to single activity field for backward compatibility
		if parent_campaign.activity:
			campaign_activities.append({
				"activity": parent_campaign.activity,
				"activity_name": parent_campaign.activity_name or "",
				"activity_group_type": parent_campaign.activity_group_type or ""
			})
	
	if not campaign_activities:
		frappe.throw("Campaign must have at least one activity.")
	
	# Get all blocks based on level and selected areas
	all_blocks = []
	
	if campaign_level == "Farm":
		# For each farm, get all clusters, then all fields, then all blocks
		for farm_name in selected_areas:
			clusters = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": farm_name, "geo_fencing_type": "Cluster"},
				fields=["name"]
			)
			for cluster in clusters:
				fields = frappe.get_all(
					"Geo Fencing Area",
					filters={"parent_area": cluster.name, "geo_fencing_type": "Field"},
					fields=["name"]
				)
				for field in fields:
					blocks = frappe.get_all(
						"Geo Fencing Area",
						filters={"parent_area": field.name, "geo_fencing_type": "Block"},
						fields=["name", "area_name", "parent_area"]
					)
					for block in blocks:
						all_blocks.append({
							"block": block.name,
							"block_name": block.area_name,
							"field": field.name,
							"field_name": frappe.get_value("Geo Fencing Area", field.name, "area_name")
						})
	elif campaign_level == "Cluster":
		# For each cluster, get all fields, then all blocks
		for cluster_name in selected_areas:
			fields = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": cluster_name, "geo_fencing_type": "Field"},
				fields=["name"]
			)
			for field in fields:
				blocks = frappe.get_all(
					"Geo Fencing Area",
					filters={"parent_area": field.name, "geo_fencing_type": "Block"},
					fields=["name", "area_name", "parent_area"]
				)
				for block in blocks:
					all_blocks.append({
						"block": block.name,
						"block_name": block.area_name,
						"field": field.name,
						"field_name": frappe.get_value("Geo Fencing Area", field.name, "area_name")
					})
	elif campaign_level == "Field":
		# For each field, get all blocks
		for field_name in selected_areas:
			blocks = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": field_name, "geo_fencing_type": "Block"},
				fields=["name", "area_name", "parent_area"]
			)
			for block in blocks:
				all_blocks.append({
					"block": block.name,
					"block_name": block.area_name,
					"field": field_name,
					"field_name": frappe.get_value("Geo Fencing Area", field_name, "area_name")
				})
	
	if not all_blocks:
		frappe.throw(f"No blocks found for the selected {campaign_level.lower()}(s).")
	
	# Create On Demand Activity entry for each block and each activity
	created_entries = []
	
	for campaign_activity in campaign_activities:
		for block_info in all_blocks:
			# Get block details
			block_doc = frappe.get_doc("Geo Fencing Area", block_info["block"])
			area_acres = 0.0
			if block_doc.area:
				area_acres = flt(block_doc.area) * SQ_METERS_TO_ACRES
			no_of_seedlings = getattr(block_doc, "no_of_seedlings", 0) or 0
			
			# Get activity doc to check for spray and other details
			activity_doc = frappe.get_doc("Farm Activity", campaign_activity["activity"])
			agt = (activity_doc.activity_group_type or "").lower()
			lbl = (activity_doc.activity_name or "").lower()
			is_spray = 1 if ("plant protection" in agt or "spray" in lbl) else 0
			
			# Create child activity entry
			child_activity = frappe.get_doc({
				"doctype": "On Demand Activity",
				"activity_type": "Campaign",
				"campaign_parent": campaign_name,
				"field": block_info["field"],
				"activity": campaign_activity["activity"],
				"activity_group_type": campaign_activity["activity_group_type"],
				"activity_name": campaign_activity["activity_name"],
				"reason": parent_campaign.reason,
				"planned_start": planned_start,
				"planned_end": planned_end,
				"status": "Scheduled",
				"is_spray": is_spray,
				"water_requirement_basis": parent_campaign.water_requirement_basis or "",
				"water_rate": parent_campaign.water_rate or 0,
				"approved_input_mix": parent_campaign.approved_input_mix or "",
				"male_count": parent_campaign.male_count or 0,
				"female_count": parent_campaign.female_count or 0,
			})
			
			# Add block
			child_activity.append("blocks", {
				"block": block_info["block"],
				"block_name": block_info["block_name"],
				"block_area_acres": flt(area_acres, 3),
				"no_of_seedlings": int(no_of_seedlings)
			})
			
			# Copy equipment if any (from parent campaign if available)
			if parent_campaign.get("machinery"):
				for mach in parent_campaign.machinery:
					child_activity.append("machinery", {
						"asset": mach.asset,
						"asset_name": mach.asset_name,
						"planned_hours": mach.planned_hours or 0,
						"remarks": mach.remarks or ""
					})
			if parent_campaign.get("implements"):
				for impl in parent_campaign.implements:
					child_activity.append("implements", {
						"asset": impl.asset,
						"asset_name": impl.asset_name,
						"planned_hours": impl.planned_hours or 0,
						"remarks": impl.remarks or ""
					})
			if parent_campaign.get("hand_tools"):
				for ht in parent_campaign.hand_tools:
					child_activity.append("hand_tools", {
						"asset": ht.asset,
						"asset_name": ht.asset_name,
						"planned_hours": ht.planned_hours or 0,
						"remarks": ht.remarks or ""
					})
			if parent_campaign.get("other_tools"):
				for ot in parent_campaign.other_tools:
					child_activity.append("other_tools", {
						"asset": ot.asset,
						"asset_name": ot.asset_name,
						"planned_hours": ot.planned_hours or 0,
						"remarks": ot.remarks or ""
					})
			
			child_activity.insert()
			created_entries.append(child_activity.name)
	
	# Update parent campaign status and store campaign areas
	parent_campaign.status = "Scheduled"
	parent_campaign.planned_start = planned_start
	parent_campaign.planned_end = planned_end
	parent_campaign.campaign_level = campaign_level
	
	# Store selected areas in campaign_areas table
	parent_campaign.set("campaign_areas", [])
	for area_name in selected_areas:
		area_doc = frappe.get_doc("Geo Fencing Area", area_name)
		parent_campaign.append("campaign_areas", {
			"area": area_name,
			"area_name": area_doc.area_name,
			"area_type": area_doc.geo_fencing_type
		})
	
	parent_campaign.save()
	
	return {
		"created_entries": len(created_entries),
		"entry_names": created_entries
	}


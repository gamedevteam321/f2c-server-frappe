# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple

import frappe
from frappe.model.document import Document
from frappe.utils import flt, get_datetime

SQ_METERS_TO_ACRES = 0.000247105


class OnDemandActivity(Document):
	def validate(self):
		self._ltt_forward_planned_warn_shown = False
		self._ltt_return_planned_warn_shown = False
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
		
		# Populate inputs from approved_input_mix if set
		if self.approved_input_mix:
			self._populate_inputs_from_approved_mix()
		
		# Only compute totals if blocks exist
		if self.get("blocks") and len(self.get("blocks", [])) > 0:
			self._compute_totals()
		
		self._compute_labour_total()
		self._compute_water()
		self._validate_spray_requirements()
		self._recompute_input_totals_if_needed()
		# Validate equipment slot availability (only for Scheduled status with dates)
		if self.status == "Scheduled" and self.planned_start and self.planned_end and self.field:
			self._validate_equipment_slot_availability()

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
		# Campaign name is required for Campaign type activities
		if not self.campaign_name or not self.campaign_name.strip():
			frappe.throw("Campaign Name is required for Campaign type activities.")
		
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
	
	def _populate_inputs_from_approved_mix(self):
		"""Populate inputs table from approved_input_mix (Farm Tasks).
		Only populates if inputs are not already provided (to preserve user edits).
		If approved_input_mix changed, repopulate from the new mix."""
		if not self.approved_input_mix:
			# Clear inputs if approved_input_mix is removed
			if self.get("inputs"):
				self.set("inputs", [])
			return
		
		# Check if approved_input_mix has changed (for existing documents)
		approved_input_mix_changed = False
		if not self.is_new():
			# Get the previous value from the database
			try:
				prev_doc = frappe.get_doc(self.doctype, self.name)
				if prev_doc.approved_input_mix != self.approved_input_mix:
					approved_input_mix_changed = True
			except (frappe.DoesNotExistError, frappe.DocumentNotFoundError):
				# Document doesn't exist yet, treat as new
				pass
		
		# If inputs are already provided and approved_input_mix hasn't changed, preserve them
		# This allows users to edit quantities without them being overwritten
		existing_inputs = self.get("inputs") or []
		if existing_inputs and len(existing_inputs) > 0 and not approved_input_mix_changed:
			# Inputs exist and approved_input_mix hasn't changed - preserve user edits
			return
		
		try:
			farm_task_doc = frappe.get_doc("Farm Tasks", self.approved_input_mix)
			
			# Clear existing inputs and populate from Farm Tasks
			self.set("inputs", [])
			
			for item_row in farm_task_doc.get("items") or []:
				self.append("inputs", {
					"item": item_row.item,
					"item_name": item_row.item_name,
					"rate_quantity": flt(item_row.quantity, 3),
					"unit": item_row.unit or "ml/L"
				})
		except frappe.DoesNotExistError:
			# Farm Task doesn't exist, clear inputs
			frappe.log_error(f"Farm Task '{self.approved_input_mix}' not found when populating inputs", "On Demand Activity Warning")
			self.set("inputs", [])
		except Exception as e:
			frappe.log_error(f"Error populating inputs from approved_input_mix: {str(e)}", "On Demand Activity Error")
			# Don't throw - allow document to save even if inputs can't be populated

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
		# Skip validation for Draft campaigns - water fields will be set during scheduling
		# Also skip for child campaign entries - they inherit from parent and parent may not have water settings if Draft
		# Skip when aborting — execution is not happening; water fields may never have been filled
		if self.status == "Aborted":
			return
		if not self.is_spray:
			return
		
		if self.activity_type == "Campaign" and (self.status == "Draft" or self.campaign_parent):
			return

		if not self.water_requirement_basis:
			frappe.throw("Water Requirement is mandatory for Spray activities.")
		if not flt(self.water_rate):
			frappe.throw("Water Quantity is mandatory for Spray activities.")
		if flt(self.water_to_be_used_liters) <= 0:
			frappe.throw("Water to be Used (Liters) must be greater than 0 for Spray activities.")

	def _recompute_input_totals_if_needed(self):
		# For spray activities, compute total quantity for items based on water_to_be_used_liters.
		# For non-spray, use rate × total_acres for Bags/Acre and rate_quantity as fallback for other units.
		if not self.get("inputs"):
			return

		if not self.is_spray:
			total_acres = flt(self.total_acres)
			for row in self.inputs:
				total = compute_total_qty(
					water_liters=0,
					total_acres=total_acres,
					rate=flt(row.rate_quantity),
					unit=(row.unit or "").strip(),
				)
				if total <= 0 and flt(row.rate_quantity) > 0:
					total = flt(row.rate_quantity, 3)
				row.total_quantity_to_use = total
				if total and (row.unit or "").strip():
					row.quantity_to_use_display = f"{flt(total, 3)} {(row.unit or '').strip()}"
				else:
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

	def _get_cluster_for_field(self, field_name: str) -> str | None:
		"""Get the cluster (Geo Fencing Area with type='Cluster') for a given field by traversing parent_area hierarchy."""
		if not field_name:
			return None
		
		try:
			field_doc = frappe.get_doc("Geo Fencing Area", field_name)
			if not field_doc:
				return None
			
			# If field itself is a cluster (edge case), return it
			if field_doc.geo_fencing_type == "Cluster":
				return field_name
			
			# Traverse up the parent_area hierarchy to find Cluster
			current = field_doc
			visited = set()
			max_depth = 10  # Prevent infinite loops
			depth = 0
			
			while current and current.parent_area and depth < max_depth:
				if current.parent_area in visited:
					break  # Circular reference detected
				visited.add(current.parent_area)
				
				parent = frappe.get_doc("Geo Fencing Area", current.parent_area)
				if parent.geo_fencing_type == "Cluster":
					return parent.name
				
				current = parent
				depth += 1
			
			return None
		except Exception as e:
			frappe.log_error(f"Error getting cluster for field {field_name}: {str(e)}", "Equipment Slot Validation")
			return None

	def _collect_equipment_assets(self) -> List[str]:
		"""Helper to collect all unique assets from all equipment tables."""
		assets = set()
		for m in self.get("machinery") or []:
			if m.asset:
				assets.add(m.asset)
		for imp in self.get("implements") or []:
			if imp.asset:
				assets.add(imp.asset)
		for ht in self.get("hand_tools") or []:
			if ht.asset:
				assets.add(ht.asset)
		for ot in self.get("other_tools") or []:
			if ot.asset:
				assets.add(ot.asset)
		return list(assets)

	def _is_field_machinery_equipment_asset(self, asset_name: str | None) -> bool:
		"""Tractor, thresher, implement — move on machinery LTT, not on pickup/inputs LTT."""
		if not asset_name:
			return False
		row = frappe.db.get_value(
			"Machinery", {"asset": asset_name}, ["machinery_type"], as_dict=True,
		)
		if row:
			mtype = (row.get("machinery_type") or "").strip()
			return mtype != "Vehicle"
		if frappe.db.exists("Implement", {"asset": asset_name}):
			return True
		return False

	def _collect_machinery_and_implement_assets(self) -> List[str]:
		"""Assets for machinery LTT: machinery + implements + tractors mis-filed under hand/other tools."""
		assets = set()
		for m in self.get("machinery") or []:
			if m.asset:
				assets.add(m.asset)
		for imp in self.get("implements") or []:
			if imp.asset:
				assets.add(imp.asset)
		for ht in self.get("hand_tools") or []:
			if ht.asset and self._is_field_machinery_equipment_asset(ht.asset):
				assets.add(ht.asset)
		for ot in self.get("other_tools") or []:
			if ot.asset and self._is_field_machinery_equipment_asset(ot.asset):
				assets.add(ot.asset)
		return list(assets)

	def _collect_hand_and_other_tool_assets(self) -> List[str]:
		"""Assets for vehicle LTT: hand tools + other tools only (not tractors/machinery)."""
		assets = set()
		for ht in self.get("hand_tools") or []:
			if ht.asset and not self._is_field_machinery_equipment_asset(ht.asset):
				assets.add(ht.asset)
		for ot in self.get("other_tools") or []:
			if ot.asset and not self._is_field_machinery_equipment_asset(ot.asset):
				assets.add(ot.asset)
		return list(assets)

	def _get_source_warehouse_for_equipment_asset(self, asset: str) -> str | None:
		"""Source warehouse for an asset (location → warehouse), fallback to cluster."""
		if not asset or not self.field:
			return None
		cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
		if not cluster_warehouse:
			return None
		wh = self._get_asset_current_warehouse(asset)
		return wh or cluster_warehouse

	def _machinery_transport_vehicle_for_primary_asset(self, primary_asset: str) -> str | None:
		"""Machinery doc name for LTT transport_vehicle when the unit moves under its own power."""
		from f2c.inventory.logistics_transfer_ticket_api import self_transport_machinery_name_for_asset

		return self_transport_machinery_name_for_asset(primary_asset)

	def _machinery_transfer_unit_payloads(self) -> List[tuple[list[dict], str | None]]:
		"""One LTT per machinery row; standalone implements; promoted hand/other machinery."""
		from f2c.inventory.logistics_transfer_ticket_api import expand_machinery_transfer_asset_requests
		from f2c.inventory.tractor_implement_ltt_plan import (
			implement_asset_ids_paired_but_unattached,
			implement_asset_ids_paired_to_tractors_on_schedule,
		)

		paired_impl_assets = implement_asset_ids_paired_to_tractors_on_schedule(self)
		unattached_paired_impl_assets = implement_asset_ids_paired_but_unattached(self)
		covered: set[str] = set()
		units: list[tuple[list[dict], str | None]] = []

		def add_unit(primary: str) -> None:
			if not primary or primary in covered:
				return
			reqs = expand_machinery_transfer_asset_requests(primary, None)
			tv = self._machinery_transport_vehicle_for_primary_asset(primary)
			units.append((reqs, tv))
			for r in reqs:
				a = r.get("asset")
				if a:
					covered.add(a)

		for m in self.get("machinery") or []:
			add_unit(m.asset)
		for imp in self.get("implements") or []:
			if (
				imp.asset
				and imp.asset not in covered
				and imp.asset not in paired_impl_assets
				and imp.asset not in unattached_paired_impl_assets
			):
				add_unit(imp.asset)
		for ht in self.get("hand_tools") or []:
			if ht.asset and self._is_field_machinery_equipment_asset(ht.asset) and ht.asset not in covered:
				add_unit(ht.asset)
		for ot in self.get("other_tools") or []:
			if ot.asset and self._is_field_machinery_equipment_asset(ot.asset) and ot.asset not in covered:
				add_unit(ot.asset)

		return units

	def _check_time_overlap(self, start1: str, end1: str, start2: str, end2: str) -> bool:
		"""Check if two time ranges overlap."""
		from frappe.utils import get_datetime
		
		try:
			start1_dt = get_datetime(start1)
			end1_dt = get_datetime(end1)
			start2_dt = get_datetime(start2)
			end2_dt = get_datetime(end2)
			
			# Check if ranges overlap: start1 < end2 AND start2 < end1
			return start1_dt < end2_dt and start2_dt < end1_dt
		except Exception:
			return False

	def _check_overlapping_schedules_in_cps(self, equipment_assets: List[str], cluster: str) -> List[Dict]:
		"""Query Crop Plan Schedule tables for conflicts (all equipment types in one query), filtered by cluster."""
		if not equipment_assets or not cluster or not self.planned_start or not self.planned_end:
			return []
		
		conflicts = []
		
		# Build a single query using UNION ALL to check all equipment types at once
		# Use parameterized query for security
		placeholders = ', '.join(['%s'] * len(equipment_assets))
		
		query = f"""
			SELECT DISTINCT 
				cps.name as schedule_name,
				cps.planned_start,
				cps.planned_end,
				cps.status,
				eq.asset,
				eq.asset_name,
				cps.field,
				cps.block
			FROM `tabCrop Plan Schedule` cps
			INNER JOIN (
				SELECT parent, asset, asset_name FROM `tabCrop Plan Schedule Machinery` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset, asset_name FROM `tabCrop Plan Schedule Implement` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset, asset_name FROM `tabCrop Plan Schedule Hand Tool` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset, asset_name FROM `tabCrop Plan Schedule Other Tool` WHERE asset IN ({placeholders})
			) eq ON cps.name = eq.parent
			WHERE cps.status IN ('Scheduled', 'Reported')
				AND cps.planned_start IS NOT NULL
				AND cps.planned_end IS NOT NULL
		"""
		
		# Get all schedules with matching assets, then filter by cluster in Python
		params = equipment_assets * 4  # 4 times for each UNION ALL
		schedules = frappe.db.sql(query, params, as_dict=True)
		
		# Filter by cluster - check if each schedule's field belongs to the same cluster
		for schedule in schedules:
			if not schedule.get("field"):
				continue
			
			schedule_cluster = self._get_cluster_for_field(schedule.field)
			if schedule_cluster != cluster:
				continue  # Different cluster, skip
			
			# Check for time overlap
			if self._check_time_overlap(
				self.planned_start,
				self.planned_end,
				schedule.planned_start,
				schedule.planned_end
			):
				# Get block name for better error message
				block_name = schedule.get("block") or ""
				if block_name:
					try:
						block_doc = frappe.get_doc("Geo Fencing Area", block_name)
						block_name = getattr(block_doc, "area_name", block_name)
					except Exception:
						pass
				
				conflicts.append({
					'asset': schedule.asset,
					'asset_name': schedule.asset_name or schedule.asset,
					'schedule_name': schedule.schedule_name,
					'schedule_type': 'Crop Plan Schedule',
					'start': schedule.planned_start,
					'end': schedule.planned_end,
					'block': schedule.get("block") or "",
					'block_name': block_name
				})
		
		return conflicts

	def _check_overlapping_schedules_in_oda(self, equipment_assets: List[str], cluster: str) -> List[Dict]:
		"""Query On Demand Activity tables for conflicts (all equipment types in one query), filtered by cluster."""
		if not equipment_assets or not cluster or not self.planned_start or not self.planned_end:
			return []
		
		conflicts = []
		
		# Build a single query using UNION ALL to check all equipment types at once
		# Use parameterized query for security
		placeholders = ', '.join(['%s'] * len(equipment_assets))
		
		# Handle new activities (self.name might be None or empty)
		name_filter = "oda.name != %s" if self.name else "1=1"
		query = f"""
			SELECT DISTINCT 
				oda.name as activity_name,
				oda.planned_start,
				oda.planned_end,
				oda.status,
				eq.asset,
				eq.asset_name,
				oda.field
			FROM `tabOn Demand Activity` oda
			INNER JOIN (
				SELECT parent, asset, asset_name FROM `tabOn Demand Activity Machinery` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset, asset_name FROM `tabOn Demand Activity Implement` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset, asset_name FROM `tabOn Demand Activity Hand Tool` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset, asset_name FROM `tabOn Demand Activity Other Tool` WHERE asset IN ({placeholders})
			) eq ON oda.name = eq.parent
			WHERE {name_filter}
				AND oda.status IN ('Scheduled', 'Reported')
				AND oda.planned_start IS NOT NULL
				AND oda.planned_end IS NOT NULL
		"""
		
		# Get all activities with matching assets, then filter by cluster in Python
		params = equipment_assets * 4  # 4 times for each UNION ALL
		if self.name:
			params.append(self.name)
		activities = frappe.db.sql(query, params, as_dict=True)
		
		# Filter by cluster - check if each activity's field belongs to the same cluster
		for activity in activities:
			# Explicitly exclude the current activity when editing
			if self.name and activity.activity_name == self.name:
				continue
			
			if not activity.get("field"):
				continue
			
			activity_cluster = self._get_cluster_for_field(activity.field)
			if activity_cluster != cluster:
				continue  # Different cluster, skip
			
			# Check for time overlap
			if self._check_time_overlap(
				self.planned_start,
				self.planned_end,
				activity.planned_start,
				activity.planned_end
			):
				# On Demand Activity has blocks in a child table, so we can't easily get block info here
				# For now, we'll skip block information for ODA conflicts
				conflicts.append({
					'asset': activity.asset,
					'asset_name': activity.asset_name or activity.asset,
					'schedule_name': activity.activity_name,
					'schedule_type': 'On Demand Activity',
					'start': activity.planned_start,
					'end': activity.planned_end,
					'block': "",
					'block_name': ""
				})
		
		return conflicts

	def _validate_equipment_slot_availability(self):
		"""Check if equipment slots are already booked for the given time period within the same cluster."""
		if not self.planned_start or not self.planned_end:
			return
		
		if self.status != "Scheduled":
			return  # Only validate scheduled activities
		
		if not self.field:
			return  # No field specified, skip validation
		
		# Get the cluster for the current activity's field
		cluster = self._get_cluster_for_field(self.field)
		if not cluster:
			# Field has no cluster, skip validation (edge case)
			return
		
		# Collect all equipment assets
		equipment_assets = self._collect_equipment_assets()
		if not equipment_assets:
			return  # No equipment to validate
		
		# Check for overlapping schedules in Crop Plan Schedule
		overlapping_schedules = self._check_overlapping_schedules_in_cps(equipment_assets, cluster)
		
		# Check for overlapping schedules in On Demand Activity
		overlapping_activities = self._check_overlapping_schedules_in_oda(equipment_assets, cluster)
		
		# Report conflicts
		conflicts = []
		if overlapping_schedules:
			conflicts.extend(overlapping_schedules)
		if overlapping_activities:
			conflicts.extend(overlapping_activities)
		
		if conflicts:
			conflict_messages = []
			for conflict in conflicts:
				block_info = ""
				if conflict.get('block_name'):
					block_info = f" for block {conflict['block_name']}"
				elif conflict.get('block'):
					block_info = f" for block {conflict['block']}"
				
				conflict_messages.append(
					f"Asset {conflict['asset_name']} ({conflict['asset']}) is already booked{block_info} "
					f"in {conflict['schedule_type']} {conflict['schedule_name']} "
					f"({conflict['start']} to {conflict['end']})"
				)
			frappe.throw(
				"Equipment slot conflict detected:\n\n" + "\n".join(conflict_messages),
				title="Slot Already Booked"
			)

	def _get_warehouse_from_location(self, location: str) -> str | None:
		"""Get warehouse from Location using reverse lookup."""
		if not location:
			return None
		
		try:
			# Get all warehouses and check which one maps to this location
			from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
			
			warehouses = frappe.get_all("Warehouse", fields=["name"], limit=1000)
			for wh in warehouses:
				try:
					result = get_location_for_warehouse(wh.name)
					if result and result.get("location") == location:
						return wh.name
				except Exception:
					continue
			
			return None
		except Exception as e:
			frappe.log_error(f"Error getting warehouse from location {location}: {str(e)}", "Equipment Transfer Ticket")
			return None

	def _get_target_warehouse_for_field(self, field_name: str) -> str | None:
		"""Get target warehouse for field/block using get_warehouses_for_geo_area()."""
		if not field_name:
			return None
		
		try:
			from f2c.inventory.logistics_transfer_ticket_api import get_warehouses_for_geo_area
			
			result = get_warehouses_for_geo_area(field_name, strict_geo_area=1)
			warehouses = result.get("warehouses", []) if result else []
			
			if warehouses and len(warehouses) > 0:
				return warehouses[0]  # Use first warehouse
			
			return None
		except Exception as e:
			frappe.log_error(f"Error getting target warehouse for field {field_name}: {str(e)}", "Equipment Transfer Ticket")
			return None

	def _get_cluster_warehouse_for_field(self, field_name: str) -> str | None:
		"""Get cluster ledger warehouse (stock-holding) for a field.
		
		Warehouse hierarchy: Farm Warehouse (top) -> Cluster Warehouse (middle) -> Field Warehouse (bottom)
		Returns the ledger (stock) warehouse for the cluster, not the group.
		"""
		if not field_name:
			return None
		try:
			from f2c.inventory.warehouse_utils import get_ledger_warehouse
		except Exception:
			get_ledger_warehouse = None
		try:
			# Get field warehouse first
			field_warehouse = self._get_target_warehouse_for_field(field_name)
			if not field_warehouse:
				frappe.log_error(f"Cannot find field warehouse for field {field_name}", "Equipment Transfer Ticket")
				return None
			
			# Get parent_warehouse from field warehouse (should be cluster warehouse)
			# Hierarchy: Farm -> Cluster -> Field
			parent_warehouse = frappe.db.get_value("Warehouse", field_warehouse, "parent_warehouse")
			if parent_warehouse:
				return (get_ledger_warehouse(parent_warehouse) or parent_warehouse) if get_ledger_warehouse else parent_warehouse
			
			# Fallback: Get cluster from Geo Fencing Area and find its warehouse
			cluster = self._get_cluster_for_field(field_name)
			if not cluster:
				frappe.log_error(f"Cannot find cluster for field {field_name}", "Equipment Transfer Ticket")
				return None
			
			# Get warehouses linked to cluster via Geo Fencing Area Warehouse
			cluster_warehouses = frappe.get_all(
				"Geo Fencing Area Warehouse",
				fields=["warehouse"],
				filters={"parent": cluster, "parenttype": "Geo Fencing Area"},
				limit=1
			)
			
			if cluster_warehouses and cluster_warehouses[0].warehouse:
				raw = cluster_warehouses[0].warehouse
				return (get_ledger_warehouse(raw) or raw) if get_ledger_warehouse else raw
			
			return None
		except Exception as e:
			frappe.log_error(f"Error getting cluster warehouse for field {field_name}: {str(e)}", "Equipment Transfer Ticket")
			return None

	def _group_assets_by_source_warehouse(self, equipment_assets: List[str]) -> Dict[str, List[str]]:
		"""Group assets by their actual current warehouse location.
		
		Determines source warehouse by checking asset's current location,
		then mapping location to warehouse. Supports:
		- Cluster → Field (first transfer of day)
		- Field → Field (in-between transfers)
		
		Warehouse hierarchy: Farm -> Cluster -> Field
		Falls back to cluster warehouse if location cannot be determined.
		"""
		assets_by_warehouse: Dict[str, List[str]] = {}
		
		if not self.field:
			frappe.log_error(f"Activity {self.name} has no field specified for grouping assets", "Equipment Transfer Ticket")
			return assets_by_warehouse
		
		# Get cluster warehouse as fallback
		cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
		if not cluster_warehouse:
			frappe.log_error(f"Cannot find cluster warehouse for field {self.field} in activity {self.name}", "Equipment Transfer Ticket")
			return assets_by_warehouse
		
		# Group assets by their actual current warehouse location
		for asset in equipment_assets:
			try:
				# Get asset's current location
				asset_doc = frappe.get_doc("Asset", asset)
				current_location = asset_doc.location if asset_doc else None
				
				# Map location to warehouse
				source_warehouse = None
				if current_location:
					source_warehouse = self._get_warehouse_from_location(current_location)
				
				# Fallback to cluster if location mapping fails
				if not source_warehouse:
					source_warehouse = cluster_warehouse
					frappe.log_error(f"Could not determine warehouse for asset {asset} location {current_location}, using cluster warehouse {cluster_warehouse}", "Equipment Transfer Ticket")
				
				# Group by source warehouse
				if source_warehouse not in assets_by_warehouse:
					assets_by_warehouse[source_warehouse] = []
				assets_by_warehouse[source_warehouse].append(asset)
			except Exception as e:
				# If asset lookup fails, fallback to cluster warehouse
				frappe.log_error(f"Error getting location for asset {asset}: {str(e)}, using cluster warehouse {cluster_warehouse}", "Equipment Transfer Ticket")
				if cluster_warehouse not in assets_by_warehouse:
					assets_by_warehouse[cluster_warehouse] = []
				assets_by_warehouse[cluster_warehouse].append(asset)
		
		return assets_by_warehouse

	def _ltt_planned_window_overlaps_ticket(self, ticket_doc) -> bool:
		"""True if this activity's planned window overlaps the ticket's planned pickup/drop window.

		Without this, an open LTT for the same tractor and from→to blocks new tickets for other dates.
		"""
		ps = get_datetime(self.planned_start) if self.get("planned_start") else None
		pe = get_datetime(self.planned_end) if self.get("planned_end") else None
		if not ps and not pe:
			return True

		if ps and pe and ps > pe:
			ps, pe = pe, ps
		if ps and not pe:
			pe = ps
		elif pe and not ps:
			ps = pe

		tpu = get_datetime(ticket_doc.planned_pickup_on) if getattr(ticket_doc, "planned_pickup_on", None) else None
		tpo = get_datetime(ticket_doc.planned_drop_off_on) if getattr(ticket_doc, "planned_drop_off_on", None) else None
		if not tpu and not tpo:
			t_anchor = get_datetime(ticket_doc.creation)
			tpu = tpo = t_anchor

		if tpu and tpo and tpu > tpo:
			tpu, tpo = tpo, tpu
		if tpu and not tpo:
			tpo = tpu
		elif tpo and not tpu:
			tpu = tpo

		return ps <= tpo and pe >= tpu

	def _ltt_open_ticket_matches_schedule_duplicate(self, ticket_doc, schedule_ref: str | None) -> bool:
		"""Duplicate only for the same activity row (schedule_ref) plus same route/items.

		Legacy tickets without schedule_ref still dedupe when planned windows overlap (backward compatible).
		"""
		ref = (schedule_ref or "").strip()
		t_ref = (getattr(ticket_doc, "schedule_ref", None) or "").strip()
		if ref:
			if t_ref == ref:
				return True
			if not t_ref and self._ltt_planned_window_overlaps_ticket(ticket_doc):
				return True
			return False
		return self._ltt_planned_window_overlaps_ticket(ticket_doc)

	def _find_open_equipment_ltt_duplicate(
		self, from_warehouse: str, to_warehouse: str, asset_list: List[str], schedule_ref: str | None = None
	) -> str | None:
		"""Return name of an open LTT with same from→to where ticket already covers this leg's assets.

		Uses subset match: scheduled assets may be a subset of ticket rows because
		create_logistics_transfer_ticket appends co-moving implement assets (superset on LTT).
		When schedule_ref is set (On Demand Activity / Crop Plan Schedule name), only the same row
		counts as duplicate—different activities or dates keep separate LTTs.
		"""
		asset_set = {a for a in (asset_list or []) if a}
		if not asset_set:
			return None
		ticket_names = frappe.get_all(
			"Logistics Transfer Ticket",
			filters={
				"from_warehouse": from_warehouse,
				"to_warehouse": to_warehouse,
				"status": ["not in", ["Received", "Cancelled"]],
			},
			pluck="name",
			limit_page_length=200,
		)
		for ticket_name in ticket_names:
			try:
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
				if not ticket_doc.asset_items or len(ticket_doc.asset_items) == 0:
					continue
				ticket_assets = {ai.asset for ai in ticket_doc.asset_items if ai.asset}
				if asset_set <= ticket_assets and self._ltt_open_ticket_matches_schedule_duplicate(
					ticket_doc, schedule_ref
				):
					return ticket_name
			except Exception:
				continue
		return None

	def _find_open_input_only_ltt_duplicate(
		self, from_warehouse: str, to_warehouse: str, input_items: List[Dict[str, Any]], schedule_ref: str | None = None
	) -> str | None:
		"""Return name of an open input-only LTT with same from→to and identical stock lines."""
		input_items_set = {
			(str(it.get("item_code")).strip(), flt(it.get("qty")))
			for it in input_items
			if it.get("item_code")
		}
		if not input_items_set:
			return None
		ticket_names = frappe.get_all(
			"Logistics Transfer Ticket",
			filters={
				"from_warehouse": from_warehouse,
				"to_warehouse": to_warehouse,
				"status": ["not in", ["Received", "Cancelled"]],
			},
			pluck="name",
			limit_page_length=200,
		)
		for ticket_name in ticket_names:
			try:
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
				if ticket_doc.asset_items and len(ticket_doc.asset_items) > 0:
					continue
				if not ticket_doc.stock_items:
					continue
				ticket_items = {
					(str(si.item_code).strip(), flt(si.qty)) for si in ticket_doc.stock_items if si.item_code
				}
				if ticket_items == input_items_set and self._ltt_open_ticket_matches_schedule_duplicate(
					ticket_doc, schedule_ref
				):
					return ticket_name
			except Exception:
				continue
		return None

	def _find_open_vehicle_consumables_ltt_duplicate(
		self,
		from_warehouse: str,
		to_warehouse: str,
		tool_asset_list: list[str],
		stock_items: list[dict] | None,
		schedule_ref: str | None = None,
	) -> str | None:
		"""Open vehicle/consumables LTT: same stock lines and tool assets are subset of ticket."""
		stock_set = {
			(str(it.get("item_code")).strip(), flt(it.get("qty")))
			for it in (stock_items or []) if it.get("item_code")
		}
		tool_set = {a for a in (tool_asset_list or []) if a}
		ticket_names = frappe.get_all(
			"Logistics Transfer Ticket",
			filters={"from_warehouse": from_warehouse, "to_warehouse": to_warehouse, "status": ["not in", ["Received", "Cancelled"]]},
			pluck="name", limit_page_length=200,
		)
		for ticket_name in ticket_names:
			try:
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
				t_stock = {(str(si.item_code).strip(), flt(si.qty)) for si in (ticket_doc.stock_items or []) if si.item_code}
				if t_stock != stock_set:
					continue
				t_assets = {ai.asset for ai in (ticket_doc.asset_items or []) if ai.asset}
				if tool_set:
					if not (tool_set <= t_assets):
						continue
				elif t_assets:
					continue
				if not self._ltt_open_ticket_matches_schedule_duplicate(ticket_doc, schedule_ref):
					continue
				return ticket_name
			except Exception:
				continue
		return None

	def _sync_open_forward_ltts_planned_times(self):
		"""Recompute planned pickup/drop on open forward LTTs when planned_start changes."""
		if self.status != "Scheduled" or not self.planned_start or not self.field:
			return
		from f2c.inventory.logistics_transfer_ticket_api import (
			planned_pickup_drop_for_activity_start,
			schedule_ltt_planned_times_enabled,
			update_ltt_planned_times_if_pending_pickup,
		)
		if not schedule_ltt_planned_times_enabled():
			return
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if not target_warehouse:
			return
		for asset_reqs, _tv in self._machinery_transfer_unit_payloads():
			primary = asset_reqs[0].get("asset") if asset_reqs else None
			if not primary:
				continue
			from_warehouse = self._get_source_warehouse_for_equipment_asset(primary)
			if not from_warehouse or from_warehouse == target_warehouse:
				continue
			asset_names = [r.get("asset") for r in asset_reqs if r.get("asset")]
			tid = self._find_open_equipment_ltt_duplicate(
				from_warehouse, target_warehouse, asset_names, self.name
			)
			if not tid:
				continue
			pt = planned_pickup_drop_for_activity_start(self.planned_start, from_warehouse, target_warehouse)
			if pt:
				update_ltt_planned_times_if_pending_pickup(tid, pt[0], pt[1])
		input_items = self._collect_input_items()
		tool_assets = self._collect_hand_and_other_tool_assets()
		input_src = self._get_source_warehouse_for_inputs() if input_items else None
		tool_by_wh = self._group_assets_by_source_warehouse(tool_assets) if tool_assets else {}
		vehicle_from_wh: set[str] = set(tool_by_wh.keys())
		if input_items and input_src:
			vehicle_from_wh.add(input_src)
		for from_warehouse in vehicle_from_wh:
			tools = tool_by_wh.get(from_warehouse, [])
			stocks = input_items if (input_items and input_src == from_warehouse) else None
			if not stocks and not tools:
				continue
			dup_v = self._find_open_vehicle_consumables_ltt_duplicate(
				from_warehouse, target_warehouse, tools, stocks, self.name
			)
			if dup_v:
				pt = planned_pickup_drop_for_activity_start(
					self.planned_start, from_warehouse, target_warehouse
				)
				if pt:
					update_ltt_planned_times_if_pending_pickup(dup_v, pt[0], pt[1])

	def _sync_open_return_ltts_planned_times(self):
		"""Recompute planned pickup/drop on open return LTTs (field→cluster) when planned_end changes."""
		if not self.field or not self.planned_end:
			return
		from f2c.inventory.logistics_transfer_ticket_api import (
			planned_pickup_drop_for_activity_start,
			schedule_ltt_planned_times_enabled,
			update_ltt_planned_times_if_pending_pickup,
		)
		if not schedule_ltt_planned_times_enabled():
			return
		field_warehouse = self._get_target_warehouse_for_field(self.field)
		cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
		if not field_warehouse or not cluster_warehouse or field_warehouse == cluster_warehouse:
			return
		pt = planned_pickup_drop_for_activity_start(self.planned_end, field_warehouse, cluster_warehouse)
		if not pt:
			return
		for asset_reqs, _tv in self._machinery_transfer_unit_payloads():
			primary = asset_reqs[0].get("asset") if asset_reqs else None
			if not primary:
				continue
			asset_names = [r.get("asset") for r in asset_reqs if r.get("asset")]
			tid_m = self._find_open_equipment_ltt_duplicate(
				field_warehouse, cluster_warehouse, asset_names, self.name
			)
			if tid_m:
				update_ltt_planned_times_if_pending_pickup(tid_m, pt[0], pt[1])
		hand_other = self._collect_hand_and_other_tool_assets()
		if hand_other:
			tid_v = self._find_open_equipment_ltt_duplicate(
				field_warehouse, cluster_warehouse, hand_other, self.name
			)
			if tid_v:
				update_ltt_planned_times_if_pending_pickup(tid_v, pt[0], pt[1])

	def _warn_if_forward_ltts_will_use_creation_planned_times(self):
		"""When schedule-based LTT times are on but Planned Start is missing, tickets still create using creation as planned anchor."""
		if getattr(self, "_ltt_forward_planned_warn_shown", False):
			return
		from f2c.inventory.logistics_transfer_ticket_api import schedule_ltt_planned_times_enabled

		if not schedule_ltt_planned_times_enabled():
			return
		if self.planned_start:
			return
		if self.status != "Scheduled" or not self.field:
			return
		if not (
			set(self._collect_equipment_assets())
			or self._collect_input_items()
			or self._collect_hand_and_other_tool_assets()
		):
			return
		frappe.msgprint(
			"F2C Settings have schedule-based LTT planned times enabled, but this activity has no Planned Start yet. "
			"Forward transfer tickets will use ticket creation time for planned pickup and drop-off until Planned Start is set; "
			"save again after setting it to refresh open tickets that are still Pending Pickup.",
			title="LTT planned times",
			indicator="orange",
		)
		self._ltt_forward_planned_warn_shown = True

	def _warn_if_return_ltts_will_use_creation_planned_times(self):
		"""When schedule-based LTT times are on but Planned End is missing, return LTTs use creation as planned anchor."""
		if getattr(self, "_ltt_return_planned_warn_shown", False):
			return
		from f2c.inventory.logistics_transfer_ticket_api import schedule_ltt_planned_times_enabled

		if not schedule_ltt_planned_times_enabled():
			return
		if self.planned_end:
			return
		frappe.msgprint(
			"Schedule-based LTT planned times are enabled, but Planned End is not set. "
			"Return transfer tickets will use ticket creation time for planned pickup and drop-off until Planned End is set.",
			title="LTT planned times",
			indicator="orange",
		)
		self._ltt_return_planned_warn_shown = True

	def _create_equipment_transfer_tickets(self):
		"""Create forward LTTs: vehicle (inputs + hand/other tools) then machinery (per-unit)."""
		self._warn_if_forward_ltts_will_use_creation_planned_times()
		self._inputs_included_in_vehicle_tickets = False
		self._inputs_included_in_equipment_tickets = False
		self._create_vehicle_consumables_transfer_tickets()
		self._create_machinery_transfer_tickets()

	def _create_vehicle_consumables_transfer_tickets(self):
		"""LTT for approved inputs and hand/other tool assets; uses Transport Vehicle (Machinery type Vehicle)."""
		if self.status != "Scheduled":
			return
		if not self.field:
			return
		input_items = self._collect_input_items()
		tool_assets = self._collect_hand_and_other_tool_assets()
		if not input_items and not tool_assets:
			return
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if not target_warehouse:
			frappe.log_error(
				title="On Demand Activity LTT",
				message=f"Activity {self.name}: vehicle LTT skipped — no target warehouse for field {self.field}.",
			)
			return
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		try:
			target_location_result = get_location_for_warehouse(target_warehouse)
			if not target_location_result or not target_location_result.get("location"):
				frappe.log_error(
					title="On Demand Activity LTT",
					message=(
						f"Activity {self.name}: vehicle LTT skipped — field warehouse {target_warehouse} has no ERPNext Location "
						"(run Geo Warehouses → Location sync)."
					),
				)
				frappe.msgprint(
					"Field warehouse has no mapped Location, so logistics transfer tickets cannot be created. "
					"Run Location sync from Geo Warehouses. Details were written to Error Log (On Demand Activity LTT).",
					indicator="orange",
					title="Transfer ticket skipped",
				)
				return
		except Exception as ex:
			frappe.log_error(
				title="On Demand Activity LTT",
				message=(
					f"Activity {self.name}: vehicle LTT skipped — get_location_for_warehouse failed for {target_warehouse}: {ex!s}"
				),
			)
			frappe.msgprint(
				"Could not resolve warehouse location for logistics. Check Error Log (On Demand Activity LTT).",
				indicator="orange",
				title="Transfer ticket skipped",
			)
			return

		input_src = self._get_source_warehouse_for_inputs() if input_items else None
		if input_items and not input_src:
			frappe.msgprint(
				f"Cannot find source warehouse for input items in activity {self.name}.",
				indicator="orange", title="Vehicle Transfer Ticket Creation Failed",
			)
			return

		tool_by_wh = self._group_assets_by_source_warehouse(tool_assets) if tool_assets else {}
		from_warehouses: set[str] = set(tool_by_wh.keys())
		if input_items and input_src:
			from_warehouses.add(input_src)

		from f2c.inventory.logistics_transfer_ticket_api import (
			create_logistics_transfer_ticket,
			planned_pickup_drop_for_activity_start,
		)

		inputs_included = False
		created: list[str] = []
		errors: list[str] = []
		tv = (getattr(self, "transport_vehicle", None) or "").strip()

		for from_warehouse in sorted(from_warehouses):
			tools_here = tool_by_wh.get(from_warehouse, [])
			stock_here = input_items if (input_items and input_src == from_warehouse) else None
			if not stock_here and not tools_here:
				continue
			if from_warehouse == target_warehouse and tools_here:
				continue
			if from_warehouse == target_warehouse and not tools_here and stock_here:
				pass
			elif from_warehouse == target_warehouse:
				continue

			dup = self._find_open_vehicle_consumables_ltt_duplicate(
				from_warehouse, target_warehouse, tools_here, stock_here, self.name
			)
			if dup:
				continue

			planned_times = planned_pickup_drop_for_activity_start(
				self.planned_start, from_warehouse, target_warehouse
			)
			planned_kwargs = {}
			if planned_times:
				planned_kwargs["planned_pickup_on"] = planned_times[0]
				planned_kwargs["planned_drop_off_on"] = planned_times[1]
			try:
				result = create_logistics_transfer_ticket(
					from_warehouse=from_warehouse,
					to_warehouse=target_warehouse,
					stock_items=stock_here,
					assets=tools_here or None,
					transport_vehicle=tv or None,
					skip_default_transport_vehicle=True,
					schedule_ref=self.name,
					**planned_kwargs,
				)
				if result and result.get("ticket"):
					created.append(result.get("ticket"))
					if stock_here:
						inputs_included = True
			except Exception as e:
				frappe.log_error(
					title="Vehicle Transfer Ticket",
					message=f"Vehicle transfer ticket error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
				)
				errors.append(str(e))

		self._inputs_included_in_vehicle_tickets = inputs_included
		self._inputs_included_in_equipment_tickets = inputs_included
		if created:
			frappe.msgprint(
				f"Created {len(created)} vehicle/consumables transfer ticket(s): {', '.join(created)}",
				indicator="green", title="Transfer Tickets Created",
			)

	def _create_machinery_transfer_tickets(self):
		"""LTT per machinery unit (no approved inputs on this ticket)."""
		if self.status != "Scheduled" or not self.field:
			return
		unit_payloads = self._machinery_transfer_unit_payloads()
		if not unit_payloads:
			return
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if not target_warehouse:
			frappe.log_error(
				title="On Demand Activity LTT",
				message=f"Activity {self.name}: machinery LTT skipped — no target warehouse for field {self.field}.",
			)
			return
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		try:
			target_location_result = get_location_for_warehouse(target_warehouse)
			if not target_location_result or not target_location_result.get("location"):
				frappe.log_error(
					title="On Demand Activity LTT",
					message=(
						f"Activity {self.name}: machinery LTT skipped — field warehouse {target_warehouse} has no ERPNext Location."
					),
				)
				frappe.msgprint(
					"Field warehouse has no mapped Location, so machinery transfer tickets cannot be created. "
					"Run Location sync from Geo Warehouses. Details were written to Error Log (On Demand Activity LTT).",
					indicator="orange",
					title="Transfer ticket skipped",
				)
				return
		except Exception as ex:
			frappe.log_error(
				title="On Demand Activity LTT",
				message=(
					f"Activity {self.name}: machinery LTT skipped — location lookup failed for {target_warehouse}: {ex!s}"
				),
			)
			frappe.msgprint(
				"Could not resolve warehouse location for machinery logistics. Check Error Log (On Demand Activity LTT).",
				indicator="orange",
				title="Transfer ticket skipped",
			)
			return

		from f2c.inventory.logistics_transfer_ticket_api import (
			create_logistics_transfer_ticket,
			planned_pickup_drop_for_activity_start,
			planned_pickup_drop_for_round_trip_leg1_immediate,
			planned_pickup_drop_for_round_trip_leg2_from_schedule,
		)
		from f2c.inventory.tractor_implement_ltt_plan import (
			implement_asset_ids_paired_to_tractors_on_schedule,
			plan_field_tractor_implement_round_trip,
		)

		created_tickets: list[str] = []
		errors: list[str] = []
		machinery_skips: list[str] = []
		cluster_wh = self._get_cluster_warehouse_for_field(self.field)
		skip_standalone_impl_assets: set[str] = set(implement_asset_ids_paired_to_tractors_on_schedule(self))

		for asset_reqs, transport_machinery in unit_payloads:
			primary = asset_reqs[0].get("asset") if asset_reqs else None
			if not primary:
				continue
			if primary in skip_standalone_impl_assets:
				continue
			from_warehouse = self._get_source_warehouse_for_equipment_asset(primary)
			if not from_warehouse:
				machinery_skips.append(f"{primary}: no source warehouse")
				continue

			rt = plan_field_tractor_implement_round_trip(self, primary) if cluster_wh else None
			if rt:
				if rt.standalone_implement_asset_to_skip:
					skip_standalone_impl_assets.add(rt.standalone_implement_asset_to_skip)
				tv = rt.transport_vehicle or transport_machinery
				leg1_names = [r.get("asset") for r in rt.leg1_assets if r.get("asset")]
				leg2_names = [r.get("asset") for r in rt.leg2_assets if r.get("asset")]
				leg1_from = target_warehouse if from_warehouse == cluster_wh else from_warehouse
				if leg1_from != cluster_wh:
					dup1 = self._find_open_equipment_ltt_duplicate(
						leg1_from, cluster_wh, leg1_names, self.name
					)
					if dup1:
						machinery_skips.append(f"{primary}: round-trip leg1 open ticket {dup1}")
					else:
						pt1 = planned_pickup_drop_for_round_trip_leg1_immediate(leg1_from, cluster_wh)
						kw1: dict = {}
						if pt1:
							kw1["planned_pickup_on"] = pt1[0]
							kw1["planned_drop_off_on"] = pt1[1]
						try:
							r1 = create_logistics_transfer_ticket(
								from_warehouse=leg1_from,
								to_warehouse=cluster_wh,
								stock_items=None,
								assets=rt.leg1_assets,
								transport_vehicle=tv,
								skip_default_transport_vehicle=True,
								schedule_ref=self.name,
								**kw1,
							)
							if r1 and r1.get("ticket"):
								created_tickets.append(r1.get("ticket"))
						except Exception as e:
							frappe.log_error(
								title="Equipment Transfer Ticket",
								message=f"Machinery round-trip leg1 error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
							)
							errors.append(str(e))
				dup2 = self._find_open_equipment_ltt_duplicate(
					cluster_wh, target_warehouse, leg2_names, self.name
				)
				if dup2:
					machinery_skips.append(f"{primary}: round-trip leg2 open ticket {dup2}")
				else:
					pt2 = planned_pickup_drop_for_round_trip_leg2_from_schedule(
						self.planned_start, cluster_wh, target_warehouse
					)
					kw2: dict = {}
					if pt2:
						kw2["planned_pickup_on"] = pt2[0]
						kw2["planned_drop_off_on"] = pt2[1]
					try:
						r2 = create_logistics_transfer_ticket(
							from_warehouse=cluster_wh,
							to_warehouse=target_warehouse,
							stock_items=None,
							assets=rt.leg2_assets,
							transport_vehicle=tv,
							skip_default_transport_vehicle=True,
							schedule_ref=self.name,
							**kw2,
						)
						if r2 and r2.get("ticket"):
							created_tickets.append(r2.get("ticket"))
					except Exception as e:
						frappe.log_error(
							title="Equipment Transfer Ticket",
							message=f"Machinery round-trip leg2 error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
						)
						errors.append(str(e))
				continue

			asset_names = [r.get("asset") for r in asset_reqs if r.get("asset")]
			if from_warehouse == target_warehouse:
				machinery_skips.append(f"{primary}: already at field warehouse {target_warehouse}")
				continue
			dup_ticket = self._find_open_equipment_ltt_duplicate(
				from_warehouse, target_warehouse, asset_names, self.name
			)
			if dup_ticket:
				machinery_skips.append(f"{primary}: open ticket {dup_ticket}")
				continue
			planned_times = planned_pickup_drop_for_activity_start(
				self.planned_start, from_warehouse, target_warehouse
			)
			planned_kwargs = {}
			if planned_times:
				planned_kwargs["planned_pickup_on"] = planned_times[0]
				planned_kwargs["planned_drop_off_on"] = planned_times[1]
			try:
				result = create_logistics_transfer_ticket(
					from_warehouse=from_warehouse,
					to_warehouse=target_warehouse,
					stock_items=None,
					assets=asset_reqs,
					transport_vehicle=transport_machinery,
					skip_default_transport_vehicle=True,
					schedule_ref=self.name,
					**planned_kwargs,
				)
				if result and result.get("ticket"):
					created_tickets.append(result.get("ticket"))
			except Exception as e:
				frappe.log_error(
					title="Equipment Transfer Ticket",
					message=f"Machinery transfer ticket error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
				)
				errors.append(str(e))

		if created_tickets:
			frappe.msgprint(
				f"Created {len(created_tickets)} machinery transfer ticket(s): {', '.join(created_tickets)}",
				indicator="green", title="Transfer Tickets Created",
			)
		elif errors:
			frappe.msgprint(
				"Failed to create some machinery transfer tickets. Please check Error Log.",
				indicator="red", title="Transfer Ticket Creation Failed",
			)
		elif unit_payloads and machinery_skips:
			frappe.log_error(
				title="On Demand Activity LTT",
				message=(
					f"Activity {self.name}: no new machinery LTT. target_wh={target_warehouse}, "
					f"planned_start={self.planned_start!s}. Skips: {' | '.join(machinery_skips)}"
				),
			)

	def _collect_input_items(self) -> List[Dict[str, Any]]:
		"""Helper to collect input items from inputs table.
		Uses total_quantity_to_use when > 0; for non-spray or legacy rows, falls back to rate*acres for Bags/Acre or rate_quantity."""
		input_items = []
		for inp in self.get("inputs") or []:
			if not inp.item:
				continue
			qty = flt(inp.total_quantity_to_use, 3)
			if qty <= 0 and flt(inp.rate_quantity) > 0:
				unit = (inp.unit or "").strip().lower()
				if unit == "bags/acre":
					qty = flt(inp.rate_quantity, 3) * flt(self.total_acres, 3)
				else:
					qty = flt(inp.rate_quantity, 3)
			if qty > 0:
				input_items.append({"item_code": inp.item, "qty": qty})
		return input_items

	def _get_source_warehouse_for_inputs(self) -> str | None:
		"""Get source warehouse for input items. Chooses cluster ledger warehouse where items have stock (by location); else first cluster ledger or company default."""
		if self.field:
			try:
				from f2c.inventory.warehouse_utils import (
					get_ledger_warehouses_for_areas,
					get_source_warehouse_by_item_location,
				)
			except Exception:
				pass
			else:
				cluster = self._get_cluster_for_field(self.field)
				if cluster:
					ledger_list = get_ledger_warehouses_for_areas([cluster])
					if ledger_list:
						items = self._collect_input_items()
						best = get_source_warehouse_by_item_location(items, ledger_list)
						if best:
							return best
						# No stock in any cluster ledger: use first ledger as fallback
						return ledger_list[0]
			# Fallback: cluster ledger warehouse (single) or company default
			cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
			if cluster_warehouse:
				return cluster_warehouse
		
		# Fallback to company default warehouse (for backward compatibility)
		# Get company from target warehouse (if we have it)
		company = None
		target_warehouse = self._get_target_warehouse_for_field(self.field) if self.field else None
		if target_warehouse:
			try:
				company = frappe.db.get_value("Warehouse", target_warehouse, "company")
			except Exception:
				pass
		
		if not company:
			frappe.log_error(f"Cannot determine company for activity {self.name}", "Input Transfer Ticket")
			return None
		
		try:
			# Try to get company's default warehouse
			default_warehouse = frappe.db.get_value("Company", company, "default_warehouse")
			if default_warehouse:
				return default_warehouse
			
			# Fallback: get first warehouse for the company
			warehouses = frappe.get_all(
				"Warehouse",
				filters={"company": company},
				fields=["name"],
				limit=1
			)
			if warehouses:
				return warehouses[0].name
		except Exception as e:
			frappe.log_error(f"Error getting source warehouse for inputs: {str(e)}", "Input Transfer Ticket")
		
		return None

	def _create_input_transfer_tickets(self):
		"""Create Logistics Transfer Tickets for input items when activity is saved with status Scheduled.
		Note: If inputs were already included in equipment transfer tickets, this will skip creating a separate ticket.
		This prevents duplicate inputs when equipment and inputs come from different source warehouses."""
		if self.status != "Scheduled":
			frappe.log_error(f"Activity {self.name} status is not 'Scheduled' (current: {self.status}), skipping input ticket creation", "Input Transfer Ticket")
			return
		
		if not self.field:
			frappe.log_error(f"Activity {self.name} has no field specified, skipping input ticket creation", "Input Transfer Ticket")
			return  # No field specified

		# When schedule-based planned times are on but planned_start is missing, still create LTTs (creation-time planned fields).

		# If inputs were already included in equipment tickets in this same save, skip (avoids duplicate LTT)
		if getattr(self, "_inputs_included_in_equipment_tickets", False):
			return
		
		# Check if inputs were already included in equipment tickets (DB lookup for tickets created earlier)
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if target_warehouse:
			from frappe.utils import add_to_date, now_datetime
			recent_time = add_to_date(now_datetime(), minutes=-5)
			
			# Find recent tickets to this target warehouse with assets (equipment tickets)
			existing_equipment_tickets = frappe.get_all(
				"Logistics Transfer Ticket",
				filters={
					"to_warehouse": target_warehouse,
					"status": ["!=", "Cancelled"],
					"creation": [">=", recent_time]
				},
				fields=["name"],
				limit=10
			)
			
			# Check if any ticket has both assets AND stock items (equipment + inputs ticket)
			for ticket_name in [t.name for t in existing_equipment_tickets]:
				try:
					ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
					# If ticket has assets and stock items, inputs were already included with equipment
					if ticket_doc.asset_items and len(ticket_doc.asset_items) > 0:
						if ticket_doc.stock_items and len(ticket_doc.stock_items) > 0:
							frappe.log_error(f"Input items for activity {self.name} were already included in equipment transfer ticket {ticket_name}, skipping separate input ticket to prevent duplicates", "Input Transfer Ticket")
							return
				except Exception:
					continue
		
		# Collect input items
		input_items = self._collect_input_items()
		if not input_items:
			frappe.log_error(f"Activity {self.name} has no input items to transfer", "Input Transfer Ticket")
			return  # No input items to transfer
		
		# Log summary only (not full list to avoid exceeding 140 char limit)
		item_codes = [item.get("item_code", "") for item in input_items[:3]]  # First 3 items only
		item_summary = ", ".join(item_codes)
		if len(input_items) > 3:
			item_summary += f" (+{len(input_items) - 3} more)"
		frappe.log_error(f"Creating input transfer tickets for activity {self.name} with {len(input_items)} items: {item_summary}", "Input Transfer Ticket")
		
		# Get source warehouse
		source_warehouse = self._get_source_warehouse_for_inputs()
		if not source_warehouse:
			error_msg = f"Cannot find source warehouse (cluster warehouse) for input items in activity {self.name}. Please ensure field has a cluster warehouse configured or company has a default warehouse."
			frappe.log_error(error_msg, "Input Transfer Ticket")
			frappe.msgprint(error_msg, indicator="orange", title="Input Transfer Ticket Creation Failed")
			return
		
		# Get target warehouse from field
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if not target_warehouse:
			error_msg = f"Cannot find target warehouse for field {self.field} in activity {self.name}"
			frappe.log_error(error_msg, "Input Transfer Ticket")
			frappe.msgprint(error_msg, indicator="orange", title="Input Transfer Ticket Creation Failed")
			return
		
		# Always create pickable/receivable entries for approved inputs; do not skip when items are at field (source==target handled by API).
		
		# Check quantity sufficiency at source; create Material Request for shortfalls
		try:
			from erpnext.stock.utils import get_stock_balance
			from erpnext.stock.stock_ledger import is_negative_stock_allowed
			shortfall_items = []
			for item in input_items:
				item_code = item.get("item_code")
				required = flt(item.get("qty"), 3)
				if not item_code or required <= 0:
					continue
				is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")
				if not is_stock_item:
					continue
				allow_negative = is_negative_stock_allowed(item_code=item_code)
				if allow_negative:
					continue
				available = flt(get_stock_balance(item_code, source_warehouse), 3)
				if available is None:
					available = 0
				if available < required:
					shortfall_items.append({
						"item_code": item_code,
						"qty": flt(required - available, 3),
					})
			if shortfall_items:
				company = frappe.db.get_value("Warehouse", source_warehouse, "company")
				if not company:
					company = frappe.db.get_value("Warehouse", target_warehouse, "company")
				from f2c.inventory.material_request_api import create_material_request
				create_material_request(
					warehouse=source_warehouse,
					items=shortfall_items,
					material_request_type="Material Transfer",
					company=company,
					notes=f"Shortfall for activity {self.name}. Request transfer to cluster/source.",
				)
				frappe.msgprint(
					f"Created Material Request for {len(shortfall_items)} item(s) with insufficient stock at source.",
					indicator="orange",
					title="Shortfall",
				)
		except Exception as e:
			frappe.log_error(f"Shortfall check/MR for activity {self.name}: {str(e)}", "Input Transfer Ticket")
		
		dup_in = self._find_open_input_only_ltt_duplicate(
			source_warehouse, target_warehouse, input_items, self.name
		)
		if dup_in:
			frappe.log_error(
				f"Duplicate input transfer ticket already exists for activity {self.name}: {dup_in} (same items and warehouses), skipping",
				"Input Transfer Ticket",
			)
			return

		self._warn_if_forward_ltts_will_use_creation_planned_times()

		# Create transfer ticket for input items
		from f2c.inventory.logistics_transfer_ticket_api import (
			create_logistics_transfer_ticket,
			planned_pickup_drop_for_activity_start,
		)
		planned_times = planned_pickup_drop_for_activity_start(
			self.planned_start, source_warehouse, target_warehouse
		)
		planned_kwargs = {}
		if planned_times:
			planned_kwargs["planned_pickup_on"] = planned_times[0]
			planned_kwargs["planned_drop_off_on"] = planned_times[1]
		try:
			result = create_logistics_transfer_ticket(
				from_warehouse=source_warehouse,
				to_warehouse=target_warehouse,
				stock_items=input_items,
				assets=None,
				schedule_ref=self.name,
				**planned_kwargs,
			)
			if result and result.get("ticket"):
				frappe.msgprint(f"Created input transfer ticket {result.get('ticket')} for {len(input_items)} item(s)", indicator="green", title="Input Transfer Ticket Created")
				frappe.log_error(f"Successfully created input transfer ticket {result.get('ticket')} for items from {source_warehouse} to {target_warehouse}", "Input Transfer Ticket")
		except Exception as e:
			# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
			# Keep message very short to avoid nested error log references causing overflow
			error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
			error_msg = f"Input transfer ticket error for {self.name}: {error_str}"
			frappe.log_error(error_msg, "Input Transfer Ticket")
			frappe.msgprint("Failed to create input transfer ticket. Please check Error Log for details.", indicator="red", title="Input Transfer Ticket Creation Failed")

	def _get_asset_current_warehouse(self, asset: str) -> str | None:
		"""Get warehouse for asset's current location.
		
		Returns the warehouse that maps to the asset's current location,
		or None if location cannot be determined or mapped.
		"""
		try:
			asset_doc = frappe.get_doc("Asset", asset)
			current_location = asset_doc.location if asset_doc else None
			
			if current_location:
				return self._get_warehouse_from_location(current_location)
			
			return None
		except Exception as e:
			frappe.log_error(f"Error getting current warehouse for asset {asset}: {str(e)}", "Asset Location Lookup")
			return None

	def _has_more_activities_for_assets(self, equipment_assets: List[str], after_time: str, exclude_activity: str = None) -> bool:
		"""Check if assets have more scheduled activities on the same day after the given time.
		
		Checks both Crop Plan Schedule and On Demand Activity for overlapping assets
		that are scheduled after the given time on the same day.
		
		Args:
			equipment_assets: List of asset names to check
			after_time: Datetime string - check for activities after this time
			exclude_activity: Activity/Schedule name to exclude from check (current activity)
		
		Returns:
			True if there are more activities scheduled for these assets after the given time
		"""
		if not equipment_assets or not after_time:
			return False
		
		try:
			from frappe.utils import get_datetime
			after_dt = get_datetime(after_time)
			after_date = after_dt.date()
			
			# Build placeholders for SQL query
			placeholders = ', '.join(['%s'] * len(equipment_assets))
			
			# Check Crop Plan Schedule
			cps_query = f"""
				SELECT DISTINCT cps.name, cps.planned_start, cps.planned_end
				FROM `tabCrop Plan Schedule` cps
				INNER JOIN (
					SELECT parent, asset FROM `tabCrop Plan Schedule Machinery` WHERE asset IN ({placeholders})
					UNION ALL
					SELECT parent, asset FROM `tabCrop Plan Schedule Implement` WHERE asset IN ({placeholders})
					UNION ALL
					SELECT parent, asset FROM `tabCrop Plan Schedule Hand Tool` WHERE asset IN ({placeholders})
					UNION ALL
					SELECT parent, asset FROM `tabCrop Plan Schedule Other Tool` WHERE asset IN ({placeholders})
				) eq ON cps.name = eq.parent
				WHERE cps.status IN ('Scheduled', 'Reported')
					AND cps.planned_start IS NOT NULL
					AND DATE(cps.planned_start) = %s
					AND cps.planned_start > %s
			"""
			
			cps_params = equipment_assets * 4 + [after_date, after_time]
			if exclude_activity:
				cps_query += " AND cps.name != %s"
				cps_params.append(exclude_activity)
			
			cps_results = frappe.db.sql(cps_query, cps_params, as_dict=True)
			
			# Check On Demand Activity
			oda_query = f"""
				SELECT DISTINCT oda.name, oda.planned_start, oda.planned_end
				FROM `tabOn Demand Activity` oda
				INNER JOIN (
					SELECT parent, asset FROM `tabOn Demand Activity Machinery` WHERE asset IN ({placeholders})
					UNION ALL
					SELECT parent, asset FROM `tabOn Demand Activity Implement` WHERE asset IN ({placeholders})
					UNION ALL
					SELECT parent, asset FROM `tabOn Demand Activity Hand Tool` WHERE asset IN ({placeholders})
					UNION ALL
					SELECT parent, asset FROM `tabOn Demand Activity Other Tool` WHERE asset IN ({placeholders})
				) eq ON oda.name = eq.parent
				WHERE oda.status IN ('Scheduled', 'Reported')
					AND oda.planned_start IS NOT NULL
					AND DATE(oda.planned_start) = %s
					AND oda.planned_start > %s
			"""
			
			oda_params = equipment_assets * 4 + [after_date, after_time]
			if exclude_activity:
				oda_query += " AND oda.name != %s"
				oda_params.append(exclude_activity)
			
			oda_results = frappe.db.sql(oda_query, oda_params, as_dict=True)
			
			# Return True if any activities found
			return len(cps_results) > 0 or len(oda_results) > 0
			
		except Exception as e:
			frappe.log_error(f"Error checking for more activities for assets {equipment_assets}: {str(e)}", "Activity Check")
			# On error, assume no more activities (safer to return to cluster)
			return False

	def _should_return_to_cluster(self, equipment_assets: List[str], activity_end_time: str) -> bool:
		"""Check if assets should be returned to cluster.
		
		Returns False if there are more scheduled activities for these assets
		on the same day after the current activity ends.
		Otherwise returns True (should return to cluster).
		
		Args:
			equipment_assets: List of asset names
			activity_end_time: End time of current activity (datetime string)
		
		Returns:
			True if assets should be returned to cluster, False if more activities exist
		"""
		if not equipment_assets or not activity_end_time:
			return True  # Default to returning if we can't determine
		
		# Check if there are more activities scheduled after this one
		has_more = self._has_more_activities_for_assets(equipment_assets, activity_end_time, self.name)
		
		# Return to cluster only if no more activities exist
		return not has_more

	def _create_return_transfer_tickets(self):
		"""Create Logistics Transfer Tickets to return equipment from field to cluster when activity is completed.
		
		Warehouse hierarchy: Farm -> Cluster -> Field
		Returns equipment from Field Warehouse (child) back to Cluster Warehouse (parent).
		Only creates return transfer if this is the last activity of the day for these assets.
		"""
		if self.status != "Completed":
			frappe.log_error(f"Activity {self.name} status is not 'Completed' (current: {self.status}), skipping return ticket creation", "Return Transfer Ticket")
			return
		
		if not self.field:
			frappe.log_error(f"Activity {self.name} has no field specified, skipping return ticket creation", "Return Transfer Ticket")
			return

		# When schedule-based planned times are on but planned_end is missing, still create return LTTs (auto planned fields).

		# Get equipment assets
		equipment_assets = self._collect_equipment_assets()
		if not equipment_assets:
			frappe.log_error(f"Activity {self.name} has no equipment assets to return", "Return Transfer Ticket")
			return
		
		# Check if there are more activities scheduled for these assets on the same day
		# Only return to cluster if this is the last activity
		if self.planned_end:
			should_return = self._should_return_to_cluster(equipment_assets, self.planned_end)
			if not should_return:
				frappe.log_error(f"Activity {self.name} has more activities scheduled for assets {equipment_assets} after {self.planned_end}, skipping return transfer", "Return Transfer Ticket")
				return  # More activities exist, skip return transfer
		
		# Get field warehouse (source for return = where equipment currently is)
		field_warehouse = self._get_target_warehouse_for_field(self.field)
		if not field_warehouse:
			error_msg = f"Cannot find field warehouse for field {self.field} in activity {self.name}"
			frappe.log_error(error_msg, "Return Transfer Ticket")
			return
		
		# Get cluster warehouse (destination for return)
		cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
		if not cluster_warehouse:
			error_msg = f"Cannot find cluster warehouse for field {self.field} in activity {self.name}"
			frappe.log_error(error_msg, "Return Transfer Ticket")
			return
		
		if field_warehouse == cluster_warehouse:
			frappe.log_error(f"Field warehouse and cluster warehouse are the same ({field_warehouse}), skipping return transfer", "Return Transfer Ticket")
			return  # Already at cluster
		
		# Check if cluster warehouse has a location (required for asset transfer)
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		try:
			cluster_location_result = get_location_for_warehouse(cluster_warehouse)
			if not cluster_location_result or not cluster_location_result.get("location"):
				error_msg = f"Cluster warehouse {cluster_warehouse} has no mapped location. Please run Location sync (Geo Warehouses → Location) for the cluster area."
				frappe.log_error(error_msg, "Return Transfer Ticket")
				return
		except Exception as e:
			frappe.log_error(f"Error checking location for cluster warehouse {cluster_warehouse}: {str(e)}", "Return Transfer Ticket")
			return

		self._warn_if_return_ltts_will_use_creation_planned_times()

		# Create return transfer ticket
		from f2c.inventory.logistics_transfer_ticket_api import (
			create_logistics_transfer_ticket,
			planned_pickup_drop_for_activity_start,
		)
		planned_times = planned_pickup_drop_for_activity_start(
			self.planned_end, field_warehouse, cluster_warehouse
		)
		planned_kwargs = {}
		if planned_times:
			planned_kwargs["planned_pickup_on"] = planned_times[0]
			planned_kwargs["planned_drop_off_on"] = planned_times[1]
		created_names: list[str] = []
		try:
			for asset_reqs, transport_machinery in self._machinery_transfer_unit_payloads():
				if not asset_reqs:
					continue
				asset_names = [r.get("asset") for r in asset_reqs if r.get("asset")]
				dup_m = self._find_open_equipment_ltt_duplicate(
					field_warehouse, cluster_warehouse, asset_names, self.name
				)
				if dup_m:
					continue
				result_m = create_logistics_transfer_ticket(
					from_warehouse=field_warehouse,
					to_warehouse=cluster_warehouse,
					stock_items=None,
					assets=asset_reqs,
					transport_vehicle=transport_machinery,
					skip_default_transport_vehicle=True,
					schedule_ref=self.name,
					**planned_kwargs,
				)
				if result_m and result_m.get("ticket"):
					created_names.append(result_m.get("ticket"))
			hand_other_assets = self._collect_hand_and_other_tool_assets()
			if hand_other_assets:
				result_v = create_logistics_transfer_ticket(
					from_warehouse=field_warehouse,
					to_warehouse=cluster_warehouse,
					stock_items=None,
					assets=[{"asset": asset, "qty": 1} for asset in hand_other_assets],
					transport_vehicle=(getattr(self, "transport_vehicle", None) or "").strip() or None,
					skip_default_transport_vehicle=True,
					schedule_ref=self.name,
					**planned_kwargs,
				)
				if result_v and result_v.get("ticket"):
					created_names.append(result_v.get("ticket"))
			if created_names:
				frappe.msgprint(
					f"Created return transfer ticket(s): {', '.join(created_names)}",
					indicator="green", title="Return Transfer Ticket Created",
				)
		except Exception as e:
			error_msg = f"Error creating return transfer ticket from {field_warehouse} to {cluster_warehouse} for activity {self.name}: {str(e)}"
			frappe.log_error(error_msg, "Return Transfer Ticket")
			frappe.msgprint("Failed to create return transfer ticket. Please check Error Log for details.", indicator="red", title="Return Transfer Ticket Creation Failed")

	def after_insert(self):
		"""Create transfer tickets when activity is first created with status Scheduled."""
		if self.status == "Scheduled":
			try:
				current_assets = set(self._collect_equipment_assets())
				if current_assets or self._collect_input_items():
					self._create_equipment_transfer_tickets()
				if self._collect_input_items():
					try:
						self._create_input_transfer_tickets()
					except Exception as inp_e:
						frappe.log_error(f"Input ticket error for {self.name}: {str(inp_e)[:60]}", "Input Transfer Ticket")
			except Exception as e:
				# Log error but don't block activity creation
				# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
				# Keep message very short to avoid nested error log references causing overflow
				error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
				error_msg = f"Transfer ticket creation error for {self.name}: {error_str}"
				frappe.log_error(error_msg, "Transfer Ticket")
				# Don't raise - allow activity to be created even if ticket creation fails

	def on_update(self):
		"""Create transfer tickets when activity status changes to Scheduled or equipment is added.
		Create return transfer tickets when status changes to Completed."""
		old_doc = self.get_doc_before_save() if not self.is_new() else None
		old_status = old_doc.get("status") if old_doc else None

		if not self.is_new():
			if (old_status or "") != "Completed" and self.status == "Completed":
				try:
					self._create_return_transfer_tickets()
				except Exception as e:
					error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
					frappe.log_error(f"Return transfer ticket error for {self.name}: {error_str}", "Return Transfer Ticket")
				return
			if (old_status or "") == "Completed" and self.status == "Completed" and old_doc:
				from frappe.utils import get_datetime as _gdts_pe

				def _planned_end_changed(a, b):
					da, db = _gdts_pe(a), _gdts_pe(b)
					if da is None and db is None:
						return False
					return da != db

				if _planned_end_changed(old_doc.get("planned_end"), self.planned_end):
					try:
						self._sync_open_return_ltts_planned_times()
					except Exception as e:
						frappe.log_error(
							f"Return LTT planned time sync for {self.name}: {str(e)[:80]}",
							"Return Transfer Ticket",
						)

		if self.status != "Scheduled":
			return

		if not self.is_new():
			if (old_status or "") != "Scheduled":
				try:
					current_assets = set(self._collect_equipment_assets())
					if current_assets or self._collect_input_items():
						self._create_equipment_transfer_tickets()
					if self._collect_input_items():
						try:
							self._create_input_transfer_tickets()
						except Exception as inp_e:
							frappe.log_error(f"Input ticket error for {self.name}: {str(inp_e)[:60]}", "Input Transfer Ticket")
				except Exception as e:
					error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
					frappe.log_error(f"Transfer ticket error for {self.name}: {error_str}", "Transfer Ticket")
			else:
				current_assets = set(self._collect_equipment_assets())
				if current_assets or self._collect_input_items():
					try:
						self._create_equipment_transfer_tickets()
					except Exception as e:
						error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
						frappe.log_error(f"Equipment transfer ticket error for {self.name}: {error_str}", "Equipment Transfer Ticket")
				if self._collect_input_items():
					try:
						self._create_input_transfer_tickets()
					except Exception as e:
						error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
						frappe.log_error(f"Input transfer ticket error for {self.name}: {error_str}", "Input Transfer Ticket")

		if not self.is_new() and old_doc:
			from frappe.utils import get_datetime as _gdts_sync

			def _schedule_time_changed(a, b):
				da, db = _gdts_sync(a), _gdts_sync(b)
				if da is None and db is None:
					return False
				return da != db

			try:
				if _schedule_time_changed(old_doc.get("planned_start"), self.planned_start):
					self._sync_open_forward_ltts_planned_times()
				if _schedule_time_changed(old_doc.get("planned_end"), self.planned_end):
					self._sync_open_return_ltts_planned_times()
			except Exception as e:
				frappe.log_error(
					f"LTT planned time sync for {self.name}: {str(e)[:80]}",
					"Transfer Ticket",
				)


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
def get_block_details(block: str, field: str = None) -> Dict[str, Any]:
	"""
	Return block_name, block_area_acres and no_of_seedlings for a given block.
	If field is provided, tries to fetch no_of_seedlings from Crop Plan that contains this block.
	"""
	if not block:
		return {}
	
	block_doc = frappe.get_doc("Geo Fencing Area", block)
	area_acres = 0.0
	if block_doc.area:
		area_acres = flt(block_doc.area) * SQ_METERS_TO_ACRES
	
	# Try to get no_of_seedlings from Crop Plan Block if field is provided
	no_of_seedlings = 0
	if field:
		try:
			# Find crop plans that contain this field
			crop_plans = frappe.get_all(
				"Crop Plan",
				filters={"field": field},
				fields=["name"],
				limit=1
			)
			
			if crop_plans:
				crop_plan_name = crop_plans[0].name
				crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
				
				# Find the block in the crop plan's blocks table
				for crop_plan_block in crop_plan_doc.blocks or []:
					if crop_plan_block.block == block:
						no_of_seedlings = int(crop_plan_block.no_of_seedlings or 0)
						break
		except Exception as e:
			# If there's any error fetching from crop plan, log it but continue
			frappe.log_error(f"Error fetching seedlings from crop plan for block {block}: {str(e)}", "On Demand Activity Block Details")
	
	# If still 0, try to get from block document as fallback (for backward compatibility)
	if no_of_seedlings == 0:
		no_of_seedlings = int(getattr(block_doc, "no_of_seedlings", 0) or 0)
	
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
def create_transfer_tickets_for_activity(activity_name: str):
	"""Manually trigger transfer ticket creation for an activity (equipment + inputs when applicable)."""
	try:
		activity = frappe.get_doc("On Demand Activity", activity_name)
		if activity.status != "Scheduled":
			return {"success": False, "error": "Activity status must be Scheduled"}
		if set(activity._collect_equipment_assets()):
			activity._create_equipment_transfer_tickets()
		if activity._collect_input_items():
			try:
				activity._create_input_transfer_tickets()
			except Exception as inp_e:
				frappe.log_error(
					f"Input ticket error in create_transfer_tickets_for_activity for {activity_name}: {str(inp_e)[:80]}",
					"Input Transfer Ticket",
				)
		return {"success": True, "message": "Transfer ticket creation triggered"}
	except Exception as e:
		frappe.log_error(f"Error in create_transfer_tickets_for_activity for {activity_name}: {str(e)}", "Equipment Transfer Ticket")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_available_assets_for_cluster(
	field: str = None,
	block: str = None,
	asset_category: str = None,
	planned_start: str = None,
	planned_end: str = None,
	exclude_activity: str = None
) -> List[Dict[str, Any]]:
	"""
	Get available assets from the same cluster as the field/block.
	Optionally filters by asset category and excludes assets booked for the time period.
	
	Args:
		field: Field name (Geo Fencing Area)
		block: Block name (Geo Fencing Area) - optional, uses field if not provided
		asset_category: Filter by asset category (e.g., "Machinery", "Implement")
		planned_start: Start datetime to check availability (optional)
		planned_end: End datetime to check availability (optional)
		exclude_activity: Activity name to exclude from conflict check (for updates)
	
	Returns:
		List of available assets with name, asset_name, location, etc.
	"""
	try:
		# Get cluster from field or block
		target_area = block or field
		if not target_area:
			frappe.log_error(f"get_available_assets_for_cluster: No field/block provided", "Asset Filter")
			return []
		
		# Get cluster for the field/block
		cluster = None
		try:
			area_doc = frappe.get_doc("Geo Fencing Area", target_area)
			current = area_doc
			# Traverse up to find cluster
			while current:
				if current.geo_fencing_type == "Cluster":
					cluster = current.name
					break
				if current.parent_area:
					current = frappe.get_doc("Geo Fencing Area", current.parent_area)
				else:
					break
		except Exception as e:
			# Log error but keep it short
			frappe.log_error(f"Error getting cluster for {target_area}: {str(e)[:100]}", "Asset Filter")
			return []
		
		if not cluster:
			frappe.log_error(f"get_available_assets_for_cluster: No cluster found for {target_area}", "Asset Filter")
			return []
		
		# Build list of all geo areas in cluster (cluster + fields + blocks) so we include
		# warehouses linked at cluster, field, or block level (equipment may be in any of these)
		cluster_geo_areas = [cluster]
		try:
			child_areas = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": cluster},
				fields=["name"],
				limit_page_length=0
			)
			for area in child_areas:
				cluster_geo_areas.append(area.name)
				blocks = frappe.get_all(
					"Geo Fencing Area",
					filters={"parent_area": area.name},
					fields=["name"],
					limit_page_length=0
				)
				for b in blocks:
					cluster_geo_areas.append(b.name)
		except Exception:
			pass
		
		# Get all warehouses linked to cluster or any descendant area (field/block)
		warehouses = frappe.get_all(
			"Geo Fencing Area Warehouse",
			fields=["warehouse"],
			filters={"parent": ["in", cluster_geo_areas], "parenttype": "Geo Fencing Area"},
			limit_page_length=0
		)
		cluster_warehouse_list = [w.warehouse for w in warehouses if w.warehouse]
		
		# Get all child warehouses for each cluster warehouse
		from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses
		warehouse_list = []
		for cluster_wh in cluster_warehouse_list:
			# get_child_warehouses returns [children..., warehouse] (includes self)
			child_warehouses = get_child_warehouses(cluster_wh)
			warehouse_list.extend(child_warehouses)
		
		# Remove duplicates while preserving order
		warehouse_list = list(dict.fromkeys(warehouse_list))
		
		if not warehouse_list:
			frappe.log_error(f"get_available_assets_for_cluster: No warehouses in cluster {cluster}", "Asset Filter")
			return []
		
		# Get locations for these warehouses
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		location_list = []
		warehouse_to_location = {}
		for wh in warehouse_list:
			try:
				result = get_location_for_warehouse(wh)
				if result and result.get("location"):
					location_list.append(result.get("location"))
					warehouse_to_location[wh] = result.get("location")
			except Exception:
				# Silently skip warehouses without locations
				continue
		
		# Build asset filters - try both location-based and warehouse-based lookup
		assets = []
		
		# Method 1: Find assets by location (if locations are available)
		if location_list:
			asset_filters = [["location", "in", location_list], ["docstatus", "<", 2]]
			if asset_category:
				asset_filters.append(["asset_category", "like", f"%{asset_category}%"])
			
			assets = frappe.get_all(
				"Asset",
				fields=["name", "asset_name", "asset_category", "location", "status"],
				filters=asset_filters,
				limit=1000
			)
		
		# Method 1b: If no assets yet, use same per-warehouse lookup as Equipments page (e.g. field 1)
		if not assets and warehouse_list:
			from f2c.inventory.logistics_transfer_ticket_api import get_assets_for_warehouse
			seen_names = set()
			for wh in warehouse_list:
				try:
					result = get_assets_for_warehouse(wh)
					wh_assets = result.get("assets") or []
					for a in wh_assets:
						if a.get("name") and a["name"] not in seen_names:
							if not asset_category or ((a.get("asset_category") or "").lower().find(asset_category.lower()) >= 0):
								assets.append(a)
								seen_names.add(a["name"])
				except Exception:
					continue
		
		# Method 2: If no assets found by location, try finding assets by location name pattern matching
		# Build location names for cluster and all its children, then find assets with matching location names
		if not assets:
			from f2c.inventory.logistics_transfer_ticket_api import _build_location_name_for_geo_area
			
			# Get all geo areas in the cluster hierarchy (cluster and all its children)
			cluster_geo_areas = [cluster]
			try:
				# Get all fields and blocks under this cluster
				child_areas = frappe.get_all(
					"Geo Fencing Area",
					filters={"parent_area": cluster},
					fields=["name"],
					limit_page_length=0
				)
				for area in child_areas:
					cluster_geo_areas.append(area.name)
					# Also get blocks under fields
					blocks = frappe.get_all(
						"Geo Fencing Area",
						filters={"parent_area": area.name},
						fields=["name"],
						limit_page_length=0
					)
					for block in blocks:
						cluster_geo_areas.append(block.name)
			except Exception:
				pass
			
			# Build location names for all geo areas in cluster
			location_names = []
			for geo_area_name in cluster_geo_areas:
				try:
					loc_name = _build_location_name_for_geo_area(geo_area_name)
					if loc_name:
						location_names.append(loc_name)
				except Exception:
					continue
			
			# Find locations with matching location_name
			if location_names:
				matching_locations = frappe.get_all(
					"Location",
					filters={"location_name": ["in", location_names]},
					fields=["name"],
					limit_page_length=0
				)
				matching_location_ids = [loc.name for loc in matching_locations]
				
				if matching_location_ids:
					asset_filters = [["location", "in", matching_location_ids], ["docstatus", "<", 2]]
					if asset_category:
						asset_filters.append(["asset_category", "like", f"%{asset_category}%"])
					
					assets = frappe.get_all(
						"Asset",
						fields=["name", "asset_name", "asset_category", "location", "status"],
						filters=asset_filters,
						limit=1000
					)
		
		# Log if no assets found
		if not assets:
			category_info = f" category={asset_category}" if asset_category else ""
			loc_info = f" locations={location_list[:2]}" if location_list else " no locations"
			wh_info = f" warehouses={warehouse_list[:2]}" if warehouse_list else ""
			frappe.log_error(f"get_available_assets_for_cluster: No assets found{loc_info}{wh_info}{category_info}", "Asset Filter")
		
		# If time period is provided, filter out booked assets
		if planned_start and planned_end:
			available_assets = []
			for asset in assets:
				asset_name = asset.name
				try:
					# Check for conflicts in Crop Plan Schedule
					conflicts_cps = frappe.db.sql("""
						SELECT DISTINCT cps.name
						FROM `tabCrop Plan Schedule` cps
						INNER JOIN (
							SELECT parent, asset FROM `tabCrop Plan Schedule Machinery` WHERE asset = %s
							UNION ALL
							SELECT parent, asset FROM `tabCrop Plan Schedule Implement` WHERE asset = %s
							UNION ALL
							SELECT parent, asset FROM `tabCrop Plan Schedule Hand Tool` WHERE asset = %s
							UNION ALL
							SELECT parent, asset FROM `tabCrop Plan Schedule Other Tool` WHERE asset = %s
						) eq ON cps.name = eq.parent
						WHERE cps.status IN ('Scheduled', 'Reported')
							AND cps.planned_start IS NOT NULL
							AND cps.planned_end IS NOT NULL
							AND cps.planned_start < %s
							AND cps.planned_end > %s
					""", (asset_name, asset_name, asset_name, asset_name, planned_end, planned_start), as_dict=True)
					
					# Check for conflicts in On Demand Activity
					conflicts_oda = frappe.db.sql("""
						SELECT DISTINCT oda.name
						FROM `tabOn Demand Activity` oda
						INNER JOIN (
							SELECT parent, asset FROM `tabOn Demand Activity Machinery` WHERE asset = %s
							UNION ALL
							SELECT parent, asset FROM `tabOn Demand Activity Implement` WHERE asset = %s
							UNION ALL
							SELECT parent, asset FROM `tabOn Demand Activity Hand Tool` WHERE asset = %s
							UNION ALL
							SELECT parent, asset FROM `tabOn Demand Activity Other Tool` WHERE asset = %s
						) eq ON oda.name = eq.parent
						WHERE oda.status IN ('Scheduled', 'Reported')
							AND oda.planned_start IS NOT NULL
							AND oda.planned_end IS NOT NULL
							AND oda.planned_start < %s
							AND oda.planned_end > %s
					""", (asset_name, asset_name, asset_name, asset_name, planned_end, planned_start), as_dict=True)
					
					# Exclude the current activity if updating
					if exclude_activity:
						conflicts_oda = [c for c in conflicts_oda if c.name != exclude_activity]
					
					# If no conflicts, asset is available
					if not conflicts_cps and not conflicts_oda:
						available_assets.append(asset)
				except Exception:
					# If conflict check fails, include the asset (better to show it than hide it)
					available_assets.append(asset)
			assets = available_assets
		
		if assets:
			from f2c.inventory.logistics_transfer_ticket_api import _enrich_equipment_display_names

			_enrich_equipment_display_names(assets)

		return assets
	except Exception as e:
		# Catch any unexpected errors and return empty list
		# Log error but keep it short to avoid CharacterLengthExceededError
		error_msg = str(e)[:100] if str(e) else "Unknown error"
		frappe.log_error(f"Error in get_available_assets_for_cluster: {error_msg}", "Asset Filter")
		return []


@frappe.whitelist()
def get_available_implements_for_cluster(
	field: str = None,
	block: str = None,
	planned_start: str = None,
	planned_end: str = None,
	exclude_activity: str = None,
) -> List[Dict[str, Any]]:
	"""Delegate to crop_plan_schedule.get_available_implements_for_cluster (exclude_activity → exclude_schedule)."""
	from f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule import (
		get_available_implements_for_cluster as _get_available_implements_for_cluster,
	)

	return _get_available_implements_for_cluster(
		field=field,
		block=block,
		planned_start=planned_start,
		planned_end=planned_end,
		exclude_schedule=exclude_activity,
	)


@frappe.whitelist()
def schedule_campaign(
	campaign_name: str,
	planned_start: str,
	planned_end: str,
	campaign_level: str,
	selected_areas: List[str],
	selected_blocks: List[str] = None
) -> Dict[str, Any]:
	"""
	Schedule a campaign by creating block-level On Demand Activity entries.
	
	Args:
		campaign_name: Name of the parent campaign
		planned_start: Start datetime for the campaign
		planned_end: End datetime for the campaign
		campaign_level: Level of campaign (Farm/Cluster/Field)
		selected_areas: List of area names at the selected level
		selected_blocks: Optional list of specific block names to include (filters blocks if provided)
		
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
	
	# Filter blocks if specific blocks are selected
	if selected_blocks and len(selected_blocks) > 0:
		selected_blocks_set = set(selected_blocks)
		all_blocks = [b for b in all_blocks if b["block"] in selected_blocks_set]
	
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
			
			# Try to get no_of_seedlings from Crop Plan Block if field is available
			no_of_seedlings = 0
			field_name = block_info.get("field")
			if field_name:
				try:
					# Find crop plans that contain this field
					crop_plans = frappe.get_all(
						"Crop Plan",
						filters={"field": field_name},
						fields=["name"],
						limit=1
					)
					
					if crop_plans:
						crop_plan_name = crop_plans[0].name
						crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
						
						# Find the block in the crop plan's blocks table
						for crop_plan_block in crop_plan_doc.blocks or []:
							if crop_plan_block.block == block_info["block"]:
								no_of_seedlings = int(crop_plan_block.no_of_seedlings or 0)
								break
				except Exception as e:
					# If there's any error fetching from crop plan, log it but continue
					frappe.log_error(f"Error fetching seedlings from crop plan for block {block_info['block']}: {str(e)}", "On Demand Activity Campaign Scheduling")
			
			# If still 0, try to get from block document as fallback (for backward compatibility)
			if no_of_seedlings == 0:
				no_of_seedlings = int(getattr(block_doc, "no_of_seedlings", 0) or 0)
			
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
				"campaign_name": parent_campaign.campaign_name or campaign_name,
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
				"estimated_irrigation_water_liters": parent_campaign.estimated_irrigation_water_liters or 0,
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
						"return_type": getattr(mach, "return_type", None) or "Non Returnable"
					})
			if parent_campaign.get("implements"):
				for impl in parent_campaign.implements:
					child_activity.append("implements", {
						"asset": impl.asset,
						"asset_name": impl.asset_name,
						"planned_hours": impl.planned_hours or 0,
						"return_type": getattr(impl, "return_type", None) or "Non Returnable"
					})
			if parent_campaign.get("hand_tools"):
				for ht in parent_campaign.hand_tools:
					child_activity.append("hand_tools", {
						"asset": ht.asset,
						"asset_name": ht.asset_name,
						"planned_hours": ht.planned_hours or 0,
						"return_type": getattr(ht, "return_type", None) or "Non Returnable"
					})
			if parent_campaign.get("other_tools"):
				for ot in parent_campaign.other_tools:
					child_activity.append("other_tools", {
						"asset": ot.asset,
						"asset_name": ot.asset_name,
						"planned_hours": ot.planned_hours or 0,
						"return_type": getattr(ot, "return_type", None) or "Non Returnable"
					})
			
			child_activity.insert()
			created_entries.append(child_activity.name)
	
	# Update parent campaign status and store campaign areas
	# Keep status as Draft (do not move to Archived)
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


_ON_DEMAND_EQUIPMENT_CHILD_TABLES = (
	"On Demand Activity Machinery",
	"On Demand Activity Implement",
	"On Demand Activity Hand Tool",
	"On Demand Activity Other Tool",
)


@frappe.whitelist()
def get_on_demand_activity_equipment_list(activity_names) -> List[Dict[str, Any]]:
	"""
	Return equipment rows {parent, asset, asset_name} for On Demand Activity documents.
	Same shape as get_execution_equipment_list / get_schedule_equipment_list for Execution page UI.
	"""
	if not activity_names:
		return []
	if isinstance(activity_names, str):
		try:
			activity_names = json.loads(activity_names)
		except Exception:
			activity_names = [activity_names]
	names = [str(n).strip() for n in activity_names if n and str(n).strip()]
	if not names:
		return []
	out: List[Dict[str, Any]] = []
	seen: Set[Tuple[str, str]] = set()
	for table in _ON_DEMAND_EQUIPMENT_CHILD_TABLES:
		rows = frappe.get_all(
			table,
			filters={"parent": ["in", names]},
			fields=["parent", "asset", "asset_name"],
			limit=5000,
			ignore_permissions=True,
		)
		for r in rows or []:
			parent = (r.get("parent") or "").strip()
			asset = (r.get("asset") or "").strip()
			asset_name = (r.get("asset_name") or "").strip()
			if not parent:
				continue
			if not asset and not asset_name:
				continue
			dedupe_key = (parent, asset or asset_name)
			if dedupe_key in seen:
				continue
			seen.add(dedupe_key)
			out.append({"parent": parent, "asset": asset or None, "asset_name": asset_name or None})
	return out


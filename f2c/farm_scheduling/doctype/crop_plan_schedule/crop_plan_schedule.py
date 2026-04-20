# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime

from f2c.farm_scheduling.cluster_area_resolution import resolve_cluster_warehouses_locations

SQ_METERS_TO_ACRES = 0.000247105


class CropPlanSchedule(Document):
	def validate(self):
		self._ltt_forward_planned_warn_shown = False
		self._ltt_return_planned_warn_shown = False
		self._validate_status_cancel_reason()
		self._validate_rescheduled_readonly()
		self._validate_block_belongs_to_crop_plan()
		self._validate_activity_matches_block()
		self._autofill_activity_fields()
		self._sync_approved_input_mix_from_plan()
		self._compute_totals()
		self._compute_labour_total()
		self._compute_water()
		self._validate_spray_requirements()
		self._recompute_input_totals_if_needed()
		# Validate equipment slot availability
		if self.status == "Scheduled" and self.planned_start and self.planned_end:
			self._validate_equipment_slot_availability()
		self._validate_transport_vehicle_for_schedule()

	def on_trash(self):
		"""Prevent deletion if linked to execution or if it's a rescheduled source."""
		self._check_linked_execution()
		self._check_rescheduled_references()

	def on_cancel(self):
		"""Prevent cancellation if linked to execution or if it's a rescheduled source."""
		self._check_linked_execution()
		self._check_rescheduled_references()

	def _check_linked_execution(self):
		"""Check if this schedule is linked to a Farm Task Execution and handle the link appropriately."""
		if self.execution_ref:
			exec_name = self.execution_ref
			# Check if execution exists and get its status
			exec_status = frappe.db.get_value("Farm Task Execution", exec_name, "status")
			if exec_status:
				# If execution is in progress, prevent deletion/cancellation
				if exec_status in ("In Progress", "Reported"):
					frappe.throw(
						f"Cannot delete or cancel because Crop Plan Schedule <b>{self.name}</b> is linked with Farm Task Execution <b>{exec_name}</b> which is In Progress. Please complete or abort the execution first."
					)
				# If execution is in terminal state (Completed/Aborted), allow deletion/cancellation
				# but clear the bidirectional link to maintain data integrity
				if exec_status in ("Completed", "Aborted"):
					# Clear schedule_ref on the execution to break the link
					frappe.db.set_value("Farm Task Execution", exec_name, "schedule_ref", None, update_modified=False)
					# Clear execution_ref on this schedule (will be cleared on deletion anyway, but good for cancellation)
					self.execution_ref = None

	def _check_rescheduled_references(self):
		"""Check if this schedule is referenced by other schedules as rescheduled_from."""
		referencing_schedules = frappe.get_all(
			"Crop Plan Schedule",
			filters={"rescheduled_from": self.name},
			fields=["name"],
			limit=1
		)
		if referencing_schedules:
			ref_name = referencing_schedules[0].name
			frappe.throw(
				f"Cannot delete or cancel because Crop Plan Schedule <b>{self.name}</b> is linked with Crop Plan Schedule <b>{ref_name}</b>"
			)

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

	def _sync_approved_input_mix_from_plan(self):
		"""
		Sync approved_input_mix and nested inputs from the parent Crop Plan.
		Only syncs if the document is in a state where it can still be modified.
		"""
		if not self.crop_plan or not self.crop_plan_activity:
			return
		
		# Only sync for Draft or Scheduled documents
		if self.status not in ["Draft", "Scheduled"]:
			return

		# Find the corresponding approved_inputs in the Crop Plan
		approved_inputs = frappe.get_all(
			"Crop Plan Activity Input",
			filters={
				"parent": self.crop_plan,
				"parenttype": "Crop Plan",
				"parentfield": "approved_inputs",
				"activity_reference": self.crop_plan_activity,
			},
			fields=["farm_task", "item", "item_name", "quantity", "unit"],
			order_by="idx asc",
		)
		
		if not approved_inputs:
			# Fallback matching: match by activity name and sequence if reference is missing/stale
			approved_inputs = frappe.get_all(
				"Crop Plan Activity Input",
				filters={
					"parent": self.crop_plan,
					"parenttype": "Crop Plan",
					"parentfield": "approved_inputs",
					"activity_name": self.activity_name,
					"sequence": self.sequence,
				},
				fields=["farm_task", "item", "item_name", "quantity", "unit"],
				order_by="idx asc",
			)
		
		if not approved_inputs:
			return

		farm_task = approved_inputs[0].get("farm_task")
		
		if farm_task:
			self.approved_input_mix = farm_task
			
			# Always sync inputs to ensure consistency with the plan.
			self.set("inputs", [])
			
			for input_item in approved_inputs:
				item_code = getattr(input_item, "item", None) or (input_item.get("item") if isinstance(input_item, dict) else None)
				item_name = getattr(input_item, "item_name", None) or (input_item.get("item_name") if isinstance(input_item, dict) else None)
				quantity = getattr(input_item, "quantity", None) or (input_item.get("quantity") if isinstance(input_item, dict) else None)
				unit = getattr(input_item, "unit", None) or (input_item.get("unit") if isinstance(input_item, dict) else None)
				
				self.append("inputs", {
					"item": item_code,
					"item_name": item_name,
					"rate_quantity": quantity,
					"unit": unit
				})

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
		"""Tractor, thresher, implement assets — move on machinery LTT, not on pickup/inputs LTT.
		Any asset linked to a Machinery doc (except type Vehicle) is field machinery.
		Handles tractors mis-filed under Hand/Other Tool."""
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
		"""One LTT per machinery row; standalone implements; promoted hand/other machinery.
		Co-moved implements are not duplicated."""
		from f2c.inventory.logistics_transfer_ticket_api import expand_machinery_transfer_asset_requests
		from f2c.inventory.tractor_implement_ltt_plan import implement_asset_ids_paired_to_tractors_on_schedule

		paired_impl_assets = implement_asset_ids_paired_to_tractors_on_schedule(self)
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
			if imp.asset and imp.asset not in covered and imp.asset not in paired_impl_assets:
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
		
		# Handle new schedules and rescheduled sources
		exclude_names = []
		if self.name:
			exclude_names.append(self.name)
		if getattr(self, "rescheduled_from", None):
			exclude_names.append(self.rescheduled_from)
			
		name_filter = " AND ".join(["cps.name != %s"] * len(exclude_names)) if exclude_names else "1=1"
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
			WHERE {name_filter}
				AND cps.status IN ('Scheduled', 'Reported')
				AND cps.planned_start IS NOT NULL
				AND cps.planned_end IS NOT NULL
		"""
		
		# Get all schedules with matching assets, then filter by cluster in Python
		params = equipment_assets * 4  # 4 times for each UNION ALL
		params.extend(exclude_names)
		schedules = frappe.db.sql(query, params, as_dict=True)
		
		# Filter by cluster - check if each schedule's field belongs to the same cluster
		for schedule in schedules:
			# Explicitly exclude the current schedule when editing
			if self.name and schedule.schedule_name == self.name:
				continue
			
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
			WHERE oda.status IN ('Scheduled', 'Reported')
				AND oda.planned_start IS NOT NULL
				AND oda.planned_end IS NOT NULL
		"""
		
		# Get all activities with matching assets, then filter by cluster in Python
		params = equipment_assets * 4  # 4 times for each UNION ALL
		activities = frappe.db.sql(query, params, as_dict=True)
		
		# Filter by cluster - check if each activity's field belongs to the same cluster
		for activity in activities:
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
		
		# Get the cluster for the current schedule's field
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
			frappe.log_error(f"Schedule {self.name} has no field specified for grouping assets", "Equipment Transfer Ticket")
			return assets_by_warehouse
		
		# Get cluster warehouse as fallback
		cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
		if not cluster_warehouse:
			frappe.log_error(f"Cannot find cluster warehouse for field {self.field} in schedule {self.name}", "Equipment Transfer Ticket")
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
		"""True if this schedule's planned window overlaps the ticket's planned pickup/drop window.

		Open LTTs are matched by from→to and assets alone; without a time check, one tractor
		still Pending Pickup from an earlier day blocks creating a new LTT for a different date.
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
		"""Duplicate only for the same schedule row (schedule_ref) plus same route/assets.

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
		self, from_warehouse: str, to_warehouse: str, asset_list: list[str], schedule_ref: str | None = None
	) -> str | None:
		"""Return name of an open LTT with same from→to where ticket already covers this leg's assets.

		Uses subset match: scheduled assets may be a subset of ticket rows because
		create_logistics_transfer_ticket appends co-moving implement assets (superset on LTT).
		When schedule_ref is set (Crop Plan Schedule / On Demand Activity name), only the same row
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
		self, from_warehouse: str, to_warehouse: str, input_items: list[dict], schedule_ref: str | None = None
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
			"F2C Settings have schedule-based LTT planned times enabled, but this schedule has no Planned Start yet. "
			"Forward transfer tickets will use the current time as the planned drop-off anchor (and derive pickup from travel and lead) until Planned Start is set; "
			"save again after setting it to refresh open tickets that are still Pending Pickup.",
			title="LTT planned times",
			indicator="orange",
		)
		self._ltt_forward_planned_warn_shown = True

	def _ltt_forward_planned_drop_off_anchor_str(self) -> str:
		"""planned_drop_off_on for forward LTTs from scheduling: planned_start when set and parseable, else server now."""
		from frappe.utils import get_datetime, get_datetime_str, now_datetime

		dt = get_datetime(self.planned_start) if self.planned_start else None
		if not dt:
			dt = now_datetime()
		return get_datetime_str(dt)

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
		# Machinery-only legs (one open LTT per unit)
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
		# Vehicle legs (inputs + hand/other tools)
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

	def _create_equipment_transfer_tickets(self):
		"""Create forward LTTs: vehicle (inputs + hand/other tools) then machinery (per-unit)."""
		frappe.log_error(
			title="Crop Plan Schedule LTT Trace",
			message=(
				f"LTT TRACE: _create_equipment_transfer_tickets called for {self.name} "
				f"(status={self.status}, field={self.field}, planned_start={self.planned_start!s})"
			),
		)
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
				title="Crop Plan Schedule LTT",
				message=f"Schedule {self.name}: vehicle LTT skipped — no target warehouse for field {self.field}.",
			)
			return
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		try:
			target_location_result = get_location_for_warehouse(target_warehouse)
			if not target_location_result or not target_location_result.get("location"):
				frappe.log_error(
					title="Crop Plan Schedule LTT",
					message=(
						f"Schedule {self.name}: vehicle LTT skipped — field warehouse {target_warehouse} has no ERPNext Location "
						"(run Geo Warehouses → Location sync)."
					),
				)
				frappe.msgprint(
					_("Field warehouse has no mapped Location, so logistics tickets cannot be created. "
					  "Run Location sync from Geo Warehouses. Details were written to Error Log (Crop Plan Schedule LTT)."),
					indicator="orange",
					title=_("Transfer ticket skipped"),
				)
				return
		except Exception as ex:
			frappe.log_error(
				title="Crop Plan Schedule LTT",
				message=(
					f"Schedule {self.name}: vehicle LTT skipped — get_location_for_warehouse failed for {target_warehouse}: {ex!s}"
				),
			)
			return

		input_src = self._get_source_warehouse_for_inputs() if input_items else None
		if input_items and not input_src:
			frappe.msgprint(
				f"Cannot find source warehouse for input items in schedule {self.name}.",
				indicator="orange", title="Vehicle Transfer Ticket Creation Failed",
			)
			return

		tool_by_wh = self._group_assets_by_source_warehouse(tool_assets) if tool_assets else {}
		from_warehouses: set[str] = set(tool_by_wh.keys())
		if input_items and input_src:
			from_warehouses.add(input_src)

		if not from_warehouses and (input_items or tool_assets):
			frappe.log_error(
				title="Crop Plan Schedule LTT",
				message=(
					f"Schedule {self.name}: vehicle LTT skipped — no source warehouse resolved "
					f"(field={self.field}, tools={len(tool_assets)}, input_lines={len(input_items)}). "
					"Often caused by missing cluster warehouse for the field."
				),
			)
			frappe.msgprint(
				_("Could not resolve a source warehouse for this transfer. Check cluster warehouse for the field. "
				  "Details: Error Log (Crop Plan Schedule LTT)."),
				indicator="orange",
				title=_("Transfer ticket skipped"),
			)
			return

		from f2c.inventory.logistics_transfer_ticket_api import (
			create_logistics_transfer_ticket,
			planned_pickup_drop_for_activity_start,
		)

		inputs_included = False
		created: list[str] = []
		errors: list[str] = []
		leg_skips: list[str] = []
		tv = (getattr(self, "transport_vehicle", None) or "").strip()

		frappe.log_error(
			title="Crop Plan Schedule LTT Trace",
			message=(
				f"LTT TRACE vehicle: {self.name} — from_warehouses={sorted(from_warehouses)}, "
				f"target={target_warehouse}, inputs={len(input_items)}, tools={len(tool_assets)}, tv={tv!r}"
			),
		)

		for from_warehouse in sorted(from_warehouses):
			tools_here = tool_by_wh.get(from_warehouse, [])
			stock_here = input_items if (input_items and input_src == from_warehouse) else None
			if not stock_here and not tools_here:
				leg_skips.append(f"{from_warehouse}: no stock or tools for this leg")
				continue
			if from_warehouse == target_warehouse and tools_here:
				leg_skips.append(
					f"{from_warehouse}: hand/other tools are already at the field warehouse — no forward vehicle ticket"
				)
				continue
			if from_warehouse == target_warehouse and not tools_here and stock_here:
				pass
			elif from_warehouse == target_warehouse:
				leg_skips.append(f"{from_warehouse}: same as target and no stock leg")
				continue

			dup = self._find_open_vehicle_consumables_ltt_duplicate(
				from_warehouse, target_warehouse, tools_here, stock_here, self.name
			)
			if dup:
				# Ticket already exists — update its planned times if setting is on and planned_start is set.
				try:
					from f2c.inventory.logistics_transfer_ticket_api import update_ltt_planned_times_if_pending_pickup
					pt = planned_pickup_drop_for_activity_start(
						self.planned_start, from_warehouse, target_warehouse
					)
					if pt:
						update_ltt_planned_times_if_pending_pickup(dup, pt[0], pt[1])
				except Exception:
					pass
				leg_skips.append(f"{from_warehouse}: open ticket already exists ({dup})")
				continue

			try:
				planned_times = planned_pickup_drop_for_activity_start(
					self.planned_start, from_warehouse, target_warehouse
				)
				planned_kwargs = {}
				if planned_times:
					planned_kwargs["planned_pickup_on"] = planned_times[0]
					planned_kwargs["planned_drop_off_on"] = planned_times[1]
				planned_kwargs.setdefault("planned_drop_off_on", self._ltt_forward_planned_drop_off_anchor_str())
				frappe.log_error(
					title="Crop Plan Schedule LTT Trace",
					message=(
						f"LTT TRACE vehicle leg: {self.name} from={from_warehouse} → to={target_warehouse}, "
						f"stock={len(stock_here or [])}, tools={len(tools_here)}, planned_times={planned_times!r}"
					),
				)
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
		elif errors:
			frappe.msgprint(
				_("Failed to create vehicle/consumables transfer ticket(s). See Error Log (Vehicle Transfer Ticket)."),
				indicator="red",
				title=_("Transfer ticket creation failed"),
			)
		elif (input_items or tool_assets) and leg_skips:
			# Nothing created and no exception — explain skips (duplicate / same warehouse / empty leg)
			frappe.log_error(
				title="Crop Plan Schedule LTT",
				message=(
					f"Schedule {self.name}: no new vehicle/consumables LTT. target_wh={target_warehouse}, "
					f"planned_start={self.planned_start!s}, transport_vehicle={tv!s}. Legs: {' | '.join(leg_skips)}"
				),
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
			return
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		try:
			target_location_result = get_location_for_warehouse(target_warehouse)
			if not target_location_result or not target_location_result.get("location"):
				frappe.log_error(
					title="Crop Plan Schedule LTT",
					message=(
						f"Schedule {self.name}: machinery LTT skipped — field warehouse {target_warehouse} has no Location."
					),
				)
				return
		except Exception as ex:
			frappe.log_error(
				title="Crop Plan Schedule LTT",
				message=(
					f"Schedule {self.name}: machinery LTT skipped — location lookup failed for {target_warehouse}: {ex!s}"
				),
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

		frappe.log_error(
			title="Crop Plan Schedule LTT Trace",
			message=(
				f"LTT TRACE machinery: {self.name} — {len(unit_payloads)} unit(s), target={target_warehouse}, "
				f"planned_start={self.planned_start!s}"
			),
		)

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
				# If Asset→warehouse is cluster but the job is field-based swap, still create leg1 from field WH
				# so receivers see two tickets (field→cluster, cluster→field); avoid cluster→cluster only.
				leg1_from = target_warehouse if from_warehouse == cluster_wh else from_warehouse
				if leg1_from != cluster_wh:
					dup1 = self._find_open_equipment_ltt_duplicate(
						leg1_from, cluster_wh, leg1_names, self.name
					)
					if dup1:
						machinery_skips.append(f"{primary}: round-trip leg1 open ticket {dup1}")
					else:
						try:
							pt1 = planned_pickup_drop_for_round_trip_leg1_immediate(leg1_from, cluster_wh)
							kw1: dict = {}
							if pt1:
								kw1["planned_pickup_on"] = pt1[0]
								kw1["planned_drop_off_on"] = pt1[1]
							kw1.setdefault("planned_drop_off_on", self._ltt_forward_planned_drop_off_anchor_str())
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
					try:
						from f2c.inventory.logistics_transfer_ticket_api import update_ltt_planned_times_if_pending_pickup

						pt_u = planned_pickup_drop_for_round_trip_leg2_from_schedule(
							self.planned_start, cluster_wh, target_warehouse
						)
						if pt_u:
							update_ltt_planned_times_if_pending_pickup(dup2, pt_u[0], pt_u[1])
					except Exception:
						pass
					machinery_skips.append(f"{primary}: round-trip leg2 open ticket {dup2}")
				else:
					try:
						pt2 = planned_pickup_drop_for_round_trip_leg2_from_schedule(
							self.planned_start, cluster_wh, target_warehouse
						)
						kw2: dict = {}
						if pt2:
							kw2["planned_pickup_on"] = pt2[0]
							kw2["planned_drop_off_on"] = pt2[1]
						kw2.setdefault("planned_drop_off_on", self._ltt_forward_planned_drop_off_anchor_str())
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
				# Ticket already exists — update its planned times if setting is on and planned_start is set.
				try:
					from f2c.inventory.logistics_transfer_ticket_api import update_ltt_planned_times_if_pending_pickup
					pt = planned_pickup_drop_for_activity_start(
						self.planned_start, from_warehouse, target_warehouse
					)
					if pt:
						update_ltt_planned_times_if_pending_pickup(dup_ticket, pt[0], pt[1])
				except Exception:
					pass
				machinery_skips.append(f"{primary}: open ticket {dup_ticket}")
				continue
			try:
				planned_times = planned_pickup_drop_for_activity_start(
					self.planned_start, from_warehouse, target_warehouse
				)
				planned_kwargs = {}
				if planned_times:
					planned_kwargs["planned_pickup_on"] = planned_times[0]
					planned_kwargs["planned_drop_off_on"] = planned_times[1]
				planned_kwargs.setdefault("planned_drop_off_on", self._ltt_forward_planned_drop_off_anchor_str())
				frappe.log_error(
					title="Crop Plan Schedule LTT Trace",
					message=(
						f"LTT TRACE machinery unit: {self.name} asset={primary} from={from_warehouse} → to={target_warehouse}, "
						f"tv={transport_machinery!r}, planned_times={planned_times!r}"
					),
				)
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
				title="Crop Plan Schedule LTT",
				message=(
					f"Schedule {self.name}: no new machinery LTT. target_wh={target_warehouse}, "
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

	def _needs_transport_vehicle(self) -> bool:
		if self._collect_input_items():
			return True
		for row in self.get("hand_tools") or []:
			if row.get("asset"):
				return True
		for row in self.get("other_tools") or []:
			if row.get("asset"):
				return True
		return False

	def _validate_transport_vehicle_for_schedule(self):
		tv = (getattr(self, "transport_vehicle", None) or "").strip()
		if tv:
			mtype = frappe.db.get_value("Machinery", tv, "machinery_type")
			if mtype != "Vehicle":
				frappe.throw(
					"Transport Vehicle must be a Machinery record with type Vehicle (tractors are not allowed)."
				)
		if self.status != "Scheduled":
			return
		if self._needs_transport_vehicle() and not tv:
			frappe.throw(
				"Transport Vehicle is required when the schedule includes approved inputs with quantity, hand tools, or other tools."
			)

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
		# Get company from crop plan
		company = None
		if self.crop_plan:
			try:
				company = frappe.db.get_value("Crop Plan", self.crop_plan, "company")
			except Exception:
				pass
		
		if not company:
			# Try to get company from target warehouse (if we have it)
			target_warehouse = self._get_target_warehouse_for_field(self.field) if self.field else None
			if target_warehouse:
				try:
					company = frappe.db.get_value("Warehouse", target_warehouse, "company")
				except Exception:
					pass
		
		if not company:
			frappe.log_error(f"Cannot determine company for schedule {self.name}", "Input Transfer Ticket")
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
		"""Create Logistics Transfer Tickets for input items when schedule is saved with status Scheduled.
		Note: If inputs were already included in equipment transfer tickets, this will skip creating a separate ticket.
		This prevents duplicate inputs when equipment and inputs come from different source warehouses."""
		if self.status != "Scheduled":
			frappe.log_error(f"Schedule {self.name} status is not 'Scheduled' (current: {self.status}), skipping input ticket creation", "Input Transfer Ticket")
			return
		
		if not self.field:
			frappe.log_error(f"Schedule {self.name} has no field specified, skipping input ticket creation", "Input Transfer Ticket")
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
							frappe.log_error(f"Input items for schedule {self.name} were already included in equipment transfer ticket {ticket_name}, skipping separate input ticket to prevent duplicates", "Input Transfer Ticket")
							return
				except Exception:
					continue
		
		# Collect input items
		input_items = self._collect_input_items()
		if not input_items:
			frappe.log_error(f"Schedule {self.name} has no input items to transfer", "Input Transfer Ticket")
			return  # No input items to transfer
		
		# Log summary only (not full list to avoid exceeding 140 char limit)
		item_codes = [item.get("item_code", "") for item in input_items[:3]]  # First 3 items only
		item_summary = ", ".join(item_codes)
		if len(input_items) > 3:
			item_summary += f" (+{len(input_items) - 3} more)"
		frappe.log_error(f"Creating input transfer tickets for schedule {self.name} with {len(input_items)} items: {item_summary}", "Input Transfer Ticket")
		
		# Get source warehouse
		source_warehouse = self._get_source_warehouse_for_inputs()
		if not source_warehouse:
			error_msg = f"Cannot find source warehouse (cluster warehouse) for input items in schedule {self.name}. Please ensure field has a cluster warehouse configured or company has a default warehouse."
			frappe.log_error(error_msg, "Input Transfer Ticket")
			frappe.msgprint(error_msg, indicator="orange", title="Input Transfer Ticket Creation Failed")
			return
		
		# Get target warehouse from field
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if not target_warehouse:
			error_msg = f"Cannot find target warehouse for field {self.field} in schedule {self.name}"
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
				company = None
				if self.crop_plan:
					company = frappe.db.get_value("Crop Plan", self.crop_plan, "company")
				if not company:
					company = frappe.db.get_value("Warehouse", source_warehouse, "company")
				from f2c.inventory.material_request_api import create_material_request
				create_material_request(
					warehouse=source_warehouse,
					items=shortfall_items,
					material_request_type="Material Transfer",
					company=company,
					notes=f"Shortfall for schedule {self.name}. Request transfer to cluster/source.",
				)
				frappe.msgprint(
					f"Created Material Request for {len(shortfall_items)} item(s) with insufficient stock at source.",
					indicator="orange",
					title="Shortfall",
				)
		except Exception as e:
			frappe.log_error(f"Shortfall check/MR for schedule {self.name}: {str(e)}", "Input Transfer Ticket")
		
		# Skip if an open input-only ticket already exists for same warehouses and stock lines
		dup_in = self._find_open_input_only_ltt_duplicate(
			source_warehouse, target_warehouse, input_items, self.name
		)
		if dup_in:
			frappe.log_error(
				f"Duplicate input transfer ticket already exists for schedule {self.name}: {dup_in} (same items and warehouses), skipping",
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
		planned_kwargs.setdefault("planned_drop_off_on", self._ltt_forward_planned_drop_off_anchor_str())
		try:
			result = create_logistics_transfer_ticket(
				from_warehouse=source_warehouse,
				to_warehouse=target_warehouse,
				stock_items=input_items,
				assets=None,
				transport_vehicle=getattr(self, "transport_vehicle", None),
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

	def _has_more_activities_for_assets(self, equipment_assets: List[str], after_time: str, exclude_schedule: str = None) -> bool:
		"""Check if assets have more scheduled activities on the same day after the given time.
		
		Checks both Crop Plan Schedule and On Demand Activity for overlapping assets
		that are scheduled after the given time on the same day.
		
		Args:
			equipment_assets: List of asset names to check
			after_time: Datetime string - check for activities after this time
			exclude_schedule: Schedule/Activity name to exclude from check (current activity)
		
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
			
			if exclude_schedule:
				cps_query += " AND cps.name != %s"
			
			cps_params = equipment_assets * 4 + [after_date, after_time]
			if exclude_schedule:
				cps_query += " AND cps.name != %s"
				cps_params.append(exclude_schedule)
			
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
			if exclude_schedule:
				oda_query += " AND oda.name != %s"
				oda_params.append(exclude_schedule)
			
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
			frappe.log_error(f"Schedule {self.name} status is not 'Completed' (current: {self.status}), skipping return ticket creation", "Return Transfer Ticket")
			return
		
		if not self.field:
			frappe.log_error(f"Schedule {self.name} has no field specified, skipping return ticket creation", "Return Transfer Ticket")
			return

		# When schedule-based planned times are on but planned_end is missing, still create return LTTs;
		# planned_pickup_drop_for_activity_start uses now as drop anchor so LTT gets explicit planned times.

		# Get equipment assets
		equipment_assets = self._collect_equipment_assets()
		if not equipment_assets:
			frappe.log_error(f"Schedule {self.name} has no equipment assets to return", "Return Transfer Ticket")
			return
		
		# Check if there are more activities scheduled for these assets on the same day
		# Only return to cluster if this is the last activity
		if self.planned_end:
			should_return = self._should_return_to_cluster(equipment_assets, self.planned_end)
			if not should_return:
				frappe.log_error(f"Schedule {self.name} has more activities scheduled for assets {equipment_assets} after {self.planned_end}, skipping return transfer", "Return Transfer Ticket")
				return  # More activities exist, skip return transfer
		
		# Get field warehouse (source for return = where equipment currently is)
		field_warehouse = self._get_target_warehouse_for_field(self.field)
		if not field_warehouse:
			error_msg = f"Cannot find field warehouse for field {self.field} in schedule {self.name}"
			frappe.log_error(error_msg, "Return Transfer Ticket")
			return
		
		# Get cluster warehouse (destination for return)
		cluster_warehouse = self._get_cluster_warehouse_for_field(self.field)
		if not cluster_warehouse:
			error_msg = f"Cannot find cluster warehouse for field {self.field} in schedule {self.name}"
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
			error_msg = f"Error creating return transfer ticket from {field_warehouse} to {cluster_warehouse} for schedule {self.name}: {str(e)}"
			frappe.log_error(title="Return Transfer Ticket", message=f"{error_msg}\n{frappe.get_traceback()}")
			frappe.msgprint("Failed to create return transfer ticket. Please check Error Log for details.", indicator="red", title="Return Transfer Ticket Creation Failed")

	def after_insert(self):
		"""Create transfer tickets when schedule is first created with status Scheduled."""
		frappe.log_error(
			title="Crop Plan Schedule LTT Trace",
			message=f"LTT TRACE after_insert: {self.name} status={self.status}, is_new={self.is_new()}",
		)
		if self.status == "Scheduled":
			try:
				current_assets = set(self._collect_equipment_assets())
				if current_assets or self._collect_input_items():
					self._create_equipment_transfer_tickets()
				if self._collect_input_items():
					try:
						self._create_input_transfer_tickets()
					except Exception as inp_e:
						frappe.log_error(
							title="Input Transfer Ticket",
							message=f"Input ticket error for {self.name}: {str(inp_e)}\n{frappe.get_traceback()}",
						)
			except Exception as e:
				# Log error but don't block schedule creation
				frappe.log_error(
					title="Transfer Ticket",
					message=f"Transfer ticket creation error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
				)
				# Don't raise - allow schedule to be created even if ticket creation fails

	def on_update(self):
		"""Create transfer tickets when schedule status changes to Scheduled or equipment is added.
		Create return transfer tickets when status changes to Completed."""
		frappe.log_error(
			title="Crop Plan Schedule LTT Trace",
			message=f"LTT TRACE on_update: {self.name} status={self.status}, is_new={self.is_new()}",
		)
		# Use doc-before-save for old status; on_update runs after DB commit so get_value would return new value
		old_doc = self.get_doc_before_save() if not self.is_new() else None
		old_status = old_doc.get("status") if old_doc else None

		if not self.is_new():
			# Check if status changed to Completed - create return transfer tickets
			if (old_status or "") != "Completed" and self.status == "Completed":
				try:
					self._create_return_transfer_tickets()
				except Exception as e:
					frappe.log_error(
						title="Return Transfer Ticket",
						message=f"Return transfer ticket error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
					)
					# Don't raise - allow schedule to be updated even if ticket creation fails
				return  # Don't process forward transfers if status is Completed
			# Already Completed: resync return LTT planned times if planned_end changed
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
							title="Return Transfer Ticket",
							message=f"Return LTT planned time sync for {self.name}: {str(e)}\n{frappe.get_traceback()}",
						)
		
		# Forward LTTs: only when Scheduled. During insert, `is_new()` is still True inside on_update
		# (see Document.insert → run_post_save_methods before __islocal is cleared), so skip here —
		# after_insert already ran ticket creation for new rows.
		if self.status != "Scheduled":
			return
		if self.is_new():
			return
		try:
			if set(self._collect_equipment_assets()) or self._collect_input_items():
				self._create_equipment_transfer_tickets()
			if self._collect_input_items():
				try:
					self._create_input_transfer_tickets()
				except Exception as inp_e:
					frappe.log_error(
						title="Input Transfer Ticket",
						message=f"Input ticket error for {self.name}: {str(inp_e)}\n{frappe.get_traceback()}",
					)
		except Exception as e:
			frappe.log_error(
				title="Transfer Ticket",
				message=f"Transfer ticket error for {self.name}: {str(e)}\n{frappe.get_traceback()}",
			)

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


# Types that use engine fuel for scheduling (matches Machinery "Engine" section + common self-propelled types).
DIESEL_PLANNING_MACHINERY_TYPES = frozenset(
	{
		"tractor",
		"harvester",
		"combine harvester",
		"thresher",
		"baler",
		"tiller",
		"vehicle",
		"earthmoving",
		"generator",
	}
)


@frappe.whitelist()
def get_machinery_display_labels_for_assets(assets=None) -> Dict[str, str]:
	"""
	Map Asset.name -> display label for scheduling tags (same as Machinery form / Equipment List NAME).

	Uses Machinery.machinery_name when set, else Machinery.name — not Asset.asset_name composites.
	"""
	import json

	if assets is None:
		return {}
	if isinstance(assets, str):
		try:
			assets = json.loads(assets)
		except Exception:
			assets = [x.strip() for x in str(assets).split(",") if x.strip()]
	if not isinstance(assets, (list, tuple)):
		return {}
	names = [str(a).strip() for a in assets if a is not None and str(a).strip()]
	if not names:
		return {}
	rows = frappe.get_all(
		"Machinery",
		filters={"asset": ["in", names]},
		fields=["name", "asset", "machinery_name"],
		limit=max(len(names) + 10, 50),
	)
	out: Dict[str, str] = {}
	for r in rows:
		ak = (r.get("asset") or "").strip()
		if not ak or ak in out:
			continue
		mn = (r.get("machinery_name") or "").strip()
		docn = (r.get("name") or "").strip()
		out[ak] = mn if mn else docn
	return out


@frappe.whitelist()
def get_machinery_schedule_details(asset: str) -> Dict[str, Any]:
	"""Return tractor scheduling metadata for a selected machinery asset."""
	result = {
		"asset": asset,
		"machinery": None,
		"machinery_type": None,
		"is_tractor": False,
		"requires_diesel_planning": False,
		"current_implement": None,
		"current_implement_name": None,
	}
	if not asset:
		return result

	machinery = frappe.db.get_value(
		"Machinery",
		{"asset": asset},
		["name", "machinery_type", "current_implement"],
		as_dict=True,
	)
	if not machinery:
		return result

	result["machinery"] = machinery.get("name")
	result["machinery_type"] = machinery.get("machinery_type")
	mt_norm = (machinery.get("machinery_type") or "").strip().lower()
	result["is_tractor"] = mt_norm == "tractor"
	result["requires_diesel_planning"] = mt_norm in DIESEL_PLANNING_MACHINERY_TYPES
	result["current_implement"] = machinery.get("current_implement") or None
	if result["current_implement"]:
		result["current_implement_name"] = (
			frappe.db.get_value("Implement", result["current_implement"], "implement_name")
			or result["current_implement"]
		)

	return result

@frappe.whitelist()
def create_transfer_tickets_for_schedule(schedule_name: str):
	"""Manually trigger transfer ticket creation for a schedule (equipment + input tickets)."""
	try:
		schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
		if schedule.status != "Scheduled":
			return {"success": False, "error": "Schedule status must be Scheduled"}
		# Always run equipment path first: it creates vehicle LTTs for inputs + hand/other tools and
		# machinery LTTs. Skipping when `_collect_equipment_assets()` is empty incorrectly skipped
		# input-only legs that still need the vehicle/consumables ticket in some setups.
		schedule._create_equipment_transfer_tickets()
		if schedule._collect_input_items():
			schedule._create_input_transfer_tickets()
		return {"success": True, "message": "Transfer ticket creation triggered"}
	except Exception as e:
		frappe.log_error(f"Error in create_transfer_tickets_for_schedule for {schedule_name}: {str(e)}", "Transfer Ticket")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_transfer_tickets_for_schedule(schedule_name: str) -> Dict[str, Any]:
	"""
	Get Logistics Transfer Tickets and Stock Entries linked to a schedule.
	Links are found by matching warehouse/field relationships, equipment assets, and destination location.
	Only returns tickets that contain ALL assets assigned to the schedule and match the destination location.
	"""
	if not schedule_name:
		return {"logistics_tickets": [], "stock_entries": []}
	
	try:
		schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
		if not schedule.field:
			return {"logistics_tickets": [], "stock_entries": []}
		
		# Get target warehouse for the schedule's field
		target_warehouse = schedule._get_target_warehouse_for_field(schedule.field)
		if not target_warehouse:
			return {"logistics_tickets": [], "stock_entries": []}
		
		# Get all equipment assets from the schedule
		schedule_assets = set(schedule._collect_equipment_assets())
		
		# If schedule has no equipment assets, try to return input-only tickets for pickable/receivable
		if not schedule_assets:
			from frappe.utils import add_to_date, now_datetime
			source_warehouse = schedule._get_source_warehouse_for_inputs()
			has_inputs = bool(schedule.get("inputs"))
			if has_inputs and target_warehouse:
				recent = add_to_date(now_datetime(), days=-7)
				filters = {
					"to_warehouse": target_warehouse,
					"status": ["!=", "Cancelled"],
					"creation": [">=", recent]
				}
				if source_warehouse:
					filters["from_warehouse"] = source_warehouse
				all_input_tickets = frappe.get_all(
					"Logistics Transfer Ticket",
					filters=filters,
					fields=["name", "status", "creation"],
					order_by="creation desc",
					limit=20
				)
				matched_tickets = []
				linked_stock_entries = []
				for t in all_input_tickets:
					try:
						td = frappe.get_doc("Logistics Transfer Ticket", t.name)
						if td.asset_items and len(td.asset_items) > 0:
							continue
						matched_tickets.append(t)
						if td.stock_entry:
							linked_stock_entries.append(td.stock_entry)
					except Exception:
						continue
				se_list = []
				if linked_stock_entries:
					se_list = frappe.get_all(
						"Stock Entry",
						filters={"name": ["in", linked_stock_entries], "docstatus": ["<", 2]},
						fields=["name", "docstatus", "posting_date", "posting_time"]
					)
				return {
					"logistics_tickets": [{"name": t.name, "status": t.status, "creation": t.creation} for t in matched_tickets],
					"stock_entries": [{"name": se.name, "docstatus": se.docstatus, "posting_date": se.posting_date} for se in se_list]
				}
			return {"logistics_tickets": [], "stock_entries": []}
		
		# Get the location for the target warehouse
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		target_location_result = get_location_for_warehouse(target_warehouse)
		target_location = target_location_result.get("location") if target_location_result else None
		
		# Find Logistics Transfer Tickets that have this target warehouse
		# and were created around the time of scheduling
		all_tickets = frappe.get_all(
			"Logistics Transfer Ticket",
			filters={
				"to_warehouse": target_warehouse,
				"status": ["!=", "Cancelled"]
			},
			fields=["name", "status", "creation", "to_location"],
			order_by="creation desc",
			limit=50  # Increased limit to allow filtering
		)
		
		# Filter tickets by matching assets and location
		matched_tickets = []
		for ticket in all_tickets:
			try:
				# Fetch full ticket document to get asset_items
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket.name)
				
				# Extract asset IDs from ticket's asset_items
				ticket_assets = set()
				if ticket_doc.asset_items:
					for asset_item in ticket_doc.asset_items:
						if asset_item.asset:
							ticket_assets.add(asset_item.asset)
				
				# Check if ticket contains ALL schedule assets (ticket assets must be superset or equal)
				assets_match = schedule_assets.issubset(ticket_assets)
				
				# Check location match
				ticket_location = ticket_doc.to_location
				location_match = True
				if target_location:
					# If schedule has a location, ticket must match it
					location_match = (ticket_location == target_location)
				# If schedule has no location, skip location matching (still filter by assets)
				
				# Only include tickets that match both conditions
				if assets_match and location_match:
					matched_tickets.append(ticket)
			except Exception as e:
				# Log error but continue processing other tickets
				frappe.log_error(f"Error processing ticket {ticket.name}: {str(e)}", "Equipment Filter")
				continue
		
		# Find Stock Entries (Material Transfer) that have this target warehouse
		# These are created as part of Logistics Transfer Tickets or separately
		stock_entries = frappe.get_all(
			"Stock Entry",
			filters={
				"purpose": "Material Transfer",
				"to_warehouse": target_warehouse,
				"docstatus": ["<", 2]  # Not cancelled
			},
			fields=["name", "docstatus", "posting_date", "posting_time"],
			order_by="posting_date desc, posting_time desc",
			limit=10
		)
		
		# Also check if stock entries are linked via Logistics Transfer Ticket
		linked_stock_entries = []
		for ticket in matched_tickets:
			try:
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket.name)
				if ticket_doc.stock_entry:
					linked_stock_entries.append(ticket_doc.stock_entry)
			except Exception:
				continue
		
		# Filter out stock entries that are already linked via tickets
		stock_entries = [se for se in stock_entries if se.name not in linked_stock_entries]
		
		return {
			"logistics_tickets": [{"name": t.name, "status": t.status, "creation": t.creation} for t in matched_tickets],
			"stock_entries": [{"name": se.name, "docstatus": se.docstatus, "posting_date": se.posting_date} for se in stock_entries]
		}
	except Exception as e:
		frappe.log_error(f"Error getting transfer tickets for schedule {schedule_name}: {str(e)}", "Transfer Ticket Lookup")
		return {"logistics_tickets": [], "stock_entries": []}


@frappe.whitelist()
def get_available_assets_for_cluster(
	field: str = None,
	block: str = None,
	asset_category: str = None,
	planned_start: str = None,
	planned_end: str = None,
	exclude_schedule: str = None
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
		exclude_schedule: Schedule name to exclude from conflict check (for updates)
	
	Returns:
		List of available assets with name, asset_name, location, etc.
	"""
	try:
		target_area = block or field
		if not target_area:
			frappe.log_error(f"get_available_assets_for_cluster: No field/block provided. field={field}, block={block}", "Asset Filter")
			return []

		frappe.log_error(
			f"get_available_assets_for_cluster: target_area={target_area}, asset_category={asset_category}",
			"Asset Filter Debug",
		)

		ctx = resolve_cluster_warehouses_locations(field, block)
		if not ctx:
			frappe.log_error(f"get_available_assets_for_cluster: No cluster/warehouses for {target_area}", "Asset Filter")
			return []

		cluster = ctx["cluster"]
		warehouse_list = ctx["warehouse_list"]
		location_list = ctx["location_list"]

		frappe.log_error(
			f"get_available_assets_for_cluster: Found cluster={cluster} for target_area={target_area}",
			"Asset Filter Debug",
		)
		frappe.log_error(
			f"get_available_assets_for_cluster: Found {len(warehouse_list)} warehouses in cluster {cluster}: {warehouse_list[:3]}",
			"Asset Filter Debug",
		)

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
		
		# Method 1b: If no assets yet, use same per-warehouse lookup as Equipments page (field 1, etc.)
		# so assets shown under a cluster warehouse in inventory appear in scheduling
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
			if assets:
				frappe.log_error(f"get_available_assets_for_cluster: Method 1b found {len(assets)} assets via get_assets_for_warehouse", "Asset Filter Debug")
		
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
					
					frappe.log_error(f"get_available_assets_for_cluster: Method 2 found {len(assets)} assets by location name pattern", "Asset Filter Debug")
		
		# Method 3: If still no assets, try pattern matching on location_name (contains cluster name)
		if not assets and cluster:
			try:
				# Get cluster area_name for pattern matching
				cluster_area_name = frappe.db.get_value("Geo Fencing Area", cluster, "area_name") or ""
				if cluster_area_name:
					# Get all locations whose location_name contains the cluster area_name
					all_locations = frappe.get_all(
						"Location",
						filters={"location_name": ["like", f"%{cluster_area_name}%"]},
						fields=["name", "location_name"],
						limit_page_length=0
					)
					
					if all_locations:
						matching_location_ids = [loc.name for loc in all_locations]
						asset_filters = [["location", "in", matching_location_ids], ["docstatus", "<", 2]]
						if asset_category:
							asset_filters.append(["asset_category", "like", f"%{asset_category}%"])
						
						assets = frappe.get_all(
							"Asset",
							fields=["name", "asset_name", "asset_category", "location", "status"],
							filters=asset_filters,
							limit=1000
						)
			except Exception as e:
				frappe.log_error(f"Error in Method 3 asset lookup: {str(e)[:100]}", "Asset Filter")
		
		# Method 4: Last resort - get all assets with category and check if their location maps to cluster warehouses
		if not assets and warehouse_list:
			try:
				# Get all assets with the category (draft and submitted)
				asset_filters = [["docstatus", "<", 2]]
				if asset_category:
					asset_filters.append(["asset_category", "like", f"%{asset_category}%"])
				
				all_assets = frappe.get_all(
					"Asset",
					fields=["name", "asset_name", "asset_category", "location", "status"],
					filters=asset_filters,
					limit=1000
				)
				
				# For each asset, check if its location maps to any warehouse in our cluster
				for asset in all_assets:
					if not asset.location:
						continue
					
					# Try to get warehouse from asset's location
					try:
						# Get location document
						location_doc = frappe.get_doc("Location", asset.location)
						location_name = location_doc.location_name or ""
						
						# Try to find a warehouse that maps to this location
						for wh in warehouse_list:
							try:
								wh_result = get_location_for_warehouse(wh)
								if wh_result and wh_result.get("location") == asset.location:
									assets.append(asset)
									break
							except Exception:
								continue
					except Exception:
						# If location lookup fails, skip this asset
						continue
				
				frappe.log_error(f"get_available_assets_for_cluster: Method 4 found {len(assets)} assets by reverse warehouse lookup", "Asset Filter Debug")
			except Exception as e:
				frappe.log_error(f"Error in Method 4 asset lookup: {str(e)[:100]}", "Asset Filter")
		
		frappe.log_error(f"get_available_assets_for_cluster: Final result: {len(assets)} assets found", "Asset Filter Debug")
		
		# Log detailed info if no assets found (for debugging)
		if not assets:
			category_info = f" category={asset_category}" if asset_category else ""
			loc_info = f" locations={location_list[:2]}" if location_list else " no locations"
			wh_info = f" warehouses={warehouse_list[:2]}" if warehouse_list else ""
			cluster_info = f" cluster={cluster}" if cluster else ""
			# Keep error log title short to avoid CharacterLengthExceededError (Error Log.title max is 140 chars)
			msg = f"get_available_assets_for_cluster: No assets found{cluster_info}{loc_info}{wh_info}{category_info}"
			frappe.log_error(msg[:130], "Asset Filter")
		
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
					
					# Exclude the current schedule if updating
					if exclude_schedule:
						conflicts_cps = [c for c in conflicts_cps if c.name != exclude_schedule]
					
					# If no conflicts, asset is available
					if not conflicts_cps and not conflicts_oda:
						available_assets.append(asset)
				except Exception as e:
					# Fail closed for time-slot filtering to avoid showing potentially booked assets.
					# Keep message short to avoid Error Log title length issues.
					frappe.log_error(
						f"Asset availability check failed for {asset_name}: {str(e)[:80]}",
						"Asset Filter"
					)
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
	exclude_schedule: str = None,
) -> List[Dict[str, Any]]:
	"""
	Implements physically at cluster-linked locations, plus implements on in-bound Logistics
	Transfer Tickets to the cluster with planned_drop_off_on <= planned_start.

	planned_end and exclude_schedule are accepted for API parity with asset availability; reserved for future use.
	"""
	_ = (planned_end, exclude_schedule)

	try:
		ctx = resolve_cluster_warehouses_locations(field, block)
		if not ctx:
			return []

		warehouse_list = ctx["warehouse_list"]
		location_list = ctx["location_list"] or []

		by_name: Dict[str, Dict[str, Any]] = {}

		if location_list:
			locs = tuple(location_list)
			rows = frappe.db.sql(
				"""
				SELECT DISTINCT i.name AS name, i.implement_name AS implement_name
				FROM `tabImplement` i
				LEFT JOIN `tabAsset` a ON a.name = i.asset AND a.docstatus < 2
				WHERE i.docstatus < 2
					AND (
						(i.asset IS NOT NULL AND i.asset != '' AND a.location IN %(locs)s)
						OR (IFNULL(i.asset, '') = '' AND i.location IN %(locs)s)
					)
				""",
				{"locs": locs},
				as_dict=True,
			)
			for r in rows or []:
				nm = r.get("name")
				if not nm:
					continue
				by_name[nm] = {
					"name": nm,
					"implement_name": (r.get("implement_name") or "").strip() or nm,
					"availability": "at_cluster",
				}

		# Same fallback as get_available_assets_for_cluster Method 1b: inventory uses
		# get_assets_for_warehouse (location + pattern fallbacks). Strict location_list
		# alone often misses assets when ERPNext Location ↔ Warehouse mapping is incomplete.
		from f2c.inventory.logistics_transfer_ticket_api import get_assets_for_warehouse

		for wh in warehouse_list:
			try:
				wh_result = get_assets_for_warehouse(wh)
				for a in wh_result.get("assets") or []:
					aname = a.get("name")
					if not aname:
						continue
					impl_row = frappe.db.get_value(
						"Implement",
						{"asset": aname},
						["name", "implement_name", "docstatus"],
						as_dict=True,
					)
					if not impl_row or int(impl_row.docstatus or 0) >= 2:
						continue
					nm = impl_row.name
					if nm in by_name:
						continue
					by_name[nm] = {
						"name": nm,
						"implement_name": (impl_row.implement_name or "").strip() or nm,
						"availability": "at_cluster",
					}
			except Exception:
				continue

		planned_start_dt = get_datetime(planned_start) if planned_start else None
		if planned_start_dt and warehouse_list:
			tickets = frappe.get_all(
				"Logistics Transfer Ticket",
				filters={
					"to_warehouse": ["in", warehouse_list],
					"status": ["in", ["Pending Pickup", "In Transit"]],
					"planned_drop_off_on": ["<=", planned_start_dt],
					"docstatus": ["<", 2],
				},
				fields=["name"],
				limit_page_length=500,
			)
			for t in tickets or []:
				try:
					doc = frappe.get_doc("Logistics Transfer Ticket", t.name)
				except Exception:
					continue
				if not doc.get("planned_drop_off_on"):
					continue
				pdo = str(doc.planned_drop_off_on)
				for row in doc.get("asset_items") or []:
					impl = row.get("paired_implement")
					if not impl and row.get("asset"):
						impl = frappe.db.get_value(
							"Implement", {"asset": row.asset}, "name", order_by="modified desc"
						)
					if not impl:
						continue
					meta = frappe.db.get_value("Implement", impl, ["implement_name", "docstatus"], as_dict=True)
					if not meta or int(meta.docstatus or 0) >= 2:
						continue
					if impl in by_name and by_name[impl].get("availability") == "at_cluster":
						continue
					if impl in by_name:
						continue
					by_name[impl] = {
						"name": impl,
						"implement_name": (meta.implement_name or "").strip() or impl,
						"availability": "in_transit",
						"planned_drop_off_on": pdo,
					}

		return list(by_name.values())
	except Exception as e:
		error_msg = str(e)[:100] if str(e) else "Unknown error"
		frappe.log_error(f"Error in get_available_implements_for_cluster: {error_msg}", "Implement Filter")
		return []


@frappe.whitelist()
def get_schedule_defaults(
	crop_plan: str,
	crop_plan_activity: str = "",
	activity_name: str = "",
	block_reference: str = "",
) -> Dict[str, Any]:
	"""
	Return activity fields and the approved input mix (farm_task) from the Crop Plan.

	Primary path: look up the Crop Plan Activity row by crop_plan_activity name.
	Fallback path: when crop_plan_activity is empty or a synthetic POP name (pop-X-Y),
	  find the activity by activity_name + block_reference inside the Crop Plan.

	This avoids using frappe.client.get_value from the browser (which can 403 due to parent permission checks).
	"""
	if not crop_plan:
		return {}

	# Ensure user can read the crop plan (basic guard)
	if not frappe.has_permission("Crop Plan", "read", crop_plan):
		frappe.throw("Not permitted", frappe.PermissionError)

	act = None

	# Primary path: look up real Crop Plan Activity row by name
	if crop_plan_activity:
		act = frappe.db.get_value(
			"Crop Plan Activity",
			crop_plan_activity,
			["parent", "parenttype", "parentfield", "sequence", "activity", "activity_name", "activity_group_type", "block_reference"],
			as_dict=True,
		)
		if act and (act.parent != crop_plan or act.parenttype != "Crop Plan" or act.parentfield != "activities"):
			frappe.throw("Invalid Crop Plan Activity selected.")
		if act and act.parent != crop_plan:
			act = None

	# Fallback path: find activity by activity_name + block_reference when crop_plan_activity is missing/synthetic
	if not act and activity_name:
		act_name_clean = activity_name.strip()
		block_ref_clean = str(block_reference or "").strip()
		filters = {
			"parent": crop_plan,
			"parenttype": "Crop Plan",
			"parentfield": "activities",
			"activity_name": act_name_clean,
		}
		if block_ref_clean:
			filters["block_reference"] = block_ref_clean
		act = frappe.db.get_value(
			"Crop Plan Activity",
			filters,
			["parent", "parenttype", "parentfield", "sequence", "activity", "activity_name", "activity_group_type", "block_reference"],
			as_dict=True,
		)

	if not act:
		return {}

	farm_task = ""
	task_name = ""

	# Primary: match by activity_reference
	input_row = frappe.db.get_value(
		"Crop Plan Activity Input",
		{
			"parent": crop_plan,
			"parenttype": "Crop Plan",
			"parentfield": "approved_inputs",
			"activity_reference": crop_plan_activity,
		},
		["farm_task", "task_name"],
		as_dict=True,
	)
	if input_row:
		farm_task = input_row.get("farm_task") or ""
		task_name = input_row.get("task_name") or ""

	# Fallback: match by activity_name
	if not farm_task and act.get("activity_name"):
		act_name = (act.activity_name or "").strip()
		act_block = act.get("block_reference")
		act_group = (act.get("activity_group_type") or "").strip()
		mix_filters_base = {
			"parent": crop_plan,
			"parenttype": "Crop Plan",
			"parentfield": "approved_inputs",
			"activity_name": act_name,
		}
		if act_group:
			mix_filters_base["activity_group_type"] = act_group
		if act_block is not None and str(act_block).strip() != "":
			input_row = frappe.db.get_value(
				"Crop Plan Activity Input",
				{**mix_filters_base, "block_reference": str(act_block).strip()},
				["farm_task", "task_name"],
				as_dict=True,
			)
			if input_row:
				farm_task = input_row.get("farm_task") or ""
				task_name = input_row.get("task_name") or ""
		if not farm_task:
			input_row = frappe.db.get_value(
				"Crop Plan Activity Input",
				mix_filters_base,
				["farm_task", "task_name"],
				as_dict=True,
			)
			if input_row:
				farm_task = input_row.get("farm_task") or ""
				task_name = input_row.get("task_name") or ""

	# No fallback to Farm Activity templates — only Crop Plan data is used.

	agt = (act.activity_group_type or "").lower()
	lbl = (act.activity_name or "").lower()
	is_spray = 1 if ("plant protection" in agt or "spray" in lbl) else 0

	return {
		"sequence": int(act.sequence or 0),
		"farm_activity": act.activity,
		"activity_name": act.activity_name or "",
		"is_spray": is_spray,
		"approved_input_mix": farm_task or "",
		"approved_input_mix_name": task_name or farm_task or "",
	}


@frappe.whitelist()
def get_approved_input_mix_by_activity_name(crop_plan: str, activity_name: str, activity_group_type: str = "") -> Dict[str, Any]:
	"""
	Return approved_input_mix (farm_task) and task_name for a Crop Plan activity by display name.
	Used when Activity Scheduling has activity_name but get_schedule_defaults returned no mix
	(e.g. crop_plan_activity row name not sent or mix stored only with activity_name).
	"""
	if not crop_plan or not (activity_name or "").strip():
		return {"approved_input_mix": "", "approved_input_mix_name": ""}
	if not frappe.has_permission("Crop Plan", "read", crop_plan):
		frappe.throw("Not permitted", frappe.PermissionError)
	act_name = (activity_name or "").strip()
	act_group = (activity_group_type or "").strip()
	filters = {
		"parent": crop_plan,
		"parenttype": "Crop Plan",
		"parentfield": "approved_inputs",
		"activity_name": act_name,
	}
	if act_group:
		filters["activity_group_type"] = act_group

	input_row = frappe.db.get_value(
		"Crop Plan Activity Input",
		filters,
		["farm_task", "task_name"],
		as_dict=True,
	)
	if not input_row or not input_row.get("farm_task"):
		return {"approved_input_mix": "", "approved_input_mix_name": ""}
	task_name = input_row.get("task_name") or ""
	if not task_name:
		try:
			task_name = frappe.db.get_value("Farm Tasks", input_row.get("farm_task"), "task_name") or ""
		except Exception:
			pass
	return {
		"approved_input_mix": input_row.get("farm_task") or "",
		"approved_input_mix_name": task_name or input_row.get("farm_task") or "",
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
	for input_row in doc.approved_inputs or []:
		if input_row.activity_reference and input_row.farm_task:
			activity_ref_to_farm_task[str(input_row.activity_reference)] = str(input_row.farm_task)

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
def get_approved_input_mix_and_items_from_crop_plan(
	crop_plan: str,
	activity_name: str = "",
	block_reference: str = "",
	crop_plan_activity: str = "",
) -> Dict[str, Any]:
	"""
	Return approved_input_mix, approved_input_mix_name, and items from the Crop Plan only.
	Use this first in Activity Scheduling so the actual Crop Plan data (e.g. 4SSPZN and its
	customized quantities) is shown, not the Farm Activity fallback (e.g. 1SS).
	"""
	result = {"approved_input_mix": "", "approved_input_mix_name": "", "items": []}
	if not crop_plan:
		return result
	if not frappe.has_permission("Crop Plan", "read", crop_plan):
		frappe.throw("Not permitted", frappe.PermissionError)
	base_filters = {
		"parent": crop_plan,
		"parenttype": "Crop Plan",
		"parentfield": "approved_input_mixes",
	}
	activity_name = (activity_name or "").strip()
	block_reference = (block_reference or "").strip()
	if crop_plan_activity and not activity_name:
		act = frappe.db.get_value(
			"Crop Plan Activity",
			crop_plan_activity,
			["activity_name", "block_reference"],
			as_dict=True,
		)
		if act:
			activity_name = (act.get("activity_name") or "").strip()
			if not block_reference:
				block_reference = str(act.get("block_reference") or "").strip()

	def _mix_row_and_items(extra_filters: dict) -> Tuple[Optional[Dict], List[Dict[str, Any]]]:
		rows = frappe.get_all(
			"Crop Plan Activity Input",
			filters={**base_filters, **extra_filters},
			fields=["farm_task", "task_name", "item", "item_name", "quantity", "unit"],
			order_by="idx asc",
		)
		if not rows:
			return None, []
		first_row = rows[0]
		if not first_row.get("farm_task"):
			return None, []
		
		mix_row = {
			"name": "flat-mix",
			"farm_task": first_row.farm_task,
			"task_name": first_row.task_name
		}
		
		# Now filter rows that share the same farm_task (since one extra_filter might match multiple tasks if activity_name is ambiguous)
		items = [
			{"item": r.item, "item_name": r.item_name, "rate_quantity": flt(r.quantity, 3), "unit": r.unit or "ml/L"}
			for r in rows if r.farm_task == first_row.farm_task
		]
		return mix_row, items

	# Try activity_reference, then activity_name+block_reference, then activity_name only
	if crop_plan_activity:
		mix_row, items = _mix_row_and_items({"activity_reference": crop_plan_activity})
		if mix_row and (mix_row.get("farm_task") or items):
			task_name = mix_row.get("task_name") or ""
			if not task_name:
				task_name = frappe.db.get_value("Farm Tasks", mix_row.get("farm_task"), "task_name") or ""
			result["approved_input_mix"] = mix_row.get("farm_task") or ""
			result["approved_input_mix_name"] = task_name or result["approved_input_mix"]
			result["items"] = items
			return result
	if activity_name and block_reference:
		mix_row, items = _mix_row_and_items({"activity_name": activity_name, "block_reference": block_reference})
		if mix_row and (mix_row.get("farm_task") or items):
			task_name = mix_row.get("task_name") or ""
			if not task_name:
				task_name = frappe.db.get_value("Farm Tasks", mix_row.get("farm_task"), "task_name") or ""
			result["approved_input_mix"] = mix_row.get("farm_task") or ""
			result["approved_input_mix_name"] = task_name or result["approved_input_mix"]
			result["items"] = items
			return result
	if activity_name:
		mix_row, items = _mix_row_and_items({"activity_name": activity_name})
		if mix_row and (mix_row.get("farm_task") or items):
			task_name = mix_row.get("task_name") or ""
			if not task_name:
				task_name = frappe.db.get_value("Farm Tasks", mix_row.get("farm_task"), "task_name") or ""
			result["approved_input_mix"] = mix_row.get("farm_task") or ""
			result["approved_input_mix_name"] = task_name or result["approved_input_mix"]
			result["items"] = items
			return result
	return result


@frappe.whitelist()
def get_crop_plan_approved_input_items(
	crop_plan: str,
	crop_plan_activity: str = "",
	farm_task: str = "",
	activity_name: str = "",
	block_reference: str = "",
) -> List[Dict[str, Any]]:
	"""
	Return approved input items directly from the Crop Plan doctype's nested child tables.

	Reads from:  Crop Plan  →  Crop Plan Approved Input Mix  →  Crop Plan Activity Input (approved_inputs)

	This is the ONLY source of truth — no fallback to Farm Tasks templates.

	Matching strategies (tried in order until a mix with items is found):
	  1. activity_reference == crop_plan_activity
	  2. activity_name  (+block_reference if available)
	  3. farm_task (approved_input_mix Link on the mix row)
	  4. If nothing else, return ALL approved_inputs across ALL mixes for this crop plan
	     that share the same activity_name (handles POP-injected synthetic names)
	"""
	if not crop_plan:
		return []

	if not frappe.has_permission("Crop Plan", "read", crop_plan):
		frappe.throw("Not permitted", frappe.PermissionError)

	base_filters = {
		"parent": crop_plan,
		"parenttype": "Crop Plan",
		"parentfield": "approved_inputs",
	}

	# Resolve activity_name and block_reference from the Crop Plan Activity row if not supplied
	if crop_plan_activity and not activity_name:
		act = frappe.db.get_value(
			"Crop Plan Activity",
			crop_plan_activity,
			["activity_name", "block_reference"],
			as_dict=True,
		)
		if act:
			activity_name = (act.get("activity_name") or "").strip()
			if not block_reference:
				block_reference = str(act.get("block_reference") or "").strip()

	def _try_filters(extra_filters: dict) -> List[Dict[str, Any]]:
		rows = frappe.get_all(
			"Crop Plan Activity Input",
			filters={**base_filters, **extra_filters},
			fields=["farm_task", "item", "item_name", "quantity", "unit"],
			order_by="idx asc",
		)
		if not rows:
			return []
		# Return rows matching the farm_task of the first row to keep it restricted to one mix.
		# Extract only the unique grouping to avoid repeating inputs if multiple activities share the same mix
		first_farm_task = rows[0].get("farm_task")
		if not first_farm_task:
			return []
		
		# Deduplicate to just one Mix definition (one instance of the items)
		seen_items = set()
		unique_items = []
		for r in rows:
			if r.farm_task == first_farm_task:
				key =f"{r.item}|{r.item_name}|{r.quantity}"
				if key not in seen_items:
					seen_items.add(key)
					unique_items.append({
						"item": r.item,
						"item_name": r.item_name,
						"rate_quantity": flt(r.quantity, 3),
						"unit": r.unit or "ml/L",
					})
		return unique_items

	# Strategy 1: exact match
	if crop_plan_activity:
		items = _try_filters({"activity_reference": crop_plan_activity})
		if items: return items
	
	if activity_name and block_reference and farm_task:
		items = _try_filters({"activity_name": activity_name, "block_reference": block_reference, "farm_task": farm_task})
		if items: return items

	if activity_name and farm_task:
		items = _try_filters({"activity_name": activity_name, "farm_task": farm_task})
		if items: return items

	if activity_name and block_reference:
		items = _try_filters({"activity_name": activity_name, "block_reference": block_reference})
		if items: return items

	if activity_name:
		items = _try_filters({"activity_name": activity_name})
		if items: return items

	if farm_task:
		items = _try_filters({"farm_task": farm_task})
		if items: return items

	if farm_task:
		try:
			ft_doc = frappe.get_doc("Farm Tasks", farm_task)
			items = []
			for row in ft_doc.items or []:
				items.append({
					"item": row.item,
					"item_name": row.item_name,
					"rate_quantity": flt(row.quantity, 3),
					"unit": row.unit or "ml/L",
				})
			if items:
				return items
		except Exception:
			pass

	return []


@frappe.whitelist()
def get_common_approved_input_mix(
	crop_plan: str,
	blocks_json: str,
	sequence: int | str,
	farm_activity: str,
) -> Dict[str, Any]:
	"""
	Find a common Farm Task (Approved Input Mix) for the selected blocks + activity.

	We match using the Crop Plan's `approved_inputs` child table which stores `activity_reference`
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
		for mix in doc.approved_inputs or []:
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
				"return_type": getattr(m, "return_type", None) or "Non Returnable",
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
				"return_type": getattr(imp, "return_type", None) or "Non Returnable",
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
				"return_type": getattr(ht, "return_type", None) or "Non Returnable",
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
				"return_type": getattr(ot, "return_type", None) or "Non Returnable",
			},
		)

	new_doc.insert(ignore_permissions=True)
	
	# Mark original schedule as "Rescheduled" unless it's already in a terminal state.
	# For Completed/Aborted schedules we keep the original status and only create a new entry.
	src.reload()
	if (src.status or "").strip() not in ("Completed", "Aborted"):
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


_SCHEDULE_EQUIPMENT_CHILD_TABLES = (
	"Crop Plan Schedule Machinery",
	"Crop Plan Schedule Implement",
	"Crop Plan Schedule Hand Tool",
	"Crop Plan Schedule Other Tool",
)


@frappe.whitelist()
def get_schedule_equipment_list(schedule_names) -> List[Dict[str, Any]]:
	"""
	Return equipment rows {parent, asset, asset_name} for Crop Plan Schedule documents.
	Aggregates machinery / implements / hand_tools / other_tools child tables (same shape as
	get_execution_equipment_list for list UI). Whitelisted so the frontend can avoid getDocList
	on child tables when permissions are tight.
	"""
	if not schedule_names:
		return []
	if isinstance(schedule_names, str):
		try:
			schedule_names = json.loads(schedule_names)
		except Exception:
			schedule_names = [schedule_names]
	names = [str(n).strip() for n in schedule_names if n and str(n).strip()]
	if not names:
		return []
	out: List[Dict[str, Any]] = []
	seen: Set[Tuple[str, str]] = set()
	for table in _SCHEDULE_EQUIPMENT_CHILD_TABLES:
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



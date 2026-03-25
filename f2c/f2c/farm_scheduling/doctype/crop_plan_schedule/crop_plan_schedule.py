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
		self._sync_approved_input_mix_from_plan()
		self._compute_totals()
		self._compute_labour_total()
		self._compute_water()
		self._validate_spray_requirements()
		self._recompute_input_totals_if_needed()
		# Validate equipment slot availability
		if self.status == "Scheduled" and self.planned_start and self.planned_end:
			self._validate_equipment_slot_availability()

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
		
		# Collect from machinery
		for m in self.get("machinery") or []:
			if m.asset:
				assets.add(m.asset)
		
		# Collect from implements
		for imp in self.get("implements") or []:
			if imp.asset:
				assets.add(imp.asset)
		
		# Collect from hand tools
		for ht in self.get("hand_tools") or []:
			if ht.asset:
				assets.add(ht.asset)
		
		# Collect from other tools
		for ot in self.get("other_tools") or []:
			if ot.asset:
				assets.add(ot.asset)
		
		return list(assets)

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

	def _create_equipment_transfer_tickets(self):
		"""Create Logistics Transfer Tickets for all equipment when schedule is saved with status Scheduled."""
		if self.status != "Scheduled":
			frappe.log_error(f"Schedule {self.name} status is not 'Scheduled' (current: {self.status}), skipping ticket creation", "Equipment Transfer Ticket")
			return
		
		if not self.field:
			frappe.log_error(f"Schedule {self.name} has no field specified, skipping ticket creation", "Equipment Transfer Ticket")
			return  # No field specified
		
		# Collect all equipment assets
		equipment_assets = self._collect_equipment_assets()
		if not equipment_assets:
			frappe.log_error(f"Schedule {self.name} has no equipment assets to transfer", "Equipment Transfer Ticket")
			# Explicitly set flag to False so _create_input_transfer_tickets knows inputs weren't included
			self._inputs_included_in_equipment_tickets = False
			return  # No equipment to transfer
		
		frappe.log_error(f"Creating transfer tickets for schedule {self.name} with {len(equipment_assets)} assets: {equipment_assets}", "Equipment Transfer Ticket")
		
		# Get target warehouse from field
		target_warehouse = self._get_target_warehouse_for_field(self.field)
		if not target_warehouse:
			error_msg = f"Cannot find target warehouse for field {self.field} in schedule {self.name}"
			frappe.log_error(error_msg, "Equipment Transfer Ticket")
			# Don't show error to user - just log it, allow schedule to be created
			return
		
		# Check if target warehouse has a location (required for asset transfer)
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
		try:
			target_location_result = get_location_for_warehouse(target_warehouse)
			if not target_location_result or not target_location_result.get("location"):
				error_msg = f"Target warehouse {target_warehouse} for field {self.field} has no mapped location. Please run Location sync (Geo Warehouses → Location) for the destination area."
				frappe.log_error(error_msg, "Equipment Transfer Ticket")
				# Don't block schedule creation - just log the error
				return
		except Exception as e:
			frappe.log_error(f"Error checking location for target warehouse {target_warehouse}: {str(e)}", "Equipment Transfer Ticket")
			return
		
		# Group assets by source warehouse
		assets_by_warehouse = self._group_assets_by_source_warehouse(equipment_assets)
		if not assets_by_warehouse:
			error_msg = f"Cannot find source warehouses for equipment assets in schedule {self.name}. Please ensure assets have locations mapped to warehouses."
			frappe.log_error(error_msg, "Equipment Transfer Ticket")
			frappe.msgprint(error_msg, indicator="orange", title="Transfer Ticket Creation Failed")
			return  # No valid assets with source warehouses
		
		# Get input items (to always include in equipment tickets as suggestions)
		input_items = self._collect_input_items()
		
		# Create transfer tickets for each source warehouse group
		from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket
		created_tickets = []
		errors = []
		# Initialize flag - preserve existing value if already set (from previous call)
		inputs_included_in_ticket = getattr(self, '_inputs_included_in_equipment_tickets', False)
		
		for from_warehouse, asset_list in assets_by_warehouse.items():
			if from_warehouse == target_warehouse:
				frappe.log_error(f"Asset(s) {asset_list} already at target warehouse {target_warehouse}, skipping", "Equipment Transfer Ticket")
				continue  # Skip if already at target
			
			# Check if a ticket already exists for this transfer (prevent duplicates)
			# Only check for very recent tickets (last 5 seconds) to catch true duplicates from rapid multiple saves
			from frappe.utils import add_to_date, now_datetime
			recent_time = add_to_date(now_datetime(), seconds=-5)
			
			existing_tickets = frappe.get_all(
				"Logistics Transfer Ticket",
				filters={
					"from_warehouse": from_warehouse,
					"to_warehouse": target_warehouse,
					"status": ["!=", "Cancelled"],
					"creation": [">=", recent_time]
				},
				fields=["name"],
				limit=10
			)
			
			# Check if any existing ticket has the same assets
			# Only consider tickets that have assets (not input-only tickets)
			ticket_exists = False
			for ticket_name in [t.name for t in existing_tickets]:
				try:
					ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
					# Only check tickets that have assets (skip input-only tickets)
					if ticket_doc.asset_items and len(ticket_doc.asset_items) > 0:
						ticket_assets = {ai.asset for ai in ticket_doc.asset_items if ai.asset}
						# Skip if no valid assets found
						if not ticket_assets:
							continue
						asset_set = set(asset_list)
						# If ticket has exactly the same assets, it's a duplicate
						if ticket_assets == asset_set:
							ticket_exists = True
							frappe.log_error(f"Duplicate equipment transfer ticket already exists for schedule {self.name}: {ticket_name} (same assets and warehouses), skipping", "Equipment Transfer Ticket")
							break
				except Exception:
					continue  # Skip if ticket can't be read
			
			if ticket_exists:
				continue  # Skip creating duplicate ticket
			
			# Always include inputs in equipment tickets (as suggestions, regardless of source warehouse)
			stock_items_for_ticket = input_items if input_items else None
			if stock_items_for_ticket:
				inputs_included_in_ticket = True
				frappe.log_error(f"Including {len(input_items)} input item(s) in equipment transfer ticket from {from_warehouse}", "Equipment Transfer Ticket")
			
			try:
				result = create_logistics_transfer_ticket(
					from_warehouse=from_warehouse,
					to_warehouse=target_warehouse,
					stock_items=stock_items_for_ticket,
					assets=asset_list
				)
				if result and result.get("ticket"):
					created_tickets.append(result.get("ticket"))
					ticket_type = "equipment and inputs" if stock_items_for_ticket else "equipment"
					frappe.log_error(f"Successfully created transfer ticket {result.get('ticket')} for {ticket_type} from {from_warehouse} to {target_warehouse}", "Equipment Transfer Ticket")
			except Exception as e:
				# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
				# Keep message very short to avoid nested error log references causing overflow
				error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
				error_msg = f"Transfer ticket error for {self.name}: {error_str}"
				frappe.log_error(error_msg, "Equipment Transfer Ticket")
				errors.append(f"Error creating transfer ticket from {from_warehouse} to {target_warehouse}: {str(e)}")
				continue
		
		# Store flag to indicate inputs were included (used by _create_input_transfer_tickets to skip if already included)
		self._inputs_included_in_equipment_tickets = inputs_included_in_ticket
		
		if created_tickets:
			ticket_type = "equipment and inputs" if inputs_included_in_ticket else "equipment"
			frappe.msgprint(f"Created {len(created_tickets)} transfer ticket(s) for {ticket_type}: {', '.join(created_tickets)}", indicator="green", title="Transfer Tickets Created")
		elif errors:
			frappe.msgprint("Failed to create transfer tickets. Please check Error Log for details.", indicator="red", title="Transfer Ticket Creation Failed")

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
		
		# Check if a ticket already exists for the same transfer (prevent duplicates)
		# Only check for very recent tickets (last 5 seconds) to catch true duplicates from rapid multiple saves
		# Look for input-only tickets (no assets) with same warehouses and items
		from frappe.utils import add_to_date, now_datetime
		recent_time = add_to_date(now_datetime(), seconds=-5)
		
		existing_tickets = frappe.get_all(
			"Logistics Transfer Ticket",
			filters={
				"from_warehouse": source_warehouse,
				"to_warehouse": target_warehouse,
				"status": ["!=", "Cancelled"],
				"creation": [">=", recent_time]
			},
			fields=["name"],
			limit=10
		)
		
		# Check if any existing ticket has the same stock items AND no assets (input-only ticket)
		for ticket_name in [t.name for t in existing_tickets]:
			try:
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
				# Only consider tickets with no assets (input-only tickets) as potential duplicates
				# Tickets with assets are from equipment transfer tickets and should be ignored
				if ticket_doc.asset_items and len(ticket_doc.asset_items) > 0:
					continue  # Skip tickets with assets - they're from equipment tickets
				
				# Check if stock items match
				if ticket_doc.stock_items and len(ticket_doc.stock_items) == len(input_items):
					# Compare items - check if all items match
					ticket_items = {(si.item_code, flt(si.qty)) for si in ticket_doc.stock_items if si.item_code}
					input_items_set = {(item.get("item_code"), flt(item.get("qty"))) for item in input_items if item.get("item_code")}
					
					if ticket_items == input_items_set:
						# Duplicate ticket found - skip creation
						frappe.log_error(f"Duplicate input transfer ticket already exists for schedule {self.name}: {ticket_name} (same items and warehouses), skipping", "Input Transfer Ticket")
						return
			except Exception:
				continue  # Skip if ticket can't be read
		
		# Create transfer ticket for input items
		from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket
		try:
			result = create_logistics_transfer_ticket(
				from_warehouse=source_warehouse,
				to_warehouse=target_warehouse,
				stock_items=input_items,
				assets=None
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
		
		# Create return transfer ticket
		from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket
		try:
			result = create_logistics_transfer_ticket(
				from_warehouse=field_warehouse,
				to_warehouse=cluster_warehouse,
				stock_items=None,
				assets=[{"asset": asset, "qty": 1} for asset in equipment_assets]
			)
			if result and result.get("ticket"):
				frappe.msgprint(f"Created return transfer ticket {result.get('ticket')} to return equipment to cluster", indicator="green", title="Return Transfer Ticket Created")
				frappe.log_error(f"Successfully created return transfer ticket {result.get('ticket')} for assets {equipment_assets} from {field_warehouse} to {cluster_warehouse}", "Return Transfer Ticket")
		except Exception as e:
			error_msg = f"Error creating return transfer ticket from {field_warehouse} to {cluster_warehouse} for schedule {self.name}: {str(e)}"
			frappe.log_error(error_msg, "Return Transfer Ticket")
			frappe.msgprint("Failed to create return transfer ticket. Please check Error Log for details.", indicator="red", title="Return Transfer Ticket Creation Failed")

	def after_insert(self):
		"""Create transfer tickets when schedule is first created with status Scheduled."""
		if self.status == "Scheduled":
			try:
				# Check if there are equipment assets
				current_assets = set(self._collect_equipment_assets())
				frappe.log_error(f"after_insert for {self.name}: {len(current_assets)} assets", "Transfer Ticket Debug")
				if current_assets:
					self._create_equipment_transfer_tickets()
				# Always create input tickets when schedule has inputs (duplicate check inside skips if already in equipment ticket)
				if self._collect_input_items():
					try:
						self._create_input_transfer_tickets()
					except Exception as inp_e:
						frappe.log_error(f"Input ticket error for {self.name}: {str(inp_e)[:60]}", "Input Transfer Ticket")
			except Exception as e:
				# Log error but don't block schedule creation
				# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
				# Keep message very short to avoid nested error log references causing overflow
				error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
				error_msg = f"Transfer ticket creation error for {self.name}: {error_str}"
				frappe.log_error(error_msg, "Transfer Ticket")
				# Don't raise - allow schedule to be created even if ticket creation fails

	def on_update(self):
		"""Create transfer tickets when schedule status changes to Scheduled or equipment is added.
		Create return transfer tickets when status changes to Completed."""
		# Use doc-before-save for old status; on_update runs after DB commit so get_value would return new value
		old_doc = self.get_doc_before_save() if not self.is_new() else None
		old_status = old_doc.get("status") if old_doc else None

		if not self.is_new():
			# Check if status changed to Completed - create return transfer tickets
			if (old_status or "") != "Completed" and self.status == "Completed":
				try:
					self._create_return_transfer_tickets()
				except Exception as e:
					# Log error but don't block schedule update
					# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
					# Keep message very short to avoid nested error log references causing overflow
					error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
					error_msg = f"Return transfer ticket error for {self.name}: {error_str}"
					frappe.log_error(error_msg, "Return Transfer Ticket")
					# Don't raise - allow schedule to be updated even if ticket creation fails
				return  # Don't process forward transfers if status is Completed
		
		# Only create forward transfer tickets if status is Scheduled
		if self.status != "Scheduled":
			return
		
		# Check if status changed to Scheduled (use old_status from doc-before-save, not DB)
		if not self.is_new():
			if (old_status or "") != "Scheduled":
				# Status just changed to Scheduled, create tickets
				try:
					# Check if there are equipment assets
					current_assets = set(self._collect_equipment_assets())
					# MARKER: CODE_VERSION_2026_01_23_v2
					frappe.log_error(f"🔧 NEW CODE RUNNING for {self.name}: Found {len(current_assets)} assets", "Transfer Ticket Debug")
					
					if current_assets:
						# If equipment exists, create equipment ticket (which may include inputs)
						frappe.log_error(f"✅ Creating equipment ticket for {self.name} (includes inputs)", "Transfer Ticket Debug")
						self._create_equipment_transfer_tickets()
					# Always create input tickets when schedule has inputs; duplicate check inside will skip if already in equipment ticket
					if self._collect_input_items():
						frappe.log_error(f"📦 Creating input ticket for {self.name} (approved inputs)", "Transfer Ticket Debug")
						try:
							self._create_input_transfer_tickets()
						except Exception as inp_e:
							err_str = str(inp_e)[:60] if len(str(inp_e)) > 60 else str(inp_e)
							frappe.log_error(f"Input ticket error for {self.name}: {err_str}", "Input Transfer Ticket")
				except Exception as e:
					# Log error but don't block schedule update
					# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
					# Keep message very short to avoid nested error log references causing overflow
					error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
					error_msg = f"Transfer ticket error for {self.name}: {error_str}"
					frappe.log_error(error_msg, "Transfer Ticket")
					# Don't raise - allow schedule to be updated even if ticket creation fails
			# If status was already Scheduled, check if equipment tickets need to be created
			# Check if tickets already exist for this schedule's equipment
			else:
				# Get current equipment assets
				current_assets = set(self._collect_equipment_assets())
				
				# If there are equipment assets, always try to create tickets
				# The _create_equipment_transfer_tickets method will handle duplicate prevention internally
				if current_assets:
					try:
						self._create_equipment_transfer_tickets()
					except Exception as e:
						# Log error but don't block schedule update
						# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
						# Keep message very short to avoid nested error log references causing overflow
						error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
						error_msg = f"Equipment transfer ticket error for {self.name}: {error_str}"
						frappe.log_error(error_msg, "Equipment Transfer Ticket")
						# Don't raise - allow schedule to be updated even if ticket creation fails
				# Always create input tickets when schedule has inputs (duplicate check inside skips if already in equipment ticket)
				if self._collect_input_items():
					try:
						self._create_input_transfer_tickets()
					except Exception as e:
						# Log error but don't block schedule update
						# Truncate error message to prevent CharacterLengthExceededError (max 140 chars for title)
						# Keep message very short to avoid nested error log references causing overflow
						error_str = str(e)[:60] if len(str(e)) > 60 else str(e)
						error_msg = f"Input transfer ticket error for {self.name}: {error_str}"
						frappe.log_error(error_msg, "Input Transfer Ticket")
						# Don't raise - allow schedule to be updated even if ticket creation fails


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
def create_transfer_tickets_for_schedule(schedule_name: str):
	"""Manually trigger transfer ticket creation for a schedule (equipment + input tickets)."""
	try:
		schedule = frappe.get_doc("Crop Plan Schedule", schedule_name)
		if schedule.status != "Scheduled":
			return {"success": False, "error": "Schedule status must be Scheduled"}
		if set(schedule._collect_equipment_assets()):
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
		# Get cluster from field or block
		target_area = block or field
		if not target_area:
			frappe.log_error(f"get_available_assets_for_cluster: No field/block provided. field={field}, block={block}", "Asset Filter")
			return []
		
		# Debug logging
		frappe.log_error(f"get_available_assets_for_cluster: target_area={target_area}, asset_category={asset_category}", "Asset Filter Debug")
		
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
		
		frappe.log_error(f"get_available_assets_for_cluster: Found cluster={cluster} for target_area={target_area}", "Asset Filter Debug")
		
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
		
		frappe.log_error(f"get_available_assets_for_cluster: Found {len(warehouse_list)} warehouses in cluster {cluster}: {warehouse_list[:3]}", "Asset Filter Debug")
		
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
				except Exception:
					# If conflict check fails, include the asset (better to show it than hide it)
					available_assets.append(asset)
			assets = available_assets
		
		return assets
	except Exception as e:
		# Catch any unexpected errors and return empty list
		# Log error but keep it short to avoid CharacterLengthExceededError
		error_msg = str(e)[:100] if str(e) else "Unknown error"
		frappe.log_error(f"Error in get_available_assets_for_cluster: {error_msg}", "Asset Filter")
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



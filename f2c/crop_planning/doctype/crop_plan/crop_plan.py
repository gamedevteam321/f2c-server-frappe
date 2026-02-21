# -*- coding: utf-8 -*-
# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import json
import frappe
from frappe.model.document import Document
from frappe.utils import getdate

# Conversion factor: 1 acre = 4046.86 square meters
SQ_METERS_TO_ACRES = 0.000247105

class CropPlan(Document):
	def autoname(self):
		"""
		Custom naming: CP-{field_name}-{date}
		Example: CP-Field1-2025-12-08
		"""
		if self.field and self.date:
			# Get the field name from Geo Fencing Area
			field_doc = frappe.get_doc("Geo Fencing Area", self.field)
			field_name = field_doc.area_name or self.field
			
			# Format date as YYYY-MM-DD
			date_str = getdate(self.date).strftime("%Y-%m-%d")
			
			# Create the name
			self.name = f"CP-{field_name}-{date_str}"
	
	def validate(self):
		"""Validate document and calculate total blocks"""
		self.calculate_field_area_acres()
		self.validate_blocks_belong_to_field()
		self.calculate_block_areas()
		self.sync_activities_to_mixes()

	def on_update(self):
		"""After save, re-persist nested approved_inputs for each approved_input_mix.

		Frappe's standard form save only sends first-level child table data.
		The nested approved_inputs (child-of-child) inside approved_input_mixes
		are NOT included in the standard save payload, so they can get orphaned
		or lost. This hook ensures they are preserved by re-reading them from the
		database after the parent save completes.

		Note: The dedicated ``update_approved_input_qty`` API is the recommended
		way to edit nested child table values from the frontend. This hook is a
		safety net for standard save operations.
		"""
		self._sync_approved_input_mixes_summary()
	
	def calculate_field_area_acres(self):
		"""Calculate field area in acres from square meters"""
		if self.field:
			field_doc = frappe.get_doc("Geo Fencing Area", self.field)
			if field_doc.area:
				# Convert square meters to acres
				self.field_area_acres = field_doc.area * SQ_METERS_TO_ACRES
	
	def calculate_total_blocks(self):
		"""Calculate total number of unique blocks (not crop rows)"""
		if not self.blocks:
			self.total_blocks = 0
			return
		
		# Count unique blocks by block reference
		unique_blocks = set()
		for block_row in self.blocks:
			if block_row.block:
				unique_blocks.add(block_row.block)
		
		self.total_blocks = len(unique_blocks)
	
	def validate_blocks_belong_to_field(self):
		"""Validate that all selected blocks belong to the selected field"""
		if not self.field or not self.blocks:
			return
		
		for block_row in self.blocks:
			if block_row.block:
				block_doc = frappe.get_doc("Geo Fencing Area", block_row.block)
				if block_doc.parent_area != self.field:
					frappe.throw(
						f"Block {block_row.block} does not belong to Field {self.field}. "
						f"Please select blocks that are children of the selected field."
					)
	
	def _sync_approved_input_mixes_summary(self):
		"""Rebuild items_summary for each approved_input_mix from DB-level nested rows."""
		if not self.approved_input_mixes:
			return
		for mix in self.approved_input_mixes:
			approved_inputs = frappe.get_all(
				"Crop Plan Activity Input",
				filters={
					"parent": mix.name,
					"parenttype": "Crop Plan Approved Input Mix",
					"parentfield": "approved_inputs"
				},
				fields=["item_name", "item", "quantity", "unit"],
				order_by="idx asc"
			)
			parts = []
			for inp in approved_inputs:
				name = inp.get("item_name") or inp.get("item") or "Item"
				qty = inp.get("quantity", "")
				u = inp.get("unit") or ""
				parts.append(f"{name}: {qty} {u}".strip())
			summary = " · ".join(parts)
			if mix.items_summary != summary:
				frappe.db.set_value(
					"Crop Plan Approved Input Mix", mix.name,
					"items_summary", summary, update_modified=False
				)

	def calculate_block_areas(self):
		"""Calculate block areas in acres from square meters and set field"""
		if not self.blocks:
			return
		
		for block_row in self.blocks:
			if block_row.block:
				block_doc = frappe.get_doc("Geo Fencing Area", block_row.block)
				if block_doc.area:
					# Convert square meters to acres
					block_row.block_area = block_doc.area * SQ_METERS_TO_ACRES
				# Set field from block's parent_area if not already set
				if block_doc.parent_area and not block_row.field:
					block_row.field = block_doc.parent_area
					# Get field name
					try:
						field_doc = frappe.get_doc("Geo Fencing Area", block_doc.parent_area)
						block_row.field_name = field_doc.area_name or block_doc.parent_area
					except:
						block_row.field_name = block_doc.parent_area

	def sync_activities_to_mixes(self):
		"""
		Synchronize 'approved_inputs' from activities table to 'approved_input_mixes' table.
		This ensures that Tank Mixes (which link to Farm Tasks) are correctly managed
		regardless of how the activity was added (manual, template, or API).
		"""
		if not self.activities:
			return

		# Map current activities by name and sequence
		# (We use both to handle multiple activities with the same name like "Spraying")
		activities_by_ref = {a.name: a for a in self.activities if a.name}
		activities_by_key = {f"{a.activity_name}_{a.block_reference}_{a.sequence}": a for a in self.activities}

		# Track which mixes we've processed/updated
		processed_mixes = []
		existing_mixes = self.get("approved_input_mixes") or []

		for act in self.activities:
			if not act.get("approved_inputs"):
				continue

			# Group inputs by farm_task (since one activity can have multiple tank mixes)
			inputs_by_task = {}
			for inp in act.approved_inputs:
				task = inp.get("farm_task") or "Default"
				if task not in inputs_by_task:
					inputs_by_task[task] = []
				inputs_by_task[task].append(inp)

			for task, inputs in inputs_by_task.items():
				if task == "Default": continue # Require a farm_task

				# Try to find an existing mix
				mix_row = None
				for m in existing_mixes:
					# Match by activity_reference first
					if m.activity_reference == act.name and m.farm_task == task:
						mix_row = m
						break
					# Fallback match by name + block + sequence
					if not m.activity_reference and m.activity_name == act.activity_name and \
					   str(m.block_reference) == str(act.block_reference) and m.sequence == act.sequence and \
					   m.farm_task == task:
						mix_row = m
						mix_row.activity_reference = act.name
						break

				if not mix_row:
					mix_row = self.append("approved_input_mixes", {
						"activity_reference": act.name,
						"activity_name": act.activity_name,
						"block_reference": act.block_reference,
						"sequence": act.sequence,
						"farm_task": task,
						"task_name": inputs[0].get("task_name")
					})
				
				# Deep sync nested inputs for this mix
				# Clear existing inputs to ensure we only have what's in the activity
				mix_row.set("approved_inputs", [])
				for inp_data in inputs:
					mix_row.append("approved_inputs", {
						"farm_task": inp_data.get("farm_task"),
						"task_name": inp_data.get("task_name"),
						"item": inp_data.get("item"),
						"item_name": inp_data.get("item_name"),
						"quantity": inp_data.get("quantity"),
						"unit": inp_data.get("unit")
					})
				
				# Regenerate summary for immediate display/save
				parts = []
				for inp in mix_row.approved_inputs:
					name = inp.get("item_name") or inp.get("item") or "Item"
					qty = inp.get("quantity", "")
					u = inp.get("unit") or ""
					parts.append(f"{name}: {qty} {u}".strip())
				mix_row.items_summary = " · ".join(parts)
				
				processed_mixes.append(mix_row)
		
		# Only replace mixes when at least one activity had approved_inputs to sync.
		# If nothing was processed, leave existing mixes untouched to avoid wiping
		# mixes that were saved via the explicit nested-save path in
		# create_or_update_crop_plan_with_activities.
		if processed_mixes:
			self.set("approved_input_mixes", processed_mixes)

@frappe.whitelist()
def load_pop_activities(crop_plan_name, block_idx, pop_name):
	"""
	Load activities from POP and create editable copies in the Crop Plan activities table
	
	Args:
		crop_plan_name: Name of the Crop Plan document
		block_idx: Index of the block row (1-based)
		pop_name: Name of the POP to load activities from
	
	Returns:
		List of activities created
	"""
	# Get the POP document
	pop_doc = frappe.get_doc("POP", pop_name)
	
	if not pop_doc.activities:
		frappe.throw(f"POP {pop_name} has no activities defined")
	
	# Get the Crop Plan document
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	
	# Remove only POP activities for this block (keep Land Preparation activities)
	activities_to_remove = []
	for activity in crop_plan_doc.activities:
		if activity.block_reference != str(block_idx):
			continue
		source = getattr(activity, "activity_source", None) or ""
		if source in (None, "", "POP"):
			activities_to_remove.append(activity)
	for activity in activities_to_remove:
		crop_plan_doc.remove(activity)
	
	# Create new activities from POP
	created_activities = []
	for pop_activity in pop_doc.activities:
		# Get the Farm Crop Activity Mapping details
		if pop_activity.pop_activity_list:
			activity_mapping = frappe.get_doc("Farm Crop Activity Mapping", pop_activity.pop_activity_list)
			
			# Create new activity row
			new_activity = crop_plan_doc.append("activities", {
				"block_reference": str(block_idx),
				"activity_source": "POP",
				"sequence": pop_activity.sequence or 0,
				"activity": activity_mapping.activity,
				"activity_name": activity_mapping.activity_name,
				"crop_stage": activity_mapping.crop_stage,
				"duration_after_stage": activity_mapping.duration_after_stage,
				"is_dat": activity_mapping.is_dat,
				"dat": activity_mapping.dat,
				"activity_group_type": activity_mapping.activity_group_type,
				"remarks": activity_mapping.remarks
			})
			
			# Load approved inputs if they exist
			if hasattr(activity_mapping, 'tasks_items') and activity_mapping.tasks_items:
				for task_item in activity_mapping.tasks_items:
					new_activity.append("approved_inputs", {
						"farm_task": task_item.farm_task,
						"task_name": task_item.task_name,
						"item": task_item.item,
						"item_name": task_item.item_name,
						"quantity": task_item.quantity,
						"unit": task_item.unit
					})
			
			created_activities.append({
				"activity_name": activity_mapping.activity_name,
				"sequence": pop_activity.sequence or 0
			})
	
	# Save the document (this creates Crop Plan Approved Input Mix rows via sync_activities_to_mixes)
	crop_plan_doc.save()
	frappe.db.commit()
	crop_plan_doc.reload()

	# Explicitly persist nested approved_inputs for each mix (Frappe does not save child-of-child automatically)
	for mix in crop_plan_doc.approved_input_mixes:
		if not mix.name:
			continue
		# Find the matching activity to get its approved_inputs
		matching_activity = None
		for act in crop_plan_doc.activities:
			if act.name == mix.activity_reference:
				matching_activity = act
				break
		if not matching_activity or not getattr(matching_activity, 'approved_inputs', None):
			continue
		# Filter inputs for this mix's farm_task
		inputs_for_mix = [
			inp for inp in matching_activity.approved_inputs
			if (inp.get('farm_task') or '') == (mix.farm_task or '')
		]
		if not inputs_for_mix:
			continue
		try:
			mix_doc = frappe.get_doc("Crop Plan Approved Input Mix", mix.name)
			mix_doc.approved_inputs = []
			for inp_data in inputs_for_mix:
				mix_doc.append("approved_inputs", {
					"farm_task": inp_data.get("farm_task") or '',
					"task_name": inp_data.get("task_name") or '',
					"item": inp_data.get("item") or '',
					"item_name": inp_data.get("item_name") or '',
					"quantity": inp_data.get("quantity") if inp_data.get("quantity") is not None else 0,
					"unit": inp_data.get("unit") or 'ml/L'
				})
			mix_doc.save()
			frappe.db.commit()
		except Exception as e:
			frappe.log_error(f"Error saving approved_inputs for mix {mix.name}: {str(e)}", "Load POP Activities Error")

	return created_activities


@frappe.whitelist()
def load_land_preparation_activities(crop_plan_name, block_idx, land_preparation_name):
	"""
	Load activities from Land Preparation and add them to the Crop Plan activities table for the given block.
	Only removes existing Land Preparation activities for this block; POP activities are kept.

	Args:
		crop_plan_name: Name of the Crop Plan document
		block_idx: Index of the block row (1-based)
		land_preparation_name: Name of the Land Preparation document

	Returns:
		List of activities created (each dict with activity_name, sequence)
	"""
	land_prep_doc = frappe.get_doc("Land Preparation", land_preparation_name)
	if not land_prep_doc.activities:
		frappe.throw(f"Land Preparation {land_preparation_name} has no activities defined")

	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)

	# Remove only Land Preparation activities for this block
	activities_to_remove = []
	for activity in crop_plan_doc.activities:
		if activity.block_reference == str(block_idx) and getattr(activity, "activity_source", None) == "Land Preparation":
			activities_to_remove.append(activity)
	for activity in activities_to_remove:
		crop_plan_doc.remove(activity)

	# Find max sequence among existing activities for this block (so LP activities come first or after existing)
	existing_sequences = [a.sequence or 0 for a in crop_plan_doc.activities if a.block_reference == str(block_idx)]
	start_sequence = (max(existing_sequences) + 1) if existing_sequences else 1

	created_activities = []
	for idx, lp_activity in enumerate(land_prep_doc.activities):
		new_activity = crop_plan_doc.append("activities", {
			"block_reference": str(block_idx),
			"activity_source": "Land Preparation",
			"sequence": start_sequence + idx,
			"activity": lp_activity.activity,
			"activity_name": lp_activity.get("activity_name") or (frappe.get_cached_value("Farm Activity", lp_activity.activity, "activity_name") if lp_activity.activity else ""),
			"activity_group_type": lp_activity.activity_group_type,
			"duration_before_transplantation": lp_activity.duration_before_transplantation,
			"remarks": lp_activity.remarks or ""
		})
		created_activities.append({
			"activity_name": new_activity.activity_name,
			"sequence": new_activity.sequence
		})

	crop_plan_doc.save()
	return created_activities


@frappe.whitelist()
def get_blocks_for_field(field_name):
	"""
	Get all blocks that belong to a specific field
	
	Args:
		field_name: Name of the Field (Geo Fencing Area)
	
	Returns:
		List of blocks with their details
	"""
	if not field_name:
		frappe.throw("Field name is required")
	
	# Verify the field exists and is of type Field
	try:
		field_doc = frappe.get_doc("Geo Fencing Area", field_name)
		if field_doc.geo_fencing_type != "Field":
			frappe.throw(f"Selected area '{field_name}' is not a Field. It is of type '{field_doc.geo_fencing_type}'")
	except frappe.DoesNotExistError:
		frappe.throw(f"Field '{field_name}' does not exist")
	except Exception as e:
		frappe.throw(f"Error validating field: {str(e)}")
	
	blocks = frappe.get_all(
		"Geo Fencing Area",
		filters={
			"parent_area": field_name,
			"geo_fencing_type": "Block"
		},
		fields=["name", "area_name", "area"],
		order_by="area_name asc"
	)
	
	# Get field information
	field_area = getattr(field_doc, 'area', None)
	field_area_acres = 0
	if field_area and field_area > 0:
		field_area_acres = field_area * SQ_METERS_TO_ACRES
	
	# Convert area to acres and add field information
	for block in blocks:
		if block.get('area'):
			block['area_acres'] = block['area'] * SQ_METERS_TO_ACRES
		else:
			block['area_acres'] = 0
		# Add field information
		block['field'] = field_name
		block['field_name'] = getattr(field_doc, 'area_name', None) or field_name
		block['field_area'] = field_area_acres
	
	return blocks

@frappe.whitelist()
def auto_populate_blocks(crop_plan_name):
	"""
	Automatically populate blocks table with all blocks from the selected field
	
	Args:
		crop_plan_name: Name of the Crop Plan document
	
	Returns:
		Number of blocks added
	"""
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	
	if not crop_plan_doc.field:
		frappe.throw("Please select a Field first")
	
	# Get all blocks for the field
	blocks = get_blocks_for_field(crop_plan_doc.field)
	
	if not blocks:
		frappe.msgprint(f"No blocks found for field {crop_plan_doc.field}")
		return 0
	
	# Clear existing blocks
	crop_plan_doc.blocks = []
	
	# Add all blocks to the table
	for block in blocks:
		# Get field from block's parent_area
		block_doc = frappe.get_doc("Geo Fencing Area", block['name'])
		field_name = block_doc.parent_area if block_doc.parent_area else crop_plan_doc.field
		field_display_name = ''
		if field_name:
			try:
				field_doc = frappe.get_doc("Geo Fencing Area", field_name)
				field_display_name = field_doc.area_name or field_name
			except:
				field_display_name = field_name
		
		crop_plan_doc.append("blocks", {
			"block": block['name'],
			"block_name": block['area_name'],
			"block_area": block['area_acres'],
			"field": field_name,
			"field_name": field_display_name
		})
	
	# Save the document
	crop_plan_doc.save()
	
	return len(blocks)

@frappe.whitelist()
def get_block_activities(crop_plan_name, block_idx):
	"""
	Get all activities for a specific block
	
	Args:
		crop_plan_name: Name of the Crop Plan document
		block_idx: Index of the block row (1-based)
	
	Returns:
		List of activities for the block
	"""
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	
	block_activities = []
	for activity in crop_plan_doc.activities:
		if activity.block_reference == str(block_idx):
			activity_dict = activity.as_dict()
			# Include approved inputs
			activity_dict['approved_inputs'] = []
			if hasattr(activity, 'approved_inputs'):
				for input_item in activity.approved_inputs:
					activity_dict['approved_inputs'].append(input_item.as_dict())
			block_activities.append(activity_dict)
	
	# Sort by sequence
	block_activities.sort(key=lambda x: x.get('sequence', 0))
	
	return block_activities

@frappe.whitelist()
def get_crop_plan_with_activities(crop_plan_name):
	"""
	Get Crop Plan with all activities and approved_input_mixes (with nested approved_inputs)
	
	Args:
		crop_plan_name: Name of the Crop Plan document
		
	Returns:
		Crop Plan document with activities and approved_input_mixes included
		Also converts approved_input_mixes back to approved_inputs in activities for frontend compatibility
	"""
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	
	# Convert to dict
	crop_plan_dict = crop_plan_doc.as_dict()
	
	# Process activities
	activities_list = []
	for activity in crop_plan_doc.activities:
		activity_dict = activity.as_dict()
		activity_dict['approved_inputs'] = []  # Initialize empty
		activities_list.append(activity_dict)
	
	# Process approved_input_mixes and convert back to approved_inputs in activities
	approved_input_mixes_list = []
	activity_inputs_map = {}  # Map activity_reference to list of approved_inputs
	
	# Manually load approved_input_mixes with their nested approved_inputs
	for mix in crop_plan_doc.approved_input_mixes:
		mix_dict = mix.as_dict()
		
		# Load approved_inputs directly from the mix's own sub-table (authoritative source).
		# Inputs are explicitly saved there by create_or_update_crop_plan_with_activities().
		mix_dict['approved_inputs'] = []
		try:
			approved_inputs = frappe.get_all(
				"Crop Plan Activity Input",
				filters={
					"parent": mix.name,
					"parenttype": "Crop Plan Approved Input Mix",
					"parentfield": "approved_inputs"
				},
				fields=["*"],
				order_by="idx asc"
			)
			for input_item in approved_inputs:
				mix_dict['approved_inputs'].append(input_item)
		except Exception as e:
			frappe.log_error(f"Error loading approved_inputs for mix {mix.name}: {str(e)}", "Crop Plan Load Error")
		
		approved_input_mixes_list.append(mix_dict)
		
		# Map activity_reference -> inputs so each mix is claimed by exactly one activity.
		activity_ref = mix_dict.get('activity_reference')
		if activity_ref:
			if activity_ref not in activity_inputs_map:
				activity_inputs_map[activity_ref] = []
			# Add all inputs from this mix to the activity
			activity_inputs_map[activity_ref].extend(mix_dict['approved_inputs'])
	
	# Track which mix names are already claimed via activity_reference so the
	# name-based fallback doesn't accidentally assign them to a second activity.
	claimed_mix_names = {mix_dict.get('name') for mix_dict in approved_input_mixes_list if mix_dict.get('activity_reference')}
	
	# Add approved_inputs back to activities for frontend compatibility
	# Match by activity_reference first, then by (activity_name + block_reference) so Crop Plan
	# approved input mixes show correctly in Activity Scheduling even when activity_reference differs.
	for activity_dict in activities_list:
		activity_name = activity_dict.get('name')
		if activity_name and activity_name in activity_inputs_map:
			activity_dict['approved_inputs'] = activity_inputs_map[activity_name]
		else:
			# Fallback: match mix by activity_name and block_reference.
			# Skip mixes already claimed by activity_reference to avoid collisions between
			# two activities with the same name on the same block (e.g. two Sprayings).
			act_display_name = (activity_dict.get('activity_name') or '').strip()
			act_block_ref = str(activity_dict.get('block_reference') or '')
			for mix_dict in approved_input_mixes_list:
				if mix_dict.get('name') in claimed_mix_names:
					continue  # already matched via activity_reference
				mix_act_name = (mix_dict.get('activity_name') or '').strip()
				mix_block_ref = str(mix_dict.get('block_reference') or '')
				# Match by activity name and block (or any block if mix has no block_reference)
				if act_display_name and mix_act_name == act_display_name and (mix_block_ref == act_block_ref or mix_block_ref == ''):
					activity_dict['approved_inputs'] = mix_dict.get('approved_inputs') or []
					break
	
	crop_plan_dict['activities'] = activities_list
	crop_plan_dict['approved_input_mixes'] = approved_input_mixes_list
	
	# Process blocks: group by block reference to support multiple crops per block
	# Backend stores multiple block rows (one per crop), frontend expects blocks with crop_configs array
	blocks_dict = {}  # Key: block reference (block field), Value: list of block rows
	
	for block_row in crop_plan_doc.blocks:
		block_ref = block_row.block
		if block_ref not in blocks_dict:
			# Get field area if field is set
			field_area = 0
			field_name = getattr(block_row, 'field_name', None) or ''
			field_id = getattr(block_row, 'field', None) or ''
			if field_id:
				try:
					field_doc = frappe.get_doc("Geo Fencing Area", field_id)
					if field_doc.area:
						field_area = field_doc.area * SQ_METERS_TO_ACRES
					if not field_name:
						field_name = field_doc.area_name or field_id
				except:
					pass
			
			blocks_dict[block_ref] = {
				'block': block_row.block,
				'block_name': block_row.block_name,
				'block_area': block_row.block_area,
				'field': field_id,
				'field_name': field_name,
				'field_area': field_area,
				'land_preparation': getattr(block_row, 'land_preparation', None) or '',
				'land_preparation_name': getattr(block_row, 'land_preparation_name', None) or '',
				'land_preparation_activities_override': getattr(block_row, 'land_preparation_activities_override', None) or '',
				'template_show_land_prep': 1 if getattr(block_row, 'template_show_land_prep', None) else 0,
				'template_show_pop': 1 if getattr(block_row, 'template_show_pop', None) else 0,
				'name': block_row.name if hasattr(block_row, 'name') else None,
				'crop_configs': []
			}
		
		# Add this crop as a crop_config
		if block_row.crop:  # Only add if crop is set
			blocks_dict[block_ref]['crop_configs'].append({
				'crop': block_row.crop,
				'variety': getattr(block_row, 'variety', None) or '',
				'pop': block_row.pop or '',
				'pop_name': block_row.pop_name or '',
				'spacing': block_row.spacing or '',
				'no_of_seedlings': block_row.no_of_seedlings or 0,
				'irrigation_type': block_row.irrigation_type or ''
			})
	
	# Convert to list and preserve order
	processed_blocks = []
	# Use original order from crop_plan_doc.blocks to maintain order
	seen_blocks = set()
	for block_row in crop_plan_doc.blocks:
		block_ref = block_row.block
		if block_ref not in seen_blocks:
			seen_blocks.add(block_ref)
			if block_ref in blocks_dict:
				processed_blocks.append(blocks_dict[block_ref])
	
	# Inject Land Preparation activities for blocks that have land_preparation but no LP activities in the plan
	# (so Activity Scheduling shows 6 LP + 3 POP when both templates are selected, even if LP was never saved)
	for idx, block_info in enumerate(processed_blocks):
		block_ref_str = str(idx + 1)
		land_prep = block_info.get('land_preparation') or ''
		if not land_prep:
			continue
		override_raw = (block_info.get('land_preparation_activities_override') or '').strip()
		# A block already has LP activities when ANY activity_source='Land Preparation' exists,
		# OR when there are activities that clearly came from LP (activity_source may be blank
		# for activities created before the field was tracked).
		has_lp = any(
			(str(a.get('block_reference') or '') == block_ref_str and (a.get('activity_source') or '') == 'Land Preparation')
			for a in activities_list
		)
		if override_raw:
			try:
				override_list = json.loads(override_raw)
				if isinstance(override_list, list):
					existing_seqs = [a.get('sequence') or 0 for a in activities_list if str(a.get('block_reference') or '') == block_ref_str]
					max_seq = max(existing_seqs, default=0)
					for i, act in enumerate(override_list):
						crop_plan_doc.append("activities", {
							'block_reference': block_ref_str,
							'activity_source': 'Land Preparation',
							'sequence': act.get('sequence', max_seq + i + 1),
							'activity': act.get('activity') or '',
							'activity_name': act.get('activity_name') or '',
							'activity_group_type': act.get('activity_group_type') or '',
							'duration_before_transplantation': act.get('duration_before_transplantation'),
							'remarks': act.get('remarks') or '',
							'approved_inputs': act.get('approved_inputs') or []
						})
					needs_save = True
			except Exception:
				pass
		elif not has_lp:
			try:
				lp_doc = frappe.get_doc("Land Preparation", land_prep)
				if lp_doc.activities:
					existing_seqs = [a.get('sequence') or 0 for a in activities_list if str(a.get('block_reference') or '') == block_ref_str]
					existing_max_seq = max(existing_seqs, default=0)
					for i, lp_act in enumerate(lp_doc.activities):
						activity_name = lp_act.get('activity_name') or (
							frappe.get_cached_value("Farm Activity", lp_act.activity, "activity_name") if lp_act.activity else ''
						)
						crop_plan_doc.append("activities", {
							'block_reference': block_ref_str,
							'activity_source': 'Land Preparation',
							'sequence': existing_max_seq + i + 1,
							'activity': lp_act.activity or '',
							'activity_name': activity_name,
							'activity_group_type': lp_act.activity_group_type or '',
							'duration_before_transplantation': lp_act.duration_before_transplantation,
							'remarks': lp_act.remarks or '',
							'approved_inputs': []
						})
					needs_save = True
			except Exception:
				pass
	
	# After Land Prep injection, check if we need to refresh the activities_list for POP injection
	if 'needs_save' in locals() and needs_save:
		crop_plan_doc.save(ignore_permissions=True)
		frappe.db.commit()
		crop_plan_doc.reload()
		# Refresh activities_list so has_pop check is accurate
		activities_list = []
		for activity in crop_plan_doc.activities:
			activity_dict = activity.as_dict()
			activity_dict['approved_inputs'] = []
			activities_list.append(activity_dict)
		needs_save = False

	# Inject POP activities for blocks that have template_show_pop and a POP selected but no POP activities in the plan
	# (so Activity Scheduling shows POP activities even if user never opened activities modal or saved after selecting POP)
	for idx, block_info in enumerate(processed_blocks):
		block_ref_str = str(idx + 1)
		template_show_pop = block_info.get('template_show_pop')
		if not template_show_pop:
			continue
		# Get POP name from first crop_config that has pop set
		pop_name = ''
		for cc in (block_info.get('crop_configs') or []):
			pop_name = (cc.get('pop') or '').strip()
			if pop_name:
				break
		if not pop_name:
			continue
		# Check if POP activities were already explicitly injected for this block.
		# Only activities with activity_source='POP' count — activities created via
		# create_or_update_crop_plan_with_activities don't set activity_source, so they
		# need POP injection to get their proper template data.
		has_pop = any(
			(str(a.get('block_reference') or '') == block_ref_str and (a.get('activity_source') or '') == 'POP')
			for a in activities_list
		)
		if has_pop:
			continue
		try:
			pop_doc = frappe.get_doc("POP", pop_name)
			if not pop_doc.activities:
				continue
			existing_seqs = [a.get('sequence') or 0 for a in activities_list if str(a.get('block_reference') or '') == block_ref_str]
			existing_max_seq = max(existing_seqs, default=0)
			for i, pop_activity in enumerate(pop_doc.activities):
				if not getattr(pop_activity, 'pop_activity_list', None):
					continue
				activity_mapping = frappe.get_doc("Farm Crop Activity Mapping", pop_activity.pop_activity_list)

				# Include approved_inputs from Farm Crop Activity Mapping so injected POP
				# activities carry the same tank-mix data the POP template defines.
				pop_approved_inputs = []
				if hasattr(activity_mapping, 'tasks_items') and activity_mapping.tasks_items:
					for task_item in activity_mapping.tasks_items:
						pop_approved_inputs.append({
							'farm_task': task_item.farm_task or '',
							'task_name': task_item.task_name or '',
							'item': task_item.item or '',
							'item_name': task_item.item_name or '',
							'quantity': task_item.quantity if task_item.quantity is not None else 0,
							'unit': task_item.unit or 'ml/L'
						})

				crop_plan_doc.append("activities", {
					'block_reference': block_ref_str,
					'activity_source': 'POP',
					'sequence': existing_max_seq + i + 1,
					'activity': activity_mapping.activity or '',
					'activity_name': activity_mapping.activity_name or '',
					'activity_group_type': getattr(activity_mapping, 'activity_group_type', None) or '',
					'remarks': getattr(activity_mapping, 'remarks', None) or '',
					'approved_inputs': pop_approved_inputs
				})
				needs_save = True
		except Exception:
			pass
	
	# Final save for POP injection
	if 'needs_save' in locals() and needs_save:
		crop_plan_doc.save(ignore_permissions=True)
		frappe.db.commit()
		crop_plan_doc.reload()
	
	# Now convert the final saved document to the expected dict structure
	crop_plan_dict = crop_plan_doc.as_dict()
	
	# Process activities - refresh from reloaded doc
	activities_list = []
	for activity in crop_plan_doc.activities:
		activity_dict = activity.as_dict()
		activity_dict['approved_inputs'] = []  # Initialize empty
		activities_list.append(activity_dict)
	
	# Process approved_input_mixes and convert back to approved_inputs in activities
	approved_input_mixes_list = []
	activity_inputs_map = {}  # Map activity_reference to list of approved_inputs
	
	# Manually load approved_input_mixes with their nested approved_inputs
	for mix in crop_plan_doc.approved_input_mixes:
		mix_dict = mix.as_dict()
		
		# Load approved_inputs directly from the mix's own sub-table (authoritative source).
		# Inputs are explicitly saved there by create_or_update_crop_plan_with_activities().
		mix_dict['approved_inputs'] = []
		try:
			approved_inputs = frappe.get_all(
				"Crop Plan Activity Input",
				filters={
					"parent": mix.name,
					"parenttype": "Crop Plan Approved Input Mix",
					"parentfield": "approved_inputs"
				},
				fields=["*"],
				order_by="idx asc"
			)
			for input_item in approved_inputs:
				mix_dict['approved_inputs'].append(input_item)
		except Exception as e:
			frappe.log_error(f"Error loading approved_inputs for mix {mix.name}: {str(e)}", "Crop Plan Load Error")
		
		approved_input_mixes_list.append(mix_dict)
		
		# Map activity_reference -> inputs so each mix is claimed by exactly one activity.
		activity_ref = mix_dict.get('activity_reference')
		if activity_ref:
			if activity_ref not in activity_inputs_map:
				activity_inputs_map[activity_ref] = []
			# Add all inputs from this mix to the activity
			activity_inputs_map[activity_ref].extend(mix_dict['approved_inputs'])
	
	# Track which mix names are already claimed via activity_reference so the
	# name-based fallback doesn't accidentally assign them to a second activity.
	claimed_mix_names = {mix_dict.get('name') for mix_dict in approved_input_mixes_list if mix_dict.get('activity_reference')}
	
	# Add approved_inputs back to activities for frontend compatibility
	for activity_dict in activities_list:
		activity_name = activity_dict.get('name')
		if activity_name and activity_name in activity_inputs_map:
			activity_dict['approved_inputs'] = activity_inputs_map[activity_name]
		else:
			# Fallback: match mix by activity_name, block_reference, AND sequence.
			# Skip mixes already claimed by activity_reference to avoid collisions between
			# two activities with the same name on the same block (e.g. two Sprayings).
			act_display_name = (activity_dict.get('activity_name') or '').strip()
			act_block_ref = str(activity_dict.get('block_reference') or '')
			act_sequence = activity_dict.get('sequence')
			for mix_dict in approved_input_mixes_list:
				if mix_dict.get('name') in claimed_mix_names:
					continue  # already matched via activity_reference
				mix_act_name = (mix_dict.get('activity_name') or '').strip()
				mix_block_ref = str(mix_dict.get('block_reference') or '')
				mix_sequence = mix_dict.get('sequence')
				# Match by name, block, and sequence to handle duplicates (e.g. multiple Sprayings)
				if act_display_name and mix_act_name == act_display_name and (mix_block_ref == act_block_ref or mix_block_ref == '') and mix_sequence == act_sequence:
					activity_dict['approved_inputs'] = mix_dict.get('approved_inputs') or []
					break
	
	crop_plan_dict['activities'] = activities_list
	crop_plan_dict['approved_input_mixes'] = approved_input_mixes_list
	
	# Process blocks: group by block reference to support multiple crops per block
	# Backend stores multiple block rows (one per crop), frontend expects blocks with crop_configs array
	blocks_dict = {}  # Key: block reference (block field), Value: list of block rows
	
	for block_row in crop_plan_doc.blocks:
		block_ref = block_row.block
		if block_ref not in blocks_dict:
			# Get field area if field is set
			field_area = 0
			field_name = getattr(block_row, 'field_name', None) or ''
			field_id = getattr(block_row, 'field', None) or ''
			if field_id:
				try:
					field_doc = frappe.get_doc("Geo Fencing Area", field_id)
					if field_doc.area:
						field_area = field_doc.area * SQ_METERS_TO_ACRES
					if not field_name:
						field_name = field_doc.area_name or field_id
				except:
					pass
			
			blocks_dict[block_ref] = {
				'block': block_row.block,
				'block_name': block_row.block_name,
				'block_area': block_row.block_area,
				'field': field_id,
				'field_name': field_name,
				'field_area': field_area,
				'land_preparation': getattr(block_row, 'land_preparation', None) or '',
				'land_preparation_name': getattr(block_row, 'land_preparation_name', None) or '',
				'land_preparation_activities_override': getattr(block_row, 'land_preparation_activities_override', None) or '',
				'template_show_land_prep': 1 if getattr(block_row, 'template_show_land_prep', None) else 0,
				'template_show_pop': 1 if getattr(block_row, 'template_show_pop', None) else 0,
				'name': block_row.name if hasattr(block_row, 'name') else None,
				'crop_configs': []
			}
		
		# Add this crop as a crop_config
		if block_row.crop:  # Only add if crop is set
			blocks_dict[block_ref]['crop_configs'].append({
				'crop': block_row.crop,
				'variety': getattr(block_row, 'variety', None) or '',
				'pop': block_row.pop or '',
				'pop_name': block_row.pop_name or '',
				'spacing': block_row.spacing or '',
				'no_of_seedlings': block_row.no_of_seedlings or 0,
				'irrigation_type': block_row.irrigation_type or ''
			})
	
	# Convert to list and preserve order
	processed_blocks = []
	# Use original order from crop_plan_doc.blocks to maintain order
	seen_blocks = set()
	for block_row in crop_plan_doc.blocks:
		block_ref = block_row.block
		if block_ref not in seen_blocks:
			seen_blocks.add(block_ref)
			if block_ref in blocks_dict:
				processed_blocks.append(blocks_dict[block_ref])
	
	crop_plan_dict['blocks'] = processed_blocks
	crop_plan_dict['total_blocks'] = len(processed_blocks)  # Count unique blocks, not crop rows
	
	return crop_plan_dict

@frappe.whitelist()
def create_or_update_crop_plan_with_activities(crop_plan_data):
	"""
	Create or update Crop Plan with activities and approved_input_mixes (with nested approved_inputs)
	
	This function ensures nested child tables are saved correctly by:
	1. Saving activities first to get their names
	2. Converting approved_inputs from activities to approved_input_mixes
	3. Saving approved_input_mixes to get their names
	4. Explicitly iterating and saving approved_inputs within each approved_input_mix
	
	Args:
		crop_plan_data: JSON string or dict containing Crop Plan data with activities and approved_inputs
		
	Returns:
		Created/updated Crop Plan document name
	"""
	import json
	import traceback
	
	# Start transaction
	frappe.db.begin()
	
	try:
		# Parse crop_plan_data if it's a string
		if isinstance(crop_plan_data, str):
			crop_plan_data = json.loads(crop_plan_data)
		
		# Log received data for debugging
		frappe.log_error(f"Received crop_plan_data with {len(crop_plan_data.get('activities', []))} activities", "Crop Plan Debug")
		if crop_plan_data.get('activities'):
			total_inputs = sum(len(act.get('approved_inputs', [])) for act in crop_plan_data.get('activities', []))
			frappe.log_error(f"Total approved_inputs in received data: {total_inputs}", "Crop Plan Debug")
		
		# Extract activities, blocks, and approved_input_mixes data (make copies to avoid modifying original)
		activities_data = list(crop_plan_data.get('activities', []))
		blocks_data = list(crop_plan_data.get('blocks', []))
		# Use a sentinel to distinguish "key not sent" (new plan) from "key sent as []" (editing with no mixes).
		_MISSING = object()
		_raw_mixes = crop_plan_data.get('approved_input_mixes', _MISSING)
		approved_input_mixes_data = list(_raw_mixes) if _raw_mixes is not _MISSING else []
		# True when the frontend explicitly sent the approved_input_mixes key (even as an empty list).
		# Used below to decide whether to clear existing mixes from the DB.
		approved_input_mixes_explicitly_sent = _raw_mixes is not _MISSING
		crop_plan_name = crop_plan_data.get('name')
		
		# Create a copy of crop_plan_data without child tables for the main document
		main_doc_data = {k: v for k, v in crop_plan_data.items() if k not in ['activities', 'blocks', 'approved_input_mixes', 'name']}
		
		# Create or update Crop Plan document
		if crop_plan_name:
			# Update existing Crop Plan
			crop_plan_doc = frappe.get_doc('Crop Plan', crop_plan_name)
			# Update main fields (excluding child tables)
			for key, value in main_doc_data.items():
				if key not in ['doctype', 'name']:
					setattr(crop_plan_doc, key, value)
		else:
			# Create new Crop Plan (without child tables in constructor)
			crop_plan_doc = frappe.get_doc({
				'doctype': 'Crop Plan',
				**{k: v for k, v in main_doc_data.items() if k not in ['doctype', 'name']}
			})
		
		# Clear blocks; sync activities by name when updating so activity_reference in mixes stays valid.
		# Do NOT clear approved_input_mixes here — the dedicated clear block after the first save+reload
		# handles that to avoid destroying existing mix data before new mixes are built.
		existing_activity_names = {a.name for a in crop_plan_doc.activities} if crop_plan_doc.activities else set()
		request_activity_names = {a.get('name') for a in activities_data if a.get('name')}
		preserve_activity_names = bool(crop_plan_name and request_activity_names and existing_activity_names)
		if not preserve_activity_names:
			crop_plan_doc.set('activities', [])
		crop_plan_doc.set('blocks', [])
		
		frappe.log_error(f"Processing {len(activities_data)} activities, {len(blocks_data)} blocks, {len(approved_input_mixes_data)} approved_input_mixes, preserve_activity_names={preserve_activity_names}", "Crop Plan Debug")
		
		# Process blocks first
		for block_data in blocks_data:
			# Create a clean copy of block_data
			block_data_clean = block_data.copy()
			
			# Remove 'name' field for new blocks (when updating, we want to create new rows)
			# This prevents issues with stale references
			block_data_clean.pop('name', None)
			block_data_clean.pop('parent', None)
			block_data_clean.pop('parentfield', None)
			block_data_clean.pop('parenttype', None)
			block_data_clean.pop('owner', None)
			block_data_clean.pop('creation', None)
			block_data_clean.pop('modified', None)
			block_data_clean.pop('modified_by', None)
			block_data_clean.pop('docstatus', None)
			block_data_clean.pop('idx', None)
			
			# Explicitly set doctype for block
			block_data_clean['doctype'] = 'Crop Plan Block'
			crop_plan_doc.append('blocks', block_data_clean)
		
		# Recalculate total_blocks after adding blocks
		crop_plan_doc.calculate_total_blocks()
		
		# Process activities (without approved_inputs - they'll be in approved_input_mixes)
		if preserve_activity_names:
			# Update existing rows by name so activity_reference in approved_input_mixes stays valid
			existing_by_name = {a.name: a for a in crop_plan_doc.activities}
			new_activities = []
			for activity_data in activities_data:
				activity_data_copy = activity_data.copy()
				activity_data_copy.pop('approved_inputs', None)
				activity_data_copy.pop('crop_stage_name', None)
				activity_data_copy.pop('activity_group_type_name', None)
				act_name = activity_data_copy.get('name')
				if act_name and act_name in existing_by_name:
					existing_row = existing_by_name[act_name]
					for k, v in activity_data_copy.items():
						if k not in ('doctype', 'name', 'parent', 'parentfield', 'parenttype', 'idx', 'owner', 'creation', 'modified', 'modified_by', 'docstatus'):
							setattr(existing_row, k, v)
					new_activities.append(existing_row)
				else:
					activity_data_copy.pop('name', None)
					activity_data_copy['doctype'] = 'Crop Plan Activity'
					crop_plan_doc.append('activities', activity_data_copy)
					new_activities.append(crop_plan_doc.activities[-1])
			crop_plan_doc.activities[:] = new_activities
		else:
			for idx, activity_data in enumerate(activities_data):
				activity_data_copy = activity_data.copy()
				activity_data_copy.pop('approved_inputs', None)
				if not crop_plan_name:
					activity_data_copy.pop('name', None)
				activity_data_copy.pop('crop_stage_name', None)
				activity_data_copy.pop('activity_group_type_name', None)
				activity_data_copy['doctype'] = 'Crop Plan Activity'
				crop_plan_doc.append('activities', activity_data_copy)
		
		# Save activities first to get their names
		crop_plan_doc.save()
		frappe.db.commit()
		crop_plan_doc.reload()
		
		# Now process approved_input_mixes
		# If approved_input_mixes_data is empty but activities have approved_inputs, convert them
		if not approved_input_mixes_data:
			# Convert old format (approved_inputs in activities) to new format (approved_input_mixes)
			# Prefer matching by activity name (from request), fallback to block_reference + sequence
			activity_map_by_name = {}
			activity_map_by_key = {}
			for idx, activity_data in enumerate(activities_data):
				act_name = activity_data.get('name')
				if act_name:
					activity_map_by_name[act_name] = activity_data
				block_ref = str(activity_data.get('block_reference', ''))
				sequence = activity_data.get('sequence', 0)
				activity_key = f"{block_ref}_{sequence}"
				activity_map_by_key[activity_key] = activity_data
			
			# Match saved activities with original activity data (by name first, then by block+sequence)
			for saved_activity in crop_plan_doc.activities:
				activity_data = activity_map_by_name.get(saved_activity.name)
				if not activity_data:
					block_ref = str(saved_activity.block_reference or '')
					sequence = saved_activity.sequence or 0
					activity_key = f"{block_ref}_{sequence}"
					activity_data = activity_map_by_key.get(activity_key)
				
				if not activity_data:
					continue
				approved_inputs = activity_data.get('approved_inputs', [])
				if not approved_inputs or len(approved_inputs) == 0:
					continue
				# Group approved_inputs by farm_task (Crop Plan Approved Input Mix requires farm_task)
				inputs_by_farm_task = {}
				for input_item in approved_inputs:
					farm_task = input_item.get('farm_task') or ''
					if farm_task not in inputs_by_farm_task:
						inputs_by_farm_task[farm_task] = {
							'farm_task': farm_task,
							'task_name': input_item.get('task_name', ''),
							'inputs': []
						}
					inputs_by_farm_task[farm_task]['inputs'].append(input_item)
				
				block_ref = str(saved_activity.block_reference or '')
				sequence = saved_activity.sequence or 0
				for farm_task, mix_data in inputs_by_farm_task.items():
					if not farm_task:
						continue
					approved_input_mix_data = {
						'activity_reference': saved_activity.name,
						'activity_name': saved_activity.activity_name or activity_data.get('activity_name', ''),
						'block_reference': block_ref,
						'sequence': sequence,
						'farm_task': mix_data['farm_task'],
						'task_name': mix_data['task_name'],
						'approved_inputs': mix_data['inputs']
					}
					approved_input_mixes_data.append(approved_input_mix_data)
		
		# Remap activity_reference in approved_input_mixes_data to the freshly saved activity names.
		# After crop_plan_doc.save() + reload(), activity rows may have new names (when preserve_activity_names=False
		# or when activities were recreated). Build a lookup from the saved activities so mixes always
		# point to a valid activity_reference, preventing orphaned mixes in get_crop_plan_with_activities.
		saved_act_by_name = {a.name: a for a in crop_plan_doc.activities if a.name}
		saved_act_by_key = {}  # activity_name|block_reference|sequence -> saved activity
		saved_act_by_name_block: dict = {}  # activity_name|block_reference -> [saved activity, ...]
		name_block_counters: dict = {}
		for a in crop_plan_doc.activities:
			key = f"{(a.activity_name or '').strip()}|{a.block_reference}|{a.sequence or 0}"
			saved_act_by_key[key] = a
			nb_key = f"{(a.activity_name or '').strip()}|{a.block_reference}"
			if nb_key not in saved_act_by_name_block:
				saved_act_by_name_block[nb_key] = []
			saved_act_by_name_block[nb_key].append(a)

		for mix_data in approved_input_mixes_data:
			old_ref = mix_data.get('activity_reference', '')
			# If the reference already points to a valid saved activity, keep it
			if old_ref and old_ref in saved_act_by_name:
				continue
			# Try to resolve via activity_name + block_reference + sequence
			mix_act_name = (mix_data.get('activity_name') or '').strip()
			mix_block_ref = str(mix_data.get('block_reference') or '')
			mix_seq = mix_data.get('sequence') or 0
			resolved = None
			key = f"{mix_act_name}|{mix_block_ref}|{mix_seq}"
			if key in saved_act_by_key:
				resolved = saved_act_by_key[key]
			if not resolved:
				# Fallback: match by activity_name + block_reference (handles sequence shifts)
				nb_key = f"{mix_act_name}|{mix_block_ref}"
				candidates = saved_act_by_name_block.get(nb_key, [])
				if candidates:
					counter = name_block_counters.get(nb_key, 0)
					if counter < len(candidates):
						resolved = candidates[counter]
						name_block_counters[nb_key] = counter + 1
			if resolved:
				mix_data['activity_reference'] = resolved.name

		# Process approved_input_mixes
		# Store approved_inputs separately to add after mixes are saved
		# Use activity_reference + farm_task as key to match after reload
		mixes_with_inputs = []  # Store (activity_ref, farm_task, approved_inputs_data) tuples
		total_mixes_saved = 0
		total_inputs_saved = 0

		# Clear existing approved_input_mixes before appending new ones to prevent duplicate rows
		# accumulating across repeated saves. The frontend always sends the full list, so a full
		# replacement is correct. We delete the nested approved_inputs rows first (grand-child),
		# then the mix rows themselves (child), directly via frappe.db to avoid doc-save loops.
		# Use approved_input_mixes_explicitly_sent (not just truthiness) so that an explicitly sent
		# empty list (e.g. when POP is unchecked) still triggers the clear of existing mix rows.
		if approved_input_mixes_explicitly_sent:
			existing_mix_names = [m.name for m in crop_plan_doc.approved_input_mixes if m.name]
			if existing_mix_names:
				for old_mix_name in existing_mix_names:
					try:
						frappe.db.delete("Crop Plan Activity Input", {"parent": old_mix_name, "parenttype": "Crop Plan Approved Input Mix"})
						frappe.db.delete("Crop Plan Approved Input Mix", {"name": old_mix_name})
					except Exception:
						pass
			crop_plan_doc.set("approved_input_mixes", [])
			frappe.db.commit()

		for mix_data in approved_input_mixes_data:
			try:
				# Extract approved_inputs from mix
				mix_data_copy = mix_data.copy()
				approved_inputs_data = mix_data_copy.pop('approved_inputs', [])
				
				# Remove fields that shouldn't be in the mix row
				mix_data_copy.pop('name', None)  # Remove name for new records
				
				# Explicitly set doctype for approved_input_mix (critical for nested child tables)
				mix_data_copy['doctype'] = 'Crop Plan Approved Input Mix'
				
				# Store key for matching after reload
				activity_ref = mix_data_copy.get('activity_reference', '')
				farm_task = mix_data_copy.get('farm_task', '')
				mix_key = f"{activity_ref}_{farm_task}"
				
				# Create approved_input_mix row
				crop_plan_doc.append('approved_input_mixes', mix_data_copy)
				
				# Store approved_inputs to add after save (using key instead of index)
				if approved_inputs_data and len(approved_inputs_data) > 0:
					mixes_with_inputs.append((mix_key, approved_inputs_data))
			except Exception as e:
				frappe.log_error(f"Error adding approved_input_mix: {str(e)}\nTraceback: {traceback.format_exc()}\nData: {mix_data}", "Crop Plan Error")
				raise
		
		# Save approved_input_mixes first to get their names
		if approved_input_mixes_data:
			crop_plan_doc.save()
			frappe.db.commit()
			crop_plan_doc.reload()
		
		# Now explicitly iterate and add approved_inputs to each mix
		# Match by activity_reference + farm_task instead of index for reliability
		# After reload, we need to get fresh document references
		for mix_key, approved_inputs_data in mixes_with_inputs:
			# Find the mix row by matching activity_reference and farm_task
			mix_row = None
			mix_row_idx = -1
			for idx, mix in enumerate(crop_plan_doc.approved_input_mixes):
				mix_activity_ref = mix.get('activity_reference', '')
				mix_farm_task = mix.get('farm_task', '')
				if f"{mix_activity_ref}_{mix_farm_task}" == mix_key:
					mix_row = mix
					mix_row_idx = idx
					break
			
			if mix_row and approved_inputs_data and len(approved_inputs_data) > 0:
				# Get the mix document by name to ensure we have a proper document reference
				mix_doc_name = mix_row.name
				if mix_doc_name:
					try:
						# Get fresh document reference for the mix
						mix_doc = frappe.get_doc("Crop Plan Approved Input Mix", mix_doc_name)
						
						# Clear existing approved_inputs to avoid duplicates when updating
						# This ensures we replace all rows with the new data
						mix_doc.approved_inputs = []
						
						for input_data in approved_inputs_data:
							try:
								# Create clean input data
								input_data_clean = {
									'doctype': 'Crop Plan Activity Input',  # Explicitly set doctype for grand-child table
									'farm_task': input_data.get('farm_task') or '',
									'task_name': input_data.get('task_name') or '',
									'item': input_data.get('item') or '',
									'item_name': input_data.get('item_name') or '',
									'quantity': float(input_data.get('quantity', 0)) if input_data.get('quantity') is not None else 0,
									'unit': input_data.get('unit') or 'ml/L'
								}
								
								# Only include 'name' if it exists (for existing records to update)
								if input_data.get('name'):
									input_data_clean['name'] = input_data['name']
								
								# Only append if we have at least item or farm_task (required fields)
								if input_data_clean.get('item') or input_data_clean.get('farm_task'):
									# Append to the mix document's approved_inputs child table
									mix_doc.append('approved_inputs', input_data_clean)
									total_inputs_saved += 1
									frappe.log_error(f"Appended approved_input to mix {mix_key}: item={input_data_clean.get('item')}, quantity={input_data_clean.get('quantity')}", "Crop Plan Debug")
							except Exception as e:
								frappe.log_error(f"Error adding approved_input to mix {mix_key}: {str(e)}\nTraceback: {traceback.format_exc()}\nData: {input_data}", "Crop Plan Error")
								raise
						
						# Save the mix document with its approved_inputs
						mix_doc.save()
						frappe.db.commit()
						total_mixes_saved += 1
					except Exception as e:
						frappe.log_error(f"Error saving mix document {mix_doc_name}: {str(e)}\nTraceback: {traceback.format_exc()}", "Crop Plan Error")
						raise
				else:
					frappe.log_error(f"Mix row {mix_key} has no name after save", "Crop Plan Error")
		
		# Log for debugging
		frappe.log_error(
			f"After save: {len(activities_data)} activities, {total_mixes_saved} approved_input_mixes with {total_inputs_saved} approved_inputs appended, total_blocks: {crop_plan_doc.total_blocks}",
			"Crop Plan Save"
		)
		
		return crop_plan_doc.name
		
	except Exception as e:
		# Rollback transaction on error
		frappe.db.rollback()
		error_msg = f"Failed to save Crop Plan: {str(e)}"
		frappe.log_error(f"Error in create_or_update_crop_plan_with_activities: {error_msg}\nTraceback: {traceback.format_exc()}", "Crop Plan Error")
		frappe.throw(error_msg)


@frappe.whitelist()
def update_approved_input_qty(crop_plan_name, mix_name, approved_input_name, quantity, unit=None):
	"""
	Update the quantity (and optionally unit) of a specific approved input
	inside an Approved Input Mix's nested child table.

	Frappe's standard form save does not persist nested child table (child-of-child)
	changes, so this dedicated endpoint handles it directly via DB + doc save.

	Args:
		crop_plan_name: Name of the parent Crop Plan document
		mix_name: Name of the Crop Plan Approved Input Mix row
		approved_input_name: Name of the Crop Plan Activity Input row (nested child)
		quantity: New quantity value (float)
		unit: Optional new unit value

	Returns:
		dict with updated quantity, unit, and items_summary for the mix
	"""
	# Validate the crop plan exists and user has write access
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	crop_plan_doc.check_permission("write")

	# Validate the mix belongs to this crop plan
	mix_doc = frappe.get_doc("Crop Plan Approved Input Mix", mix_name)
	if mix_doc.parent != crop_plan_name:
		frappe.throw(f"Approved Input Mix {mix_name} does not belong to Crop Plan {crop_plan_name}")

	# Update the nested child row directly in the database
	quantity = float(quantity) if quantity is not None else 0

	update_fields = {"quantity": quantity}
	if unit is not None:
		update_fields["unit"] = unit

	frappe.db.set_value(
		"Crop Plan Activity Input",
		approved_input_name,
		update_fields,
		update_modified=True
	)

	# Also update the parent mix's modified timestamp so the change is tracked
	frappe.db.set_value(
		"Crop Plan Approved Input Mix",
		mix_name,
		"modified",
		frappe.utils.now()
	)

	# Update parent Crop Plan modified timestamp
	frappe.db.set_value(
		"Crop Plan",
		crop_plan_name,
		"modified",
		frappe.utils.now()
	)

	frappe.db.commit()

	# Build updated items_summary for the mix row
	approved_inputs = frappe.get_all(
		"Crop Plan Activity Input",
		filters={
			"parent": mix_name,
			"parenttype": "Crop Plan Approved Input Mix",
			"parentfield": "approved_inputs"
		},
		fields=["item_name", "item", "quantity", "unit"],
		order_by="idx asc"
	)
	items_summary_parts = []
	for inp in approved_inputs:
		name = inp.get("item_name") or inp.get("item") or "Item"
		qty = inp.get("quantity", "")
		u = inp.get("unit") or ""
		items_summary_parts.append(f"{name}: {qty} {u}".strip())
	items_summary = " · ".join(items_summary_parts)

	# Update items_summary on the mix row
	frappe.db.set_value(
		"Crop Plan Approved Input Mix",
		mix_name,
		"items_summary",
		items_summary
	)
	frappe.db.commit()

	return {
		"quantity": quantity,
		"unit": unit or frappe.db.get_value("Crop Plan Activity Input", approved_input_name, "unit"),
		"items_summary": items_summary
	}


@frappe.whitelist()
def get_approved_input_mixes_with_inputs(crop_plan_name):
	"""
	Fetch all approved_input_mixes with their nested approved_inputs for a Crop Plan.
	Used by the frontend to populate the editable dialog.

	Args:
		crop_plan_name: Name of the Crop Plan document

	Returns:
		List of approved_input_mix dicts, each with an 'approved_inputs' list
	"""
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	crop_plan_doc.check_permission("read")

	result = []
	for mix in crop_plan_doc.approved_input_mixes:
		mix_dict = mix.as_dict()
		mix_dict["approved_inputs"] = []

		approved_inputs = frappe.get_all(
			"Crop Plan Activity Input",
			filters={
				"parent": mix.name,
				"parenttype": "Crop Plan Approved Input Mix",
				"parentfield": "approved_inputs"
			},
			fields=["name", "farm_task", "task_name", "item", "item_name", "quantity", "unit"],
			order_by="idx asc"
		)
		mix_dict["approved_inputs"] = approved_inputs
		result.append(mix_dict)

	return result


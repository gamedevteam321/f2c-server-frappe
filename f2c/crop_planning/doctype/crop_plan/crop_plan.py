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

	def on_update(self):
		"""After save, perform any necessary operations"""
		pass
	
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
			
			crop_plan_doc.save()
			frappe.db.commit()
			# Need to reload to get new_activity.name correctly
			crop_plan_doc.reload()
			activity_name_in_db = crop_plan_doc.activities[-1].name
			
			# Load approved inputs if they exist into the root approved_inputs
			if hasattr(activity_mapping, 'tasks_items') and activity_mapping.tasks_items:
				for task_item in activity_mapping.tasks_items:
					crop_plan_doc.append("approved_inputs", {
						"activity_reference": activity_name_in_db,
						"activity_name": activity_mapping.activity_name,
						"block_reference": str(block_idx),
						"sequence": pop_activity.sequence or 0,
						"activity_group_type": activity_mapping.activity_group_type,
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
	
	# Final save
	crop_plan_doc.save()
	frappe.db.commit()
	
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
	
	# Process approved_inputs to nest into activities
	approved_inputs_list = [inp.as_dict() for inp in crop_plan_doc.approved_inputs]
	crop_plan_dict['approved_inputs'] = approved_inputs_list
	
	# Map back to activities
	activity_inputs_map = {}
	for input_dict in approved_inputs_list:
		activity_ref = input_dict.get('activity_reference')
		if activity_ref not in activity_inputs_map:
			activity_inputs_map[activity_ref] = []
		activity_inputs_map[activity_ref].append(input_dict)
		
	# Track which input names are already claimed via activity_reference
	claimed_input_names = {inp.get('name') for inp in approved_inputs_list if inp.get('activity_reference')}
	
	# Add approved_inputs back to activities for frontend compatibility
	for activity_dict in activities_list:
		activity_name = activity_dict.get('name')
		if activity_name and activity_name in activity_inputs_map:
			activity_dict['approved_inputs'] = activity_inputs_map[activity_name]
		else:
			# Fallback: match by activity_name, block_reference, AND sequence.
			act_display_name = (activity_dict.get('activity_name') or '').strip()
			act_block_ref = str(activity_dict.get('block_reference') or '')
			act_sequence = activity_dict.get('sequence')
			
			matched_inputs = []
			for input_dict in approved_inputs_list:
				if input_dict.get('name') in claimed_input_names: continue
				
				mix_act_name = (input_dict.get('activity_name') or '').strip()
				mix_block_ref = str(input_dict.get('block_reference') or '')
				mix_sequence = input_dict.get('sequence')
				
				if act_display_name and mix_act_name == act_display_name and (mix_block_ref == act_block_ref or mix_block_ref == '') and mix_sequence == act_sequence:
					matched_inputs.append(input_dict)
					
			if matched_inputs:
				activity_dict['approved_inputs'] = matched_inputs
	
	crop_plan_dict['activities'] = activities_list
	# Do not add approved_input_mixes since we removed it
	
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
		template_show_land_prep = 1 if block_info.get('template_show_land_prep') else 0
		if not template_show_land_prep:
			continue
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
		try:
			crop_plan_doc.save(ignore_permissions=True)
			frappe.db.commit()
		except frappe.exceptions.TimestampMismatchError:
			frappe.db.rollback()
			pass # Another concurrent request already injected and saved
		
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
		try:
			crop_plan_doc.save(ignore_permissions=True)
			frappe.db.commit()
		except frappe.exceptions.TimestampMismatchError:
			frappe.db.rollback()
			pass # Another concurrent request already injected and saved
		
		crop_plan_doc.reload()
	
	# Now convert the final saved document to the expected dict structure
	crop_plan_dict = crop_plan_doc.as_dict()
	
	# Process activities - refresh from reloaded doc
	activities_list = []
	for activity in crop_plan_doc.activities:
		activity_dict = activity.as_dict()
		activity_dict['approved_inputs'] = []  # Initialize empty
		activities_list.append(activity_dict)
	
	# Process approved_inputs natively
	approved_inputs_list = []
	activity_inputs_map = {}  # Map activity_reference to list of approved_inputs
	
	for input_row in crop_plan_doc.get("approved_inputs") or []:
		input_dict = input_row.as_dict()
		approved_inputs_list.append(input_dict)
		
		# Map activity_reference -> inputs
		activity_ref = input_dict.get('activity_reference')
		if activity_ref:
			if activity_ref not in activity_inputs_map:
				activity_inputs_map[activity_ref] = []
			activity_inputs_map[activity_ref].append(input_dict)
	
	# Track which inputs are already claimed via a VALID activity_reference that still exists
	valid_activity_names = {act_dict.get('name') for act_dict in activities_list if act_dict.get('name')}
	claimed_input_names = {
		input_dict.get('name') 
		for input_dict in approved_inputs_list 
		if input_dict.get('activity_reference') and input_dict.get('activity_reference') in valid_activity_names
	}
	
	# Add approved_inputs back to activities for frontend compatibility
	for activity_dict in activities_list:
		activity_name = activity_dict.get('name')
		if activity_name and activity_name in activity_inputs_map:
			activity_dict['approved_inputs'] = activity_inputs_map[activity_name]
		else:
			# Fallback: match input by activity_name, block_reference, AND sequence.
			act_display_name = (activity_dict.get('activity_name') or '').strip()
			act_block_ref = str(activity_dict.get('block_reference') or '')
			act_sequence = activity_dict.get('sequence')
			act_group_type = (activity_dict.get('activity_group_type') or '').strip()
			
			fallback_inputs = []
			for input_dict in approved_inputs_list:
				if input_dict.get('name') in claimed_input_names:
					continue  # already matched via activity_reference
				
				mix_act_name = (input_dict.get('activity_name') or '').strip()
				mix_block_ref = str(input_dict.get('block_reference') or '')
				mix_sequence = input_dict.get('sequence')
				mix_group_type = (input_dict.get('activity_group_type') or '').strip()
				
				# Match by name, block, group type and sequence to handle duplicates
				if act_display_name and mix_act_name == act_display_name and (mix_block_ref == act_block_ref or mix_block_ref == ''):
					is_match = False
					# If group type differs, it's NOT a match
					if mix_group_type and act_group_type and mix_group_type != act_group_type:
						pass
					elif mix_sequence == act_sequence:
						is_match = True
					elif mix_group_type == act_group_type and mix_group_type:
						is_match = True
					
					if is_match:
						fallback_inputs.append(input_dict)
			
			if fallback_inputs:
				activity_dict['approved_inputs'] = fallback_inputs
	
	crop_plan_dict['activities'] = activities_list
	crop_plan_dict['approved_inputs'] = approved_inputs_list
	
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
	Create or update Crop Plan with activities and flattened approved_inputs
	"""
	import json
	import traceback
	
	frappe.db.begin()
	
	try:
		if isinstance(crop_plan_data, str):
			crop_plan_data = json.loads(crop_plan_data)
		
		activities_data = list(crop_plan_data.get('activities', []))
		blocks_data = list(crop_plan_data.get('blocks', []))
		
		_MISSING = object()
		_raw_inputs = crop_plan_data.get('approved_inputs', _MISSING)
		approved_inputs_data = list(_raw_inputs) if _raw_inputs is not _MISSING else []
		approved_inputs_explicitly_sent = _raw_inputs is not _MISSING
		
		crop_plan_name = crop_plan_data.get('name')
		
		main_doc_data = {k: v for k, v in crop_plan_data.items() if k not in ['activities', 'blocks', 'approved_inputs', 'approved_input_mixes', 'name']}
		
		if crop_plan_name:
			crop_plan_doc = frappe.get_doc('Crop Plan', crop_plan_name)
			for key, value in main_doc_data.items():
				if key not in ['doctype', 'name']:
					setattr(crop_plan_doc, key, value)
		else:
			crop_plan_doc = frappe.get_doc({
				'doctype': 'Crop Plan',
				**{k: v for k, v in main_doc_data.items() if k not in ['doctype', 'name']}
			})
		
		# Handle blocks processing
		crop_plan_doc.set('blocks', [])
		for block_data in blocks_data:
			block_data_clean = block_data.copy()
			for field in ['name', 'parent', 'parentfield', 'parenttype', 'owner', 'creation', 'modified', 'modified_by', 'docstatus', 'idx']:
				block_data_clean.pop(field, None)
			block_data_clean['doctype'] = 'Crop Plan Block'
			crop_plan_doc.append('blocks', block_data_clean)
		crop_plan_doc.calculate_total_blocks()
		
		# Handle activities processing
		existing_activity_names = {a.name for a in crop_plan_doc.activities} if crop_plan_doc.activities else set()
		request_activity_names = {a.get('name') for a in activities_data if a.get('name')}
		preserve_activity_names = bool(crop_plan_name and request_activity_names and existing_activity_names)
		
		if not preserve_activity_names:
			crop_plan_doc.set('activities', [])
			for activity_data in activities_data:
				activity_data_copy = activity_data.copy()
				activity_data_copy.pop('approved_inputs', None)
				if not crop_plan_name:
					activity_data_copy.pop('name', None)
				activity_data_copy.pop('crop_stage_name', None)
				activity_data_copy.pop('activity_group_type_name', None)
				activity_data_copy['doctype'] = 'Crop Plan Activity'
				crop_plan_doc.append('activities', activity_data_copy)
		else:
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
			
		# Save basic changes to get activity names mapped
		crop_plan_doc.save()
		frappe.db.commit()
		crop_plan_doc.reload()
		
		# Now handle approved inputs
		# Map existing activity paths to resolve references
		saved_act_by_name = {a.name: a for a in crop_plan_doc.activities if a.name}
		saved_act_by_key = {}
		saved_act_by_name_block = {}
		name_block_counters = {}
		
		for a in crop_plan_doc.activities:
			agt = (a.activity_group_type or '').strip()
			key = f"{(a.activity_name or '').strip()}|{a.block_reference}|{a.sequence or 0}|{agt}"
			saved_act_by_key[key] = a
			
			# Multiple fallbacks
			nb_grp_key = f"{(a.activity_name or '').strip()}|{a.block_reference}|{agt}"
			if nb_grp_key not in saved_act_by_name_block:
				saved_act_by_name_block[nb_grp_key] = []
			saved_act_by_name_block[nb_grp_key].append(a)
			
			nb_key = f"{(a.activity_name or '').strip()}|{a.block_reference}"
			if nb_key not in saved_act_by_name_block:
				saved_act_by_name_block[nb_key] = []
			saved_act_by_name_block[nb_key].append(a)
			
		# If the frontend did not send approved_inputs explicitly, and activities nested inputs exist, construct it
		if not approved_inputs_data and not approved_inputs_explicitly_sent:
			for idx, activity_data in enumerate(activities_data):
				nested_inputs = activity_data.get('approved_inputs', [])
				for input_item in nested_inputs:
					input_clean = input_item.copy()
					# Try to fix reference
					act_name = activity_data.get('name')
					act_display_name = activity_data.get('activity_name', '')
					block_ref = str(activity_data.get('block_reference', ''))
					sequence = activity_data.get('sequence', 0)
					
					input_clean['activity_reference'] = act_name
					input_clean['activity_name'] = act_display_name
					input_clean['block_reference'] = block_ref
					input_clean['sequence'] = sequence
					input_clean['activity_group_type'] = activity_data.get('activity_group_type', '')
					approved_inputs_data.append(input_clean)
					
		# Resolve references
		for input_data in approved_inputs_data:
			old_ref = input_data.get('activity_reference', '')
			# If the reference already points to a valid saved activity, keep it
			if old_ref and old_ref in saved_act_by_name:
				continue
			mix_act_name = (input_data.get('activity_name') or '').strip()
			mix_block_ref = str(input_data.get('block_reference') or '')
			mix_seq = input_data.get('sequence') or 0
			mix_agt = (input_data.get('activity_group_type') or '').strip()
			resolved = None
			
			key = f"{mix_act_name}|{mix_block_ref}|{mix_seq}|{mix_agt}"
			if key in saved_act_by_key:
				resolved = saved_act_by_key[key]
				
			if not resolved:
				# Fallback: match by activity_name + block_reference + group_type
				nb_grp_key = f"{mix_act_name}|{mix_block_ref}|{mix_agt}"
				candidates = saved_act_by_name_block.get(nb_grp_key, [])
				if candidates:
					counter = name_block_counters.get(nb_grp_key, 0)
					if counter < len(candidates):
						resolved = candidates[counter]
						name_block_counters[nb_grp_key] = counter + 1
						
			if not resolved:
				# Fallback: match by activity_name + block_reference
				nb_key = f"{mix_act_name}|{mix_block_ref}"
				candidates = saved_act_by_name_block.get(nb_key, [])
				if candidates:
					counter = name_block_counters.get(nb_key, 0)
					if counter < len(candidates):
						resolved = candidates[counter]
						name_block_counters[nb_key] = counter + 1
			if resolved:
				input_data['activity_reference'] = resolved.name
				# Update related fields as well
				input_data['activity_name'] = resolved.activity_name
				input_data['block_reference'] = resolved.block_reference
				input_data['sequence'] = resolved.sequence
				input_data['activity_group_type'] = resolved.activity_group_type
		
		# Replace child table fully
		if approved_inputs_explicitly_sent or True:
			crop_plan_doc.set('approved_inputs', [])
			
			for input_data in approved_inputs_data:
				input_data_clean = input_data.copy()
				for field in ['name', 'parent', 'parentfield', 'parenttype', 'owner', 'creation', 'modified', 'modified_by', 'docstatus', 'idx']:
					input_data_clean.pop(field, None)
				input_data_clean['doctype'] = 'Crop Plan Activity Input'
				
				# Ensure defaults
				input_data_clean['quantity'] = float(input_data_clean.get('quantity', 0)) if input_data_clean.get('quantity') is not None else 0
				input_data_clean['unit'] = input_data_clean.get('unit') or 'ml/L'
				
				if input_data_clean.get('item') or input_data_clean.get('farm_task'):
					crop_plan_doc.append('approved_inputs', input_data_clean)
					
		crop_plan_doc.save()
		frappe.db.commit()
		return crop_plan_doc.name
		
	except Exception as e:
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

@frappe.whitelist()
def update_approved_input_qty(crop_plan_name, approved_input_name, quantity, unit=None, mix_name=None):
	"""
	Update the quantity (and optionally unit) of a specific approved input.
	mix_name is kept for backwards compatibility but ignored since inputs are flat now.
	"""
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	crop_plan_doc.check_permission("write")

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

	# Update parent Crop Plan modified timestamp
	frappe.db.set_value(
		"Crop Plan",
		crop_plan_name,
		"modified",
		frappe.utils.now()
	)

	frappe.db.commit()

	return {
		"quantity": quantity,
		"unit": unit or frappe.db.get_value("Crop Plan Activity Input", approved_input_name, "unit")
	}


@frappe.whitelist()
def get_approved_input_mixes_with_inputs(crop_plan_name):
	"""
	Fetch all approved_inputs grouped by activity and farm_task to mimic the old mix structure.
	Used by the frontend to populate the editable dialog if it still expects mixes.
	"""
	crop_plan_doc = frappe.get_doc("Crop Plan", crop_plan_name)
	crop_plan_doc.check_permission("read")

	# Group inputs by activity_reference and farm_task
	grouped = {}
	for input_row in crop_plan_doc.approved_inputs:
		act_ref = input_row.get("activity_reference") or ""
		task = input_row.get("farm_task") or ""
		key = f"{act_ref}_{task}"
		
		if key not in grouped:
			grouped[key] = {
				# Use a synthetic mix name for the UI if it expects one
				"name": f"mix-{key}",
				"activity_reference": act_ref,
				"activity_name": input_row.activity_name,
				"block_reference": input_row.block_reference,
				"sequence": input_row.sequence,
				"farm_task": task,
				"task_name": input_row.task_name,
				"approved_inputs": []
			}
		
		grouped[key]["approved_inputs"].append(input_row.as_dict())
		
	return list(grouped.values())


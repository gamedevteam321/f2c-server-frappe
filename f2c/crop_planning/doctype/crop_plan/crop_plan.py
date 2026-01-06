# -*- coding: utf-8 -*-
# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
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
		self.calculate_total_blocks()
		self.validate_blocks_belong_to_field()
		self.calculate_block_areas()
	
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
	
	# Remove existing activities for this block
	activities_to_remove = []
	for activity in crop_plan_doc.activities:
		if activity.block_reference == str(block_idx):
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
	
	# Save the document
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
		
		# Manually load approved_inputs for this mix
		mix_dict['approved_inputs'] = []
		try:
			# Query the child table directly
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
		
		# Also add to activity's approved_inputs for frontend compatibility
		activity_ref = mix_dict.get('activity_reference')
		if activity_ref:
			if activity_ref not in activity_inputs_map:
				activity_inputs_map[activity_ref] = []
			# Add all inputs from this mix to the activity
			activity_inputs_map[activity_ref].extend(mix_dict['approved_inputs'])
	
	# Add approved_inputs back to activities for frontend compatibility
	for activity_dict in activities_list:
		activity_name = activity_dict.get('name')
		if activity_name and activity_name in activity_inputs_map:
			activity_dict['approved_inputs'] = activity_inputs_map[activity_name]
	
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
				'name': block_row.name if hasattr(block_row, 'name') else None,
				'crop_configs': []
			}
		
		# Add this crop as a crop_config
		if block_row.crop:  # Only add if crop is set
			blocks_dict[block_ref]['crop_configs'].append({
				'crop': block_row.crop,
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
		approved_input_mixes_data = list(crop_plan_data.get('approved_input_mixes', []))
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
		
		# Clear existing child tables by replacing the lists
		# This ensures we start fresh
		crop_plan_doc.set('activities', [])
		crop_plan_doc.set('blocks', [])
		crop_plan_doc.set('approved_input_mixes', [])
		
		frappe.log_error(f"Processing {len(activities_data)} activities, {len(blocks_data)} blocks, {len(approved_input_mixes_data)} approved_input_mixes", "Crop Plan Debug")
		
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
		for idx, activity_data in enumerate(activities_data):
			# Remove fields that shouldn't be in the activity row
			activity_data_copy = activity_data.copy()
			activity_data_copy.pop('approved_inputs', None)  # Remove approved_inputs - they're in approved_input_mixes now
			# Keep 'name' if it exists for updates, but remove for new activities
			if not crop_plan_name:  # New document, remove name
				activity_data_copy.pop('name', None)
			activity_data_copy.pop('crop_stage_name', None)  # Remove computed fields
			activity_data_copy.pop('activity_group_type_name', None)  # Remove computed fields
			
			# Explicitly set doctype for activity (critical for nested child tables)
			activity_data_copy['doctype'] = 'Crop Plan Activity'
			
			# Create activity row
			crop_plan_doc.append('activities', activity_data_copy)
		
		# Save activities first to get their names
		crop_plan_doc.save()
		frappe.db.commit()
		crop_plan_doc.reload()
		
		# Now process approved_input_mixes
		# If approved_input_mixes_data is empty but activities have approved_inputs, convert them
		if not approved_input_mixes_data:
			# Convert old format (approved_inputs in activities) to new format (approved_input_mixes)
			# Create a map of activities by block_reference + sequence
			activity_map_by_key = {}
			for idx, activity_data in enumerate(activities_data):
				block_ref = str(activity_data.get('block_reference', ''))
				sequence = activity_data.get('sequence', 0)
				activity_key = f"{block_ref}_{sequence}"
				activity_map_by_key[activity_key] = activity_data
			
			# Match saved activities with original activity data
			for saved_activity in crop_plan_doc.activities:
				block_ref = str(saved_activity.block_reference or '')
				sequence = saved_activity.sequence or 0
				activity_key = f"{block_ref}_{sequence}"
				
				if activity_key in activity_map_by_key:
					activity_data = activity_map_by_key[activity_key]
					approved_inputs = activity_data.get('approved_inputs', [])
					
					if approved_inputs and len(approved_inputs) > 0:
						# Group approved_inputs by farm_task
						inputs_by_farm_task = {}
						for input_item in approved_inputs:
							farm_task = input_item.get('farm_task', '')
							if farm_task:
								if farm_task not in inputs_by_farm_task:
									inputs_by_farm_task[farm_task] = {
										'farm_task': farm_task,
										'task_name': input_item.get('task_name', ''),
										'inputs': []
									}
								inputs_by_farm_task[farm_task]['inputs'].append(input_item)
						
						# Create approved_input_mix entries
						for farm_task, mix_data in inputs_by_farm_task.items():
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
		
		# Process approved_input_mixes
		# Store approved_inputs separately to add after mixes are saved
		# Use activity_reference + farm_task as key to match after reload
		mixes_with_inputs = []  # Store (activity_ref, farm_task, approved_inputs_data) tuples
		total_mixes_saved = 0
		total_inputs_saved = 0
		
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
		
		# Reload the main document to reflect changes from mix documents
		if total_inputs_saved > 0:
			crop_plan_doc.reload()
		
		# Verify saved data by reloading
		crop_plan_doc.reload()
		
		# Verify approved_input_mixes were saved
		verified_mixes = len(crop_plan_doc.approved_input_mixes) if hasattr(crop_plan_doc, 'approved_input_mixes') else 0
		verified_inputs = 0
		for mix in crop_plan_doc.approved_input_mixes:
			# Manually query for approved_inputs to verify they were saved
			try:
				inputs = frappe.get_all(
					"Crop Plan Activity Input",
					filters={
						"parent": mix.name,
						"parenttype": "Crop Plan Approved Input Mix",
						"parentfield": "approved_inputs"
					},
					fields=["*"]
				)
				verified_inputs += len(inputs)
			except Exception as e:
				frappe.log_error(f"Error verifying approved_inputs for mix {mix.name}: {str(e)}", "Crop Plan Error")
		
		# Final save to ensure all changes are committed
		crop_plan_doc.save()
		frappe.db.commit()
		
		# Log for debugging
		frappe.log_error(
			f"After save: {len(activities_data)} activities, {total_mixes_saved} approved_input_mixes with {total_inputs_saved} approved_inputs appended, verified: {verified_mixes} mixes with {verified_inputs} inputs in DB, total_blocks: {crop_plan_doc.total_blocks}",
			"Crop Plan Save"
		)
		
		return crop_plan_doc.name
		
	except Exception as e:
		# Rollback transaction on error
		frappe.db.rollback()
		error_msg = f"Failed to save Crop Plan: {str(e)}"
		frappe.log_error(f"Error in create_or_update_crop_plan_with_activities: {error_msg}\nTraceback: {traceback.format_exc()}", "Crop Plan Error")
		frappe.throw(error_msg)


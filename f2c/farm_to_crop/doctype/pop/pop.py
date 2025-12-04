# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import json


class POP(Document):
	def validate(self):
		"""Validate and create Farm Crop Activity Mapping records for inline activities"""
		self.handle_inline_activities()
	
	def handle_inline_activities(self):
		"""Create Farm Crop Activity Mapping records from inline activity data"""
		if not self.activities:
			return
		
		for activity_row in self.activities:
			# Check if this is inline data (has activity field but no pop_activity_list)
			if hasattr(activity_row, '_inline_data') and activity_row._inline_data:
				# Create Farm Crop Activity Mapping record
				pop_activity_list = frappe.get_doc({
					'doctype': 'Farm Crop Activity Mapping',
					'crop': self.crop,
					'crop_stage': activity_row._inline_data.get('crop_stage'),
					'duration_after_stage': activity_row._inline_data.get('duration_after_stage'),
					'is_dat': activity_row._inline_data.get('is_dat', 0),
					'dat': activity_row._inline_data.get('dat'),
					'activity_group_type': activity_row._inline_data.get('activity_group_type'),
					'activity': activity_row._inline_data.get('activity'),
					'remarks': activity_row._inline_data.get('remarks')
				})
				pop_activity_list.insert()
				
				# Update the activity row with the created Farm Crop Activity Mapping ID
				activity_row.pop_activity_list = pop_activity_list.name
				activity_row.activity_name = pop_activity_list.activity_name
				
				# Clear inline data flag
				delattr(activity_row, '_inline_data')


@frappe.whitelist()
def create_pop_with_activities(pop_data):
	"""Create or update POP with inline Farm Crop Activity Mapping records
	
	Args:
		pop_data: JSON string or dict containing POP data with activities array
		
	Returns:
		Created/updated POP document name
	"""
	try:
		# Parse pop_data if it's a string
		if isinstance(pop_data, str):
			pop_data = json.loads(pop_data)
		
		# Extract activities data
		activities_data = pop_data.pop('activities', [])
		pop_name = pop_data.pop('name', None)
		
		# Create or update POP document
		if pop_name:
			# Update existing POP
			pop_doc = frappe.get_doc('POP', pop_name)
			# Update only the main fields
			pop_doc.pop_name = pop_data.get('pop_name', pop_doc.pop_name)
			pop_doc.crop = pop_data.get('crop', pop_doc.crop)
			pop_doc.description = pop_data.get('description', pop_doc.description)
		else:
			# Create new POP
			pop_doc = frappe.get_doc({
				'doctype': 'POP',
				'pop_name': pop_data.get('pop_name'),
				'crop': pop_data.get('crop'),
				'description': pop_data.get('description', '')
			})
		
		# Clear existing activities
		pop_doc.activities = []
		
		# Process each activity
		for idx, activity_data in enumerate(activities_data):
			sequence = activity_data.get('sequence', idx + 1)
			
			# Check if we have a pop_activity_list ID or need to create one
			if activity_data.get('pop_activity_list'):
				# Use existing Farm Crop Activity Mapping
				pop_activity_list_id = activity_data['pop_activity_list']
				try:
					pop_activity_list = frappe.get_doc('Farm Crop Activity Mapping', pop_activity_list_id)
					activity_name = pop_activity_list.activity_name
				except frappe.DoesNotExistError:
					# If the referenced Farm Crop Activity Mapping doesn't exist, create a new one
					pop_activity_list = frappe.get_doc({
						'doctype': 'Farm Crop Activity Mapping',
						'crop': pop_doc.crop,
						'crop_stage': activity_data.get('crop_stage'),
						'duration_after_stage': activity_data.get('duration_after_stage'),
						'is_dat': activity_data.get('is_dat', 0),
						'dat': activity_data.get('dat'),
						'activity_group_type': activity_data.get('activity_group_type'),
						'activity': activity_data.get('activity'),
						'remarks': activity_data.get('remarks', '')
					})
					pop_activity_list.insert()
					pop_activity_list_id = pop_activity_list.name
					activity_name = pop_activity_list.activity_name
			else:
				# Create new Farm Crop Activity Mapping record
				pop_activity_list = frappe.get_doc({
					'doctype': 'Farm Crop Activity Mapping',
					'crop': pop_doc.crop,
					'crop_stage': activity_data.get('crop_stage'),
					'duration_after_stage': activity_data.get('duration_after_stage'),
					'is_dat': activity_data.get('is_dat', 0),
					'dat': activity_data.get('dat'),
					'activity_group_type': activity_data.get('activity_group_type'),
					'activity': activity_data.get('activity'),
					'remarks': activity_data.get('remarks', '')
				})
				pop_activity_list.insert()
				pop_activity_list_id = pop_activity_list.name
				activity_name = pop_activity_list.activity_name
			
			# Add to POP activities child table
			pop_doc.append('activities', {
				'pop_activity_list': pop_activity_list_id,
				'activity_name': activity_name,
				'sequence': sequence
			})
		
		# Save the POP document (outside the loop)
		if pop_name:
			pop_doc.save()
		else:
			pop_doc.insert()
		
		frappe.db.commit()
		
		return pop_doc.name
		
	except frappe.DuplicateEntryError as e:
		frappe.db.rollback()
		frappe.throw(f"A POP with this name already exists: {str(e)}")
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(f"Error in create_pop_with_activities: {str(e)}")
		frappe.throw(f"Failed to save POP: {str(e)}")


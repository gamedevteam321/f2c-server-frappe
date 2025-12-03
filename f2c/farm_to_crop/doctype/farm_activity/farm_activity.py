# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import re
from frappe.model.document import Document


class FarmActivity(Document):
	def autoname(self):
		"""
		Generate the next Farm Activity ID starting from 001.
		Reuses deleted numbers if available, otherwise uses the next sequential number.
		"""
		# Only set name if it's not already set
		if self.name:
			return
			
		prefix = self.get("id") or "FA-"
		
		# Get all existing IDs for this prefix
		existing_ids = frappe.get_all(
			"Farm Activity",
			filters={"name": ["like", f"{prefix}%"]},
			fields=["name"],
			pluck="name"
		)
		
		# Extract numbers from existing IDs
		existing_numbers = []
		for id_name in existing_ids:
			# Extract the number part (e.g., "001" from "FA-001")
			match = re.search(rf"^{re.escape(prefix)}(\d+)$", id_name)
			if match:
				existing_numbers.append(int(match.group(1)))
		
		# Find the next available number
		if not existing_numbers:
			# No records exist, start from 001
			next_number = 1
		else:
			existing_numbers.sort()
			# Find gaps in the sequence
			next_number = None
			for i in range(1, max(existing_numbers) + 2):
				if i not in existing_numbers:
					next_number = i
					break
			
			if next_number is None:
				next_number = max(existing_numbers) + 1
		
		# Format as 3-digit number (001, 002, etc.)
		self.name = f"{prefix}{next_number:03d}"
	
	def validate(self):
		"""Validate parent activity hierarchy (no sequence handling)."""
		if self.parent_activity:
			# Prevent an activity from being its own parent
			if self.parent_activity == self.name:
				frappe.throw("An activity cannot be its own parent")
			
			# Check if parent activity exists
			if not frappe.db.exists("Farm Activity", self.parent_activity):
				frappe.throw(f"Parent Activity '{self.parent_activity}' does not exist")
			
			# Check for circular references
			self._check_circular_reference(self.name, self.parent_activity)
	
	def _check_circular_reference(self, current_activity, parent_activity, visited=None):
		"""Recursively check for circular references in parent hierarchy"""
		if visited is None:
			visited = set()
		
		if parent_activity in visited:
			frappe.throw("Circular reference detected in parent activity hierarchy")
		
		visited.add(parent_activity)
		
		# Get the parent's parent
		parent_doc = frappe.get_doc("Farm Activity", parent_activity)
		if parent_doc.parent_activity:
			self._check_circular_reference(current_activity, parent_doc.parent_activity, visited)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_parent_activities(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show activity_name in parent_activity field dropdown"""
	# Exclude current document to prevent self-reference
	current_doc = filters.get("name") if filters else None
	
	conditions = []
	if current_doc:
		conditions.append("name != %(current_doc)s")
	
	where_clause = " AND ".join(conditions) if conditions else ""
	if where_clause:
		where_clause = "WHERE " + where_clause
	
	# Return format: (name, activity_name) - Frappe will use activity_name as the display
	return frappe.db.sql("""
		SELECT 
			name,
			activity_name
		FROM `tabFarm Activity`
		{where_clause}
		AND (
			activity_name LIKE %(txt)s 
			OR name LIKE %(txt)s
		)
		ORDER BY activity_name
		LIMIT %(start)s, %(page_len)s
	""".format(where_clause=where_clause), {
		'txt': f'%{txt}%',
		'start': start,
		'page_len': page_len,
		'current_doc': current_doc
	}, as_dict=False)


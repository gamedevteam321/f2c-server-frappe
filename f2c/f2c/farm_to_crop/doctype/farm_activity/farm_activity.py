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


def _normalize_activity_category(activity_group_type_name: str | None, code: str | None) -> str:
	"""Map activity group type to a stable category string for Approved Tank Mix UI."""
	raw = (activity_group_type_name or "").strip()
	code_val = (code or "").strip()
	key = f"{raw} {code_val}".lower()

	if "plant" in key or "protection" in key or "pp" in key:
		return "Plant Protection"
	if "nutrition" in key or "nutri" in key or "nm" in key:
		return "Nutrition Management"

	# Fallback to the group type name (best-effort)
	return raw or code_val or ""


@frappe.whitelist()
def get_activity_category_for_farm_tasks(farm_tasks) -> dict:
	"""Return mapping of Farm Tasks -> activity category derived from Farm Activity mapping.

	Args:
		farm_tasks: list[str] or JSON string list of Farm Tasks names

	Returns:
		{ "<Farm Tasks name>": "Plant Protection" | "Nutrition Management" | "<fallback>" }
	"""
	# Guard: only users who can read Farm Tasks should be able to call this helper
	if not frappe.has_permission("Farm Tasks", "read"):
		raise frappe.PermissionError("Not permitted")

	task_list = frappe.parse_json(farm_tasks) if isinstance(farm_tasks, str) else farm_tasks
	if not task_list:
		return {}

	# ensure strings only + unique
	task_list = list({str(t) for t in task_list if t})
	if not task_list:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			fat.farm_task as farm_task,
			agt.activity_group_type_name as activity_group_type_name,
			agt.code as code
		FROM `tabFarm Activity Task` fat
		INNER JOIN `tabFarm Activity` fa
			ON fa.name = fat.parent
			AND fat.parenttype = 'Farm Activity'
		LEFT JOIN `tabActivity Group Type` agt
			ON agt.name = fa.activity_group_type
		WHERE fat.farm_task IN %(farm_tasks)s
		""",
		{"farm_tasks": tuple(task_list)},
		as_dict=True,
	)

	result: dict[str, str] = {}
	for r in rows:
		ft = (r.get("farm_task") or "").strip()
		if not ft or ft in result:
			continue
		result[ft] = _normalize_activity_category(r.get("activity_group_type_name"), r.get("code"))

	return result


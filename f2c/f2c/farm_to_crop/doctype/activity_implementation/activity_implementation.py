# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import re
from frappe.model.document import Document


class ActivityImplementation(Document):
	def autoname(self):
		"""
		Generate the next Activity Implementation ID starting from 001.
		Reuses deleted numbers if available, otherwise uses the next sequential number.
		"""
		# Only set name if it's not already set
		if self.name:
			return
			
		prefix = self.get("id") or "IM-"
		
		# Get all existing IDs for this prefix
		existing_ids = frappe.get_all(
			"Activity Implementation",
			filters={"name": ["like", f"{prefix}%"]},
			fields=["name"],
			pluck="name"
		)
		
		# Extract numbers from existing IDs
		existing_numbers = []
		for id_name in existing_ids:
			# Extract the number part (e.g., "001" from "IM-001")
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


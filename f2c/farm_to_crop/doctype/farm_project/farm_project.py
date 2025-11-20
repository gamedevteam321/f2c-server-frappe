# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FarmProject(Document):
	def validate(self):
		"""Validate that only root geo fencing areas (Farm type) are added"""
		self.validate_geo_areas()
	
	def validate_geo_areas(self):
		"""Ensure only root nodes (Farm type, level 1) are added to the project"""
		for row in self.geo_areas:
			if row.geo_fencing_area:
				area = frappe.get_doc("Geo Fencing Area", row.geo_fencing_area)
				
				# Check if it's a root node (Farm type with level 1)
				if area.level_sequence != 1:
					frappe.throw(
						f"Row #{row.idx}: Only root geo fencing areas (Farm type) can be added to a project. "
						f"'{area.area_name}' is a {area.geo_fencing_type} (Level {area.level_sequence})."
					)


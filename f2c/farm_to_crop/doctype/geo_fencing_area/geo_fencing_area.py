# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GeoFencingArea(Document):
	def before_save(self):
		"""Set level sequence based on geo fencing type"""
		self.set_level_sequence()
	
	def set_level_sequence(self):
		"""Set level sequence based on the hierarchy:
		Farm (1) -> Cluster (2) -> Field (3) -> Plot (4) -> Block (5) -> Row (6)
		"""
		level_map = {
			"Farm": 1,
			"Cluster": 2,
			"Field": 3,
			"Plot": 4,
			"Block": 5,
			"Row": 6
		}
		
		if self.geo_fencing_type:
			self.level_sequence = level_map.get(self.geo_fencing_type, 0)
	
	def validate(self):
		"""Validate parent-child relationship based on hierarchy"""
		if self.parent_area:
			parent = frappe.get_doc("Geo Fencing Area", self.parent_area)
			parent_level = parent.level_sequence or 0
			current_level = self.level_sequence or 0
			
			# Validate that child level is exactly one more than parent level
			if current_level != parent_level + 1:
				frappe.throw(
					f"Invalid hierarchy: {self.geo_fencing_type} (Level {current_level}) "
					f"cannot be a child of {parent.geo_fencing_type} (Level {parent_level}). "
					f"Expected level {parent_level + 1}."
				)

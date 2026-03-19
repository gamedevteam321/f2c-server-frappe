# -*- coding: utf-8 -*-
# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class CropPlanActivity(Document):
	def validate(self):
		"""Prevent changes to planned activities that have been scheduled."""
		if not self.is_new():
			self._check_if_scheduled()
	
	def on_trash(self):
		"""Prevent deletion of planned activities that have been scheduled."""
		self._check_if_scheduled()
	
	def _check_if_scheduled(self):
		"""Check if this activity has been scheduled and prevent modifications."""
		# Check if there are any Crop Plan Schedules referencing this activity
		schedules = frappe.get_all(
			"Crop Plan Schedule",
			filters={"crop_plan_activity": self.name},
			fields=["name", "status"],
			limit=1
		)
		
		if schedules:
			# Activity has been scheduled, prevent modifications
			frappe.throw(
				f"Cannot modify this planned activity because it has been scheduled. "
				f"Schedule: {schedules[0].name}. "
				"If you need to make changes, please cancel or abort the schedule first."
			)


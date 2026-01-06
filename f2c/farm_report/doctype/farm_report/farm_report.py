# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document


class FarmReport(Document):
	def validate(self):
		self._validate_report_type()
		self._validate_required_fields()
		self._autofill_fields()

	def _validate_report_type(self):
		"""Validate that report_type is set and is a valid option."""
		if not self.report_type:
			frappe.throw("Report Type is required.")
		
		valid_types = ["Delay", "Inventory Failure", "Farm Worker"]
		if self.report_type not in valid_types:
			frappe.throw(f"Report Type must be one of: {', '.join(valid_types)}")

	def _validate_required_fields(self):
		"""Validate required fields based on report_type."""
		if not self.report_reason or not self.report_reason.strip():
			frappe.throw("Report Reason is required.")
		
		if self.report_type == "Inventory Failure":
			if not self.get("equipment") or len(self.equipment) == 0:
				frappe.throw("At least one Equipment is required for Inventory Failure reports.")
		
		elif self.report_type == "Farm Worker":
			if not self.get("list_of_labours") or len(self.list_of_labours) == 0:
				frappe.throw("At least one Farm Worker is required for Farm Worker reports.")

	def _autofill_fields(self):
		"""Auto-fill read-only fields from linked documents."""
		if self.block:
			block_doc = frappe.get_doc("Geo Fencing Area", self.block)
			self.block_name = block_doc.area_name
		
		if self.stage:
			stage_doc = frappe.get_doc("Crop Stage", self.stage)
			self.stage_name = stage_doc.stage
		
		if self.activity:
			activity_doc = frappe.get_doc("Farm Activity", self.activity)
			self.activity_name = activity_doc.activity_name
		
		# Auto-fill asset_name and asset_type in equipment child table
		if self.get("equipment"):
			for equipment_row in self.equipment:
				if equipment_row.asset:
					try:
						asset_doc = frappe.get_doc("Asset", equipment_row.asset)
						if not equipment_row.asset_name:
							equipment_row.asset_name = asset_doc.asset_name
						if not equipment_row.asset_type:
							equipment_row.asset_type = asset_doc.asset_category or ""
					except frappe.DoesNotExistError:
						pass
		
		# Auto-fill worker_name in farm worker child table
		if self.get("list_of_labours"):
			for labour_row in self.list_of_labours:
				if labour_row.farm_worker and not labour_row.worker_name:
					try:
						worker_doc = frappe.get_doc("Farm Worker Details", labour_row.farm_worker)
						labour_row.worker_name = worker_doc.worker_name
					except frappe.DoesNotExistError:
						pass


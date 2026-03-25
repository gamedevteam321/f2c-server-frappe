# Copyright (c) 2026, Orgatek and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document


class LeasePlotLegend(Document):
	def validate(self):
		self.legend_name = (self.legend_name or "").strip()
		self.legend_color = (self.legend_color or "").strip().upper()

		if not self.legend_name:
			frappe.throw("Legend Name is required.")

		if not self.legend_color:
			frappe.throw("Legend Color is required.")

		if not re.fullmatch(r"#[0-9A-F]{6}", self.legend_color):
			frappe.throw("Legend Color must be a valid hex color like #10B981.")

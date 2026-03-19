# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document


class EquipmentReportTicket(Document):
	def validate(self):
		if self.report_type == "Repair":
			if not self.get("parts") or len(self.parts) == 0:
				frappe.throw("At least one Part is required for Repair reports.")

# -*- coding: utf-8 -*-

from __future__ import annotations

import frappe
from frappe.model.document import Document


class FarmTaskExecutionDay(Document):
	def validate(self):
		# Enforce unique (execution, date)
		if self.execution and self.date:
			filters = {"execution": self.execution, "date": self.date}
			if self.name:
				filters["name"] = ["!=", self.name]
			existing = frappe.db.exists("Farm Task Execution Day", filters)
			if existing:
				frappe.throw(
					frappe._("A day record already exists for this execution on date {0}. Only one day per execution per date is allowed.").format(
						self.date
					)
				)

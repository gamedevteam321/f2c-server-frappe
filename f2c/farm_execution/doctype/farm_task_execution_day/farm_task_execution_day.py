# -*- coding: utf-8 -*-

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class FarmTaskExecutionDay(Document):
	def validate(self):
		self._validate_inputs_consumed_qty()
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

	def _validate_inputs_consumed_qty(self):
		"""Consumed quantity cannot exceed issued quantity for any input."""
		for row in self.get("inputs") or []:
			consumed = flt(row.get("consumed_qty"), 3)
			issued = flt(row.get("issued_qty"), 3)
			if consumed > issued:
				item_label = (row.get("item_name") or row.get("item") or "Item").strip() or "Item"
				frappe.throw(
					frappe._("Consumed quantity cannot be greater than issued quantity for {0}. Issued: {1}, Consumed: {2}.").format(
						item_label, issued, consumed
					)
				)

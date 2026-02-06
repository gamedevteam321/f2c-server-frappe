# -*- coding: utf-8 -*-

from __future__ import annotations

from frappe.model.document import Document
from frappe.utils import flt


class FarmTaskExecutionDayInput(Document):
	def validate(self):
		self.consumed_qty = flt(flt(self.issued_qty) - flt(self.returned_qty), 3)

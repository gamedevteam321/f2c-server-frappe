# -*- coding: utf-8 -*-

from __future__ import annotations

from frappe.model.document import Document


class FarmTaskExecutionDayInput(Document):
	def validate(self):
		# consumed_qty is user input; do not overwrite with issued - returned
		pass

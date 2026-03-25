# -*- coding: utf-8 -*-
# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class Spacing(Document):
	def validate(self):
		"""Update spacing_name in row X col format with UOM"""
		if self.row is not None and self.col is not None:
			uom_name = ""
			if self.uom:
				try:
					uom_doc = frappe.get_doc("UOM", self.uom)
					uom_name = f" {uom_doc.uom_name}" if uom_doc.uom_name else ""
				except frappe.DoesNotExistError:
					uom_name = ""
			self.spacing_name = f"{self.row} X {self.col}{uom_name}"


# -*- coding: utf-8 -*-
# Copyright (c) 2026, Orgatek and contributors

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class EquipmentSpecOption(Document):
	def validate(self) -> None:
		title = (self.title or "").strip()
		if not title:
			frappe.throw(_("Title is required"))
		self.title = title

		filters: dict = {"option_type": self.option_type, "title": self.title}
		if self.name:
			filters["name"] = ["!=", self.name]

		if frappe.get_all("Equipment Spec Option", filters=filters, limit_page_length=1):
			frappe.throw(
				_("An option with this Title already exists for {0}.").format(
					frappe.bold(self.option_type)
				)
			)

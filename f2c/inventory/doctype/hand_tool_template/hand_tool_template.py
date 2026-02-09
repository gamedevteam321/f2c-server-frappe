# -*- coding: utf-8 -*-
from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from f2c.inventory.equipment_template_utils import create_or_update_item_from_template


class HandToolTemplate(Document):
	def validate(self):
		if self.default_item_code and not frappe.db.exists("Item", self.default_item_code):
			self.default_item_code = None


@frappe.whitelist()
def create_item_from_template(template_name: str, overwrite_existing: int = 0):
	if not template_name:
		frappe.throw(_("template_name is required"))
	doc = frappe.get_doc("Hand Tool Template", template_name)
	return create_or_update_item_from_template(doc, overwrite_existing=overwrite_existing)


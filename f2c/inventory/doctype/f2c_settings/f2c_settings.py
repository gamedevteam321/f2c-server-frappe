# Copyright (c) 2026, Orgatek and contributors
# License: MIT. See LICENSE

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class F2CSettings(Document):
	def validate(self):
		radius = cint(self.logistics_proximity_radius_meters)
		if radius <= 0:
			frappe.throw(frappe._("Logistics Proximity Radius must be greater than zero."))

		page = cint(self.manual_transfer_equipment_page_size)
		if page < 1 or page > 200:
			frappe.throw(frappe._("Manual Transfer Equipment Page Size must be between 1 and 200."))

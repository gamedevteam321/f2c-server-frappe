# -*- coding: utf-8 -*-

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document


class Broker(Document):
	def validate(self):
		# Enforce location selection (name + coordinates)
		location_name = (getattr(self, "location_name", None) or "").strip()
		lat = getattr(self, "latitude", None)
		lng = getattr(self, "longitude", None)

		if not location_name:
			frappe.throw(_("Location Name is required."), frappe.ValidationError)
		if lat in (None, "") or lng in (None, ""):
			frappe.throw(_("Latitude and Longitude are required."), frappe.ValidationError)

		# Enforce 10-digit numeric contact number (no country code)
		contact = (self.contact_no or "").strip()
		if contact:
			contact = re.sub(r"\s+", "", contact)
		self.contact_no = contact

		if not re.fullmatch(r"\d{10}", contact or ""):
			frappe.throw(_("Contact No must be a 10-digit number."), frappe.ValidationError)


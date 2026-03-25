# Copyright (c) 2024, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class VehicleInfo(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		acquisition_date: DF.Date | None
		amended_from: DF.Link | None
		carbon_check_date: DF.Date | None
		chassis_no: DF.Data | None
		color: DF.Data | None
		doors: DF.Int
		employee: DF.Link | None
		end_date: DF.Date | None
		fuel_type: DF.Literal["Petrol", "Diesel", "Natural Gas", "Electric"] | None
		insurance_company: DF.Data | None
		last_odometer: DF.Int
		license_plate: DF.Data | None
		location: DF.Data | None
		make: DF.Data | None
		model: DF.Data | None
		policy_no: DF.Data | None
		start_date: DF.Date | None
		uom: DF.Link | None
		vehicle_type: DF.Data
		vehicle_value: DF.Currency
		wheels: DF.Int
	# end: auto-generated types

	def validate(self):
		if self.start_date and self.end_date and getdate(self.start_date) > getdate(self.end_date):
			frappe.throw("Insurance Start date should be less than Insurance End date")
		if self.carbon_check_date and getdate(self.carbon_check_date) > getdate():
			frappe.throw("Last carbon check date cannot be a future date")


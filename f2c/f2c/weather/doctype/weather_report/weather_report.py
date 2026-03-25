# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WeatherReport(Document):
	def validate(self):
		"""Validate the weather report before saving."""
		self.validate_coordinates()
		self.validate_temperature_range()
	
	def validate_coordinates(self):
		"""Validate latitude and longitude values."""
		if self.latitude is not None:
			if self.latitude < -90 or self.latitude > 90:
				frappe.throw("Latitude must be between -90 and 90 degrees")
		
		if self.longitude is not None:
			if self.longitude < -180 or self.longitude > 180:
				frappe.throw("Longitude must be between -180 and 180 degrees")
	
	def validate_temperature_range(self):
		"""Validate that min temperature is not greater than max temperature."""
		if self.temperature_min is not None and self.temperature_max is not None:
			if self.temperature_min > self.temperature_max:
				frappe.throw("Minimum temperature cannot be greater than maximum temperature")
	
	def before_save(self):
		"""Actions to perform before saving the document."""
		# If location is set but coordinates are not, try to fetch them
		if self.location and (not self.latitude or not self.longitude):
			self.fetch_coordinates_from_location()
	
	def fetch_coordinates_from_location(self):
		"""Fetch latitude and longitude from the linked Geo Fencing Area."""
		if self.location:
			geo_area = frappe.get_doc("Geo Fencing Area", self.location)
			if hasattr(geo_area, 'latitude') and hasattr(geo_area, 'longitude'):
				self.latitude = geo_area.latitude
				self.longitude = geo_area.longitude

# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GeoFencingAreaWarehouse(Document):
	def get_image_urls(self):
		"""Return list of image URLs from comma-separated string"""
		if not self.images:
			return []
		return [url.strip() for url in self.images.split(',') if url.strip()]
	
	def has_images(self):
		"""Check if warehouse has any images"""
		return bool(self.images and self.images.strip())


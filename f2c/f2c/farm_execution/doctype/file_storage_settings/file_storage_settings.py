# Copyright (c) 2025, Orgatek and contributors
# License: MIT. See LICENSE

import frappe
from frappe.model.document import Document


class FileStorageSettings(Document):
	def validate(self):
		if (self.storage_backend or "").strip().lower() != "s3":
			return
		if not (self.s3_bucket or "").strip():
			frappe.throw(frappe._("Bucket Name is required when using S3 storage."))

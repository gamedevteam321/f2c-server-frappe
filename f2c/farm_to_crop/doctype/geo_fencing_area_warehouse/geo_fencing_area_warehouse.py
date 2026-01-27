# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


def _default_company():
	"""Get default company for creating new warehouses."""
	company = None
	try:
		company = frappe.defaults.get_global_default("company")
	except Exception:
		pass
	if not company:
		try:
			company = frappe.db.get_single_value("Global Defaults", "default_company")
		except Exception:
			pass
	if not company:
		companies = frappe.get_all("Company", limit=1)
		if companies:
			company = companies[0].name
	return company


class GeoFencingAreaWarehouse(Document):
	def validate(self):
		"""Create Warehouse if the link value does not exist (user typed a new name in Manage Area)."""
		if not getattr(self, "warehouse", None) or not str(self.warehouse).strip():
			return
		name = str(self.warehouse).strip()
		if frappe.db.exists("Warehouse", name):
			return
		company = _default_company()
		if not company:
			frappe.throw(
				frappe._("Cannot create warehouse '{0}': no Company set. Set default Company in Global Defaults.").format(name)
			)
		try:
			wh_doc = frappe.get_doc({
				"doctype": "Warehouse",
				"warehouse_name": name,
				"is_group": 0,
				"company": company,
			})
			wh_doc.insert(ignore_permissions=True)
			self.warehouse = wh_doc.name
		except Exception as e:
			frappe.log_error(
				f"Geo Fencing Area Warehouse: failed to create warehouse '{name}': {str(e)}",
				"Geo Fencing Area Warehouse Ensure"
			)
			frappe.throw(frappe._("Could not create warehouse '{0}': {1}").format(name, str(e)))

	def get_image_urls(self):
		"""Return list of image URLs from comma-separated string"""
		if not self.images:
			return []
		return [url.strip() for url in self.images.split(',') if url.strip()]
	
	def has_images(self):
		"""Check if warehouse has any images"""
		return bool(self.images and self.images.strip())


@frappe.whitelist()
def ensure_warehouse_exists(warehouse_name):
	"""Create Warehouse if it doesn't exist; return the warehouse doc name for linking.
	Called from the UI before save so the payload only contains existing warehouse names.
	"""
	if not warehouse_name or not str(warehouse_name).strip():
		return None
	name = str(warehouse_name).strip()
	if frappe.db.exists("Warehouse", name):
		return name
	company = _default_company()
	if not company:
		frappe.throw(frappe._("Cannot create warehouse: set default Company in Global Defaults."))
	wh_doc = frappe.get_doc({
		"doctype": "Warehouse",
		"warehouse_name": name,
		"is_group": 0,
		"company": company,
	})
	wh_doc.insert(ignore_permissions=True)
	return wh_doc.name


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_non_group_warehouses(doctype, txt, searchfield, start, page_len, filters):
	"""
	Get query for non-group warehouses only.
	Filters out warehouses where is_group = 1
	"""
	return frappe.db.sql("""
		SELECT name, warehouse_name
		FROM `tabWarehouse`
		WHERE is_group = 0
			AND disabled = 0
			AND (name LIKE %(txt)s OR warehouse_name LIKE %(txt)s)
		ORDER BY
			CASE WHEN name LIKE %(txt)s THEN 0 ELSE 1 END,
			warehouse_name
		LIMIT %(start)s, %(page_len)s
	""", {
		'txt': "%%%s%%" % txt,
		'start': start,
		'page_len': page_len
	})


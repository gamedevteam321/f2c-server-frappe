# -*- coding: utf-8 -*-

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class Machinery(Document):
	def validate(self):
		# Ensure template application happens consistently for API/imports too.
		# Only apply on Draft docs (submitted/cancelled should not be mutated silently).
		if int(getattr(self, "docstatus", 0) or 0) != 0:
			return
		from f2c.inventory.equipment_template_apply import apply_template_overwrite

		apply_template_overwrite(self)

	def before_insert(self):
		"""
		Auto-create an ERPNext Asset when creating Machinery, unless an existing Asset is linked.
		"""
		if self.asset:
			return
		self._create_and_link_asset()

	def _create_and_link_asset(self):
		# Validate required fields for Asset creation (from our DocType)
		missing = []
		for fieldname, label in [
			("item_code", "Item Code"),
			("company", "Company"),
			("location", "Location"),
			("purchase_date", "Purchase Date"),
		]:
			if not getattr(self, fieldname, None):
				missing.append(label)

		if missing:
			frappe.throw(_("Missing required field(s) to auto-create Asset: {0}").format(", ".join(missing)))

		is_existing_asset = int(getattr(self, "is_existing_asset", 0) or 0) == 1
		available_for_use_date = getattr(self, "available_for_use_date", None) or self.purchase_date

		# Match the user flow: existing assets must explicitly have available-for-use date,
		# non-existing assets must reference a purchase document.
		if is_existing_asset and not getattr(self, "available_for_use_date", None):
			frappe.throw(_("Available-for-use Date is required when 'Is Existing Asset' is checked."))

		if not is_existing_asset:
			if not (getattr(self, "purchase_receipt", None) or getattr(self, "purchase_invoice", None)):
				frappe.throw(_("Purchase Receipt or Purchase Invoice is required when 'Is Existing Asset' is not checked."))

		# Derive naming series from Item (ERPNext requires this for auto-name on Asset)
		item_meta = frappe.db.get_value(
			"Item",
			self.item_code,
			["asset_naming_series", "asset_category", "is_fixed_asset"],
			as_dict=True,
		)
		if not item_meta:
			frappe.throw(_("Item {0} not found.").format(self.item_code))

		if not item_meta.get("asset_naming_series"):
			frappe.throw(
				_(
					"Item {0} is missing Asset Naming Series. Set Item.asset_naming_series to enable auto Asset creation."
				).format(self.item_code)
			)

		# Asset submission typically requires an Asset Category (drives accounting/depreciation defaults)
		if not item_meta.get("asset_category"):
			frappe.throw(
				_(
					"Item {0} is missing Asset Category. Set Item.asset_category to enable auto Asset creation."
				).format(self.item_code)
			)

		asset_name = (self.asset_name or "").strip() or self._generate_asset_name()

		asset_doc = frappe.get_doc(
			{
				"doctype": "Asset",
				"naming_series": item_meta.get("asset_naming_series"),
				"asset_category": item_meta.get("asset_category"),
				"asset_name": asset_name,
				"item_code": self.item_code,
				"company": self.company,
				"location": self.location,
				"purchase_date": self.purchase_date,
				"available_for_use_date": available_for_use_date,
				"gross_purchase_amount": self.price or 0,
				"is_existing_asset": 1 if is_existing_asset else 0,
				"asset_owner": getattr(self, "asset_owner", None) or "Company",
				"asset_owner_company": (getattr(self, "asset_owner_company", None) or self.company) if (getattr(self, "asset_owner", None) or "Company") == "Company" else None,
				"asset_quantity": 1,
			}
		)

		# Optional purchase references (only for non-existing assets)
		if not is_existing_asset:
			if getattr(self, "purchase_receipt", None):
				asset_doc.purchase_receipt = self.purchase_receipt
			if getattr(self, "purchase_invoice", None):
				asset_doc.purchase_invoice = self.purchase_invoice

		# Optional: use hero image on Asset as well (ERPNext Asset.image exists but is hidden)
		if getattr(self, "hero_image", None):
			asset_doc.image = self.hero_image

		try:
			asset_doc.insert(ignore_permissions=True)
			# Leave Asset in Draft so user can review before submitting.
		except Exception as e:
			frappe.throw(_("Failed to auto-create Asset: {0}").format(str(e)))

		self.asset = asset_doc.name
		# Persist the generated name back on Machinery for visibility
		self.asset_name = asset_name

	def _generate_asset_name(self) -> str:
		parts: list[str] = []
		if (self.brand or "").strip():
			parts.append(self.brand.strip())
		if (self.model or "").strip():
			parts.append(self.model.strip())
		if (self.machinery_type or "").strip():
			parts.append(self.machinery_type.strip())
		if (self.serial_number or "").strip():
			parts.append(f"SN:{self.serial_number.strip()}")
		if parts:
			return " - ".join(parts)
		if (self.machinery_name or "").strip():
			return self.machinery_name.strip()
		return "Machinery"



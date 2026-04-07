# -*- coding: utf-8 -*-

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class Implement(Document):
	def validate(self):
		if int(getattr(self, "docstatus", 0) or 0) != 0:
			return
		from f2c.inventory.equipment_template_apply import apply_template_overwrite

		apply_template_overwrite(self)
		self._validate_tractor_attachment()

	def _validate_tractor_attachment(self):
		tractor = getattr(self, "attached_to_machinery", None)
		if not tractor:
			return

		machinery_type = frappe.db.get_value("Machinery", tractor, "machinery_type")
		if machinery_type != "Tractor":
			frappe.throw(_("Implements can only be attached to Tractor type machinery."))

		current_on_tractor = frappe.db.get_value("Machinery", tractor, "current_implement")
		if current_on_tractor and current_on_tractor != self.name:
			frappe.throw(
				_("Tractor {0} already has implement {1} attached. Detach it first.").format(
					tractor, current_on_tractor
				)
			)

	def on_update(self):
		if frappe.flags.get("in_implement_sync"):
			return
		self._sync_tractor_attachment()

	def _sync_tractor_attachment(self):
		prev = self.get_doc_before_save()
		old_tractor = (prev.attached_to_machinery if prev else None) or None
		new_tractor = getattr(self, "attached_to_machinery", None) or None

		if old_tractor == new_tractor:
			return

		now = frappe.utils.now()
		frappe.flags["in_implement_sync"] = True
		try:
			if old_tractor and old_tractor != new_tractor:
				frappe.db.set_value(
					"Machinery",
					old_tractor,
					{
						"current_implement": None,
						"attachment_status": "None",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)

			if new_tractor:
				frappe.db.set_value(
					"Machinery",
					new_tractor,
					{
						"current_implement": self.name,
						"attachment_status": "Attached",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)
				frappe.db.set_value(
					"Implement",
					self.name,
					{
						"attachment_status": "Attached",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)
			else:
				frappe.db.set_value(
					"Implement",
					self.name,
					{
						"attachment_status": "Detached",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)
		finally:
			frappe.flags["in_implement_sync"] = False

	def before_insert(self):
		if self.asset:
			return
		self._create_and_link_asset()

	def _create_and_link_asset(self):
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

		if is_existing_asset and not getattr(self, "available_for_use_date", None):
			frappe.throw(_("Available-for-use Date is required when 'Is Existing Asset' is checked."))

		if not is_existing_asset:
			if not (getattr(self, "purchase_receipt", None) or getattr(self, "purchase_invoice", None)):
				frappe.throw(_("Purchase Receipt or Purchase Invoice is required when 'Is Existing Asset' is not checked."))

		item_meta = frappe.db.get_value(
			"Item",
			self.item_code,
			["asset_naming_series", "asset_category"],
			as_dict=True,
		)
		if not item_meta:
			frappe.throw(_("Item {0} not found.").format(self.item_code))

		from f2c.inventory.asset_defaults import get_default_asset_naming_series, get_default_asset_category

		naming_series = (item_meta.get("asset_naming_series") or "").strip() or get_default_asset_naming_series()
		asset_category = (item_meta.get("asset_category") or "").strip() or get_default_asset_category()

		if not asset_category:
			frappe.throw(
				_(
					"Item {0} is missing Asset Category and no default Asset Category exists. "
					"Set Item.asset_category or create an Asset Category to enable auto Asset creation."
				).format(self.item_code)
			)

		asset_name = (self.asset_name or "").strip() or self._generate_asset_name()

		asset_doc = frappe.get_doc(
			{
				"doctype": "Asset",
				"naming_series": naming_series,
				"asset_category": asset_category,
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

		if not is_existing_asset:
			if getattr(self, "purchase_receipt", None):
				asset_doc.purchase_receipt = self.purchase_receipt
			if getattr(self, "purchase_invoice", None):
				asset_doc.purchase_invoice = self.purchase_invoice

		if getattr(self, "hero_image", None):
			asset_doc.image = self.hero_image

		try:
			asset_doc.insert(ignore_permissions=True)
			# Leave Asset in Draft so user can review before submitting.
		except Exception as e:
			frappe.throw(_("Failed to auto-create Asset: {0}").format(str(e)))

		self.asset = asset_doc.name
		self.asset_name = asset_name

	def _generate_asset_name(self) -> str:
		parts: list[str] = []
		if (self.brand or "").strip():
			parts.append(self.brand.strip())
		if (self.model or "").strip():
			parts.append(self.model.strip())
		if (self.implement_type or "").strip():
			parts.append(self.implement_type.strip())
		if parts:
			return " - ".join(parts)
		if (self.implement_name or "").strip():
			return self.implement_name.strip()
		return "Implement"



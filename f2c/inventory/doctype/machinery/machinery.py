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
		self._validate_implement_attachment()

	def _validate_implement_attachment(self):
		prev = self.get_doc_before_save()
		old_impl = (prev.current_implement if prev else None) or None
		new_impl = (getattr(self, "current_implement", None) or None) or None

		if (self.machinery_type or "").strip() == "Tractor" and self.name:
			from f2c.inventory.equipment_location_level import assert_cluster_for_tractor_implement_link_change

			assert_cluster_for_tractor_implement_link_change(self.name, old_impl, new_impl)

		current_implement = getattr(self, "current_implement", None)
		if not current_implement:
			return

		if (self.machinery_type or "") != "Tractor":
			frappe.throw(_("Only Tractor type machinery can have an implement attached."))

		owner = frappe.db.get_value("Implement", current_implement, "attached_to_machinery")
		if owner and owner != self.name:
			frappe.throw(
				_("Implement {0} is already attached to {1}. Detach it first.").format(
					current_implement, owner
				)
			)

	def on_update(self):
		frappe.log_error(
			title="[DBG] Machinery.on_update",
			message=f"{self.name} | docstatus={getattr(self,'docstatus',None)} | in_sync={frappe.flags.get('in_implement_sync')} | current_implement={getattr(self,'current_implement',None)!r}"
		)
		if frappe.flags.get("in_implement_sync"):
			return
		self._sync_implement_attachment()

	def on_update_after_submit(self):
		frappe.log_error(
			title="[DBG] Machinery.on_update_after_submit",
			message=f"{self.name} | in_sync={frappe.flags.get('in_implement_sync')} | current_implement={getattr(self,'current_implement',None)!r}"
		)
		if frappe.flags.get("in_implement_sync"):
			return
		self._sync_implement_attachment()

	def _sync_implement_attachment(self):
		prev = self.get_doc_before_save()
		old_implement = (prev.current_implement if prev else None) or None
		new_implement = getattr(self, "current_implement", None) or None

		frappe.log_error(title="[DBG] Machinery._sync", message=f"{self.name} | old={old_implement!r} | new={new_implement!r}")

		# Even when the value hasn't changed, the implement's back-link may be inconsistent
		# (e.g. a previous save stored current_implement but the on_update sync failed).
		# Check actual DB state and force sync if anything is out of sync.
		if old_implement == new_implement:
			if new_implement:
				actual_owner = frappe.db.get_value("Implement", new_implement, "attached_to_machinery") or None
				actual_status = frappe.db.get_value("Machinery", self.name, "attachment_status") or None
				already_consistent = (actual_owner == self.name and actual_status == "Attached")
				frappe.log_error(title="[DBG] Machinery._sync same-val", message=f"actual_owner={actual_owner!r} actual_status={actual_status!r} consistent={already_consistent}")
				if already_consistent:
					return
				# Fall through to fix the inconsistency
			else:
				return

		now = frappe.utils.now()
		frappe.flags["in_implement_sync"] = True
		try:
			if old_implement and old_implement != new_implement:
				frappe.log_error(title="[DBG] Machinery._sync clear-old", message=f"clearing {old_implement}")
				frappe.db.set_value(
					"Implement",
					old_implement,
					{
						"attached_to_machinery": None,
						"attachment_status": "Detached",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)

			if new_implement:
				frappe.log_error(title="[DBG] Machinery._sync attach", message=f"attaching {new_implement} to {self.name}")
				frappe.db.set_value(
					"Implement",
					new_implement,
					{
						"attached_to_machinery": self.name,
						"attachment_status": "Attached",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)
				frappe.db.set_value(
					"Machinery",
					self.name,
					{
						"attachment_status": "Attached",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)
				frappe.logger().info(f"[Machinery._sync] DONE — attached {new_implement} to {self.name}")
			else:
				frappe.logger().info(f"[Machinery._sync] clearing attachment on {self.name}")
				frappe.db.set_value(
					"Machinery",
					self.name,
					{
						"attachment_status": "None",
						"attachment_updated_on": now,
					},
					update_modified=False,
				)
		except Exception as e:
			frappe.logger().error(f"[Machinery._sync] ERROR: {e}")
			raise
		finally:
			frappe.flags["in_implement_sync"] = False

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

		# Derive naming series and category from Item; use defaults when Item has none set
		item_meta = frappe.db.get_value(
			"Item",
			self.item_code,
			["asset_naming_series", "asset_category", "is_fixed_asset"],
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
		if (self.chassis_number or "").strip():
			parts.append(f"CH:{self.chassis_number.strip()}")
		elif (self.serial_number or "").strip():
			parts.append(f"SN:{self.serial_number.strip()}")
		if parts:
			return " - ".join(parts)
		if (self.machinery_name or "").strip():
			return self.machinery_name.strip()
		return "Machinery"



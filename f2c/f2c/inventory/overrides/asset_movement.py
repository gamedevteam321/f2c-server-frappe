# Copyright (c) 2025, Orgatek and contributors
# Allow draft assets in Asset Movement (Transfer) for LTT flow.

import frappe
from frappe import _

from erpnext.assets.doctype.asset_movement.asset_movement import AssetMovement


class F2CAssetMovement(AssetMovement):
	"""Override Asset Movement to allow Draft assets for purpose Transfer (LTT flow)."""

	def validate_asset(self, d):
		status, company = frappe.db.get_value("Asset", d.asset, ["status", "company"])
		# For Transfer: allow Draft; still block Scrapped and Sold
		if self.purpose == "Transfer" and status in ("Scrapped", "Sold"):
			frappe.throw(_("{0} asset cannot be transferred").format(status))

		if company != self.company:
			frappe.throw(_("Asset {0} does not belong to company {1}").format(d.asset, self.company))

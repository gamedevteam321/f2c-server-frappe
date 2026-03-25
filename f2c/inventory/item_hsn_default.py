# Copyright (c) 2025, Orgatek and contributors
# Set default HSN/SAC code for Items when India Compliance requires it.

import frappe


def _get_valid_hsn_length():
	"""Return allowed HSN code lengths from GST Settings (6 or 8 if min_hsn_digits=6; else 4,6,8)."""
	try:
		from india_compliance.gst_india.utils import get_hsn_settings
		_, valid_length = get_hsn_settings()
		return valid_length
	except Exception:
		return (4, 6, 8)


def set_default_gst_hsn_code_for_fixed_asset(doc, method=None):
	"""
	Before Item validate: if Item is a sales item or fixed asset and gst_hsn_code is empty,
	set it from the Item Group's default, or from a suitable fallback (default 00000000), so India Compliance passes.
	"""
	if doc.get("gst_hsn_code"):
		return
	if not (doc.get("is_sales_item") or doc.get("is_fixed_asset")):
		return
	if not frappe.db.table_exists("GST HSN Code"):
		return

	valid_length = _get_valid_hsn_length()

	def _valid_code(code):
		return code and len(str(code).strip()) in valid_length and frappe.db.exists("GST HSN Code", code)

	# 1) Default by Item Group: use Item Group's default GST HSN Code if set and valid
	# (Only query if the Custom Field exists; it is created by ensure_item_group_default_gst_hsn_code_field on migrate)
	if doc.get("item_group") and frappe.get_meta("Item Group").has_field("default_gst_hsn_code"):
		group_code = frappe.db.get_value("Item Group", doc.item_group, "default_gst_hsn_code")
		if _valid_code(group_code):
			doc.gst_hsn_code = group_code
			return

	# 2) Fallback: prefer default 00000000, then other codes that match valid length
	preferred = ("00000000", "999900", "61149090", "843290", "843390", "998314", "8432", "8433", "9983", "9999")
	for code in preferred:
		if _valid_code(code):
			doc.gst_hsn_code = code
			return

	# 3) Any existing code with valid length
	for name in frappe.get_all("GST HSN Code", pluck="name", order_by="name"):
		if len(str(name).strip()) in valid_length:
			doc.gst_hsn_code = name
			return

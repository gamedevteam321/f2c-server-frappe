# -*- coding: utf-8 -*-
"""
API for Purchase Receipt and Purchase Invoice lists filtered by item and docstatus.
Used by equipment forms (Machinery, Implement, Hand Tool, Other Tool) to show only
non-cancelled receipts/invoices that contain the selected item.
"""
from __future__ import annotations

import frappe


@frappe.whitelist()
def get_purchase_receipts_and_invoices_for_item(item_code: str) -> dict:
	"""
	Return Purchase Receipt and Purchase Invoice lists that:
	- Are not cancelled (docstatus != 2)
	- Contain the given item_code in their items table.

	:param item_code: Item code to filter by.
	:return: {"purchase_receipts": [{"name", "supplier", "posting_date"}, ...], "purchase_invoices": [...]}
	"""
	item_code = (item_code or "").strip()
	if not item_code:
		return {"purchase_receipts": [], "purchase_invoices": []}

	# Purchase Receipt: get parent names from Purchase Receipt Item where item_code matches
	# and parent is not cancelled
	pr_names = frappe.db.sql(
		"""
		SELECT DISTINCT pri.parent
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pri.item_code = %(item_code)s
		  AND COALESCE(pr.docstatus, 0) != 2
		ORDER BY pri.parent
		""",
		{"item_code": item_code},
	)
	pr_names = [r[0] for r in pr_names]

	# Purchase Invoice: same for Purchase Invoice Item
	pi_names = frappe.db.sql(
		"""
		SELECT DISTINCT pii.parent
		FROM `tabPurchase Invoice Item` pii
		INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		WHERE pii.item_code = %(item_code)s
		  AND COALESCE(pi.docstatus, 0) != 2
		ORDER BY pii.parent
		""",
		{"item_code": item_code},
	)
	pi_names = [r[0] for r in pi_names]

	# Fetch name, supplier, posting_date for those docs (order by modified desc for recency)
	purchase_receipts = []
	if pr_names:
		purchase_receipts = frappe.db.sql(
			"""
			SELECT name, supplier, posting_date
			FROM `tabPurchase Receipt`
			WHERE name IN %(names)s
			ORDER BY modified DESC
			""",
			{"names": pr_names},
			as_dict=True,
		)

	purchase_invoices = []
	if pi_names:
		purchase_invoices = frappe.db.sql(
			"""
			SELECT name, supplier, posting_date
			FROM `tabPurchase Invoice`
			WHERE name IN %(names)s
			ORDER BY modified DESC
			""",
			{"names": pi_names},
			as_dict=True,
		)

	return {
		"purchase_receipts": purchase_receipts,
		"purchase_invoices": purchase_invoices,
	}

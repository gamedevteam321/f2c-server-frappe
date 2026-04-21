# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

"""
Warehouse helpers: resolve ledger (stock) warehouses for farm/cluster and areas.
Used for transfer-ticket source selection and frontend filter sets.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import frappe
from frappe.utils import flt


def get_ledger_warehouse(warehouse_name: str) -> str | None:
	"""
	Return the ledger warehouse to use for stock/transfers.
	- If warehouse is group (is_group=1): return its ledger child (e.g. " - Stock" or first is_group=0 child).
	- Otherwise return the warehouse as-is.
	"""
	if not (warehouse_name and str(warehouse_name).strip()):
		return None
	wh = str(warehouse_name).strip()
	try:
		is_group = frappe.db.get_value("Warehouse", wh, "is_group", ignore_permissions=True)
		if is_group is None:
			return None
		if is_group == 0:
			return wh
		# Group warehouse: resolve to ledger child
		children = frappe.get_all(
			"Warehouse",
			filters={"parent_warehouse": wh, "is_group": 0},
			fields=["name", "warehouse_name"],
			limit=10,
			ignore_permissions=True,
		)
		if not children:
			return None
		# Prefer one whose warehouse_name ends with " - Stock"
		for c in children:
			wn = (c.get("warehouse_name") or "").strip()
			if wn.endswith(" - Stock") or wn.endswith("- Stock"):
				return c.get("name")
		return children[0].get("name")
	except Exception as e:
		frappe.log_error(
			f"get_ledger_warehouse({wh!r}): {e}",
			"Warehouse Utils",
		)
		return None


@frappe.whitelist()
def get_ledger_warehouses_for_areas(area_names: str | List[str]) -> List[str]:
	"""
	Return list of ledger warehouse names linked to the given Geo Fencing Areas.
	Used for filtering (e.g. Pickup/Receivable by farm/cluster) and for source-by-location logic.
	Accepts JSON list string or list from Python.
	"""
	if not area_names:
		return []
	if isinstance(area_names, str):
		try:
			area_names = json.loads(area_names)
		except Exception:
			area_names = [a.strip() for a in area_names.split(",") if a.strip()]
	if not isinstance(area_names, (list, tuple)):
		return []
	names = [str(a).strip() for a in area_names if a]
	if not names:
		return []
	try:
		rows = frappe.get_all(
			"Geo Fencing Area Warehouse",
			fields=["warehouse"],
			filters={"parent": ["in", names], "parenttype": "Geo Fencing Area"},
			limit_page_length=0,
			ignore_permissions=True,
		)
		seen: set[str] = set()
		out: List[str] = []
		for r in rows:
			wh = r.get("warehouse")
			if not wh:
				continue
			ledger = get_ledger_warehouse(wh)
			if ledger and ledger not in seen:
				seen.add(ledger)
				out.append(ledger)
		return out
	except Exception as e:
		frappe.log_error(
			f"get_ledger_warehouses_for_areas({names!r}): {e}",
			"Warehouse Utils",
		)
		return []


def get_source_warehouse_by_item_location(
	input_items: List[Dict[str, Any]],
	candidate_warehouses: List[str],
) -> str | None:
	"""
	Among candidate_warehouses, pick the one that can fulfil the most of the required qty
	(sum over items of min(required_qty, get_stock_balance(item, wh))).
	Input items: list of dicts with "item_code" and "qty".
	Returns warehouse name or None if none have stock (caller should fall back to primary/default).
	"""
	if not input_items or not candidate_warehouses:
		return None
	try:
		from erpnext.stock.utils import get_stock_balance
	except Exception:
		return None
	best_wh: str | None = None
	best_score: float = -1.0
	for wh in candidate_warehouses:
		if not wh:
			continue
		score = 0.0
		for row in input_items:
			item_code = row.get("item_code")
			required = flt(row.get("qty"), 3)
			if not item_code or required <= 0:
				continue
			bal = get_stock_balance(item_code, wh)
			available = flt(bal, 3) if bal is not None else 0
			score += min(required, available)
		if score > best_score:
			best_score = score
			best_wh = wh
	return best_wh if best_score > 0 else None

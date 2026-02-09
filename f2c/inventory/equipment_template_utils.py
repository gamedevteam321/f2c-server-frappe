# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

import frappe
from frappe import _
from frappe.model.document import Document


def _norm_part(value: Any) -> str:
	"""Normalize a string for Item Code generation."""
	s = str(value or "").strip()
	if not s:
		return ""
	# Keep alphanumerics; convert runs of others to '-'
	s = re.sub(r"[^A-Za-z0-9]+", "-", s)
	s = s.strip("-")
	return s.upper()


def _build_item_code(parts: list[str], max_len: int = 140) -> str:
	code = "-".join([p for p in parts if p])
	code = re.sub(r"-{2,}", "-", code).strip("-")
	return code[:max_len] if max_len and len(code) > max_len else code


def generate_item_code_for_template(template: Document) -> str:
	"""
	Generate a deterministic-ish Item Code for a template.
	If the generated code already exists, caller should de-duplicate.
	"""
	doctype = str(template.doctype or "")
	abbr = {
		"Machinery Template": "MACH",
		"Implement Template": "IMP",
		"Hand Tool Template": "HTOOL",
		"Other Tool Template": "OTOOL",
	}.get(doctype, "EQT")

	# Type field differs per template doctype
	type_value = (
		getattr(template, "machinery_type", None)
		or getattr(template, "implement_type", None)
		or getattr(template, "hand_tool_type", None)
		or getattr(template, "other_tool_type", None)
		or ""
	)

	brand = getattr(template, "brand", "") or ""
	model = getattr(template, "model", "") or ""
	template_name = getattr(template, "template_name", "") or template.name or ""

	parts = [abbr, _norm_part(type_value), _norm_part(brand), _norm_part(model)]
	code = _build_item_code(parts)
	if not code:
		code = _build_item_code([abbr, _norm_part(template_name)]) or abbr
	return code


def _dedupe_item_code(base_code: str) -> str:
	"""Return a unique Item code based on base_code."""
	code = (base_code or "").strip()
	if not code:
		frappe.throw(_("Cannot generate Item Code (empty base code)."))

	if not frappe.db.exists("Item", code):
		return code

	# Append numeric suffix until unique
	for i in range(2, 5000):
		candidate = f"{code}-{i}"
		if not frappe.db.exists("Item", candidate):
			return candidate

	frappe.throw(_("Failed to generate unique Item Code (too many collisions)."))


def _template_to_item_payload(template: Document) -> Dict[str, Any]:
	"""
	Map template -> Item fields. Keep this conservative: only set fields that
	we are confident exist on ERPNext Item.
	"""
	item_name = ""
	brand = str(getattr(template, "brand", "") or "").strip()
	model = str(getattr(template, "model", "") or "").strip()
	if brand and model:
		item_name = f"{brand} {model}"
	elif brand:
		item_name = brand
	elif model:
		item_name = model
	else:
		item_name = str(getattr(template, "template_name", "") or "").strip() or str(template.name)

	item_group = getattr(template, "item_group", None)
	stock_uom = getattr(template, "stock_uom", None) or "Nos"
	asset_category = getattr(template, "asset_category", None)
	asset_naming_series = (getattr(template, "asset_naming_series", None) or "").strip() or None
	description = getattr(template, "default_description", None)

	payload: Dict[str, Any] = {
		"item_name": item_name,
		"item_group": item_group,
		"stock_uom": stock_uom,
		"description": description,
		"is_stock_item": 0,
		"is_fixed_asset": 1,
		"disabled": 0,
		"asset_category": asset_category,
		"asset_naming_series": asset_naming_series,
	}
	return payload


def create_or_update_item_from_template(
	template: Document,
	overwrite_existing: int = 0,
) -> Dict[str, Any]:
	"""
	Create an ERPNext Item from a template, or update linked Item when overwrite_existing=1.

	Returns: { ok: bool, item_code: str, action: 'created'|'updated'|'linked' }
	"""
	if not template:
		frappe.throw(_("Template is required."))

	overwrite = int(overwrite_existing or 0) == 1

	payload = _template_to_item_payload(template)
	missing = []
	for fieldname, label in [
		("item_group", "Item Group"),
		("stock_uom", "Stock UOM"),
		("asset_category", "Asset Category"),
		("asset_naming_series", "Asset Naming Series"),
	]:
		val = payload.get(fieldname)
		if val is None or (isinstance(val, str) and not val.strip()):
			missing.append(label)
	if missing:
		frappe.throw(_("Missing required template Item setting(s): {0}").format(", ".join(missing)))

	linked_item = getattr(template, "default_item_code", None)
	if linked_item:
		if not frappe.db.exists("Item", linked_item):
			# stale link; clear and treat as new
			linked_item = None
		elif not overwrite:
			return {"ok": True, "item_code": linked_item, "action": "linked"}

	if linked_item and overwrite:
		item = frappe.get_doc("Item", linked_item)
		item.update(payload)
		item.save(ignore_permissions=True)
		return {"ok": True, "item_code": item.name, "action": "updated"}

	base_code = generate_item_code_for_template(template)
	item_code = _dedupe_item_code(base_code)

	item = frappe.get_doc({"doctype": "Item", "item_code": item_code, **payload})
	item.insert(ignore_permissions=True)

	# Persist back to template
	try:
		template.db_set("default_item_code", item.name, update_modified=True)
	except Exception:
		# best-effort; don't fail Item creation if template update fails
		pass

	return {"ok": True, "item_code": item.name, "action": "created"}


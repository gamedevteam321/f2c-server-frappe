# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors

from __future__ import annotations

from typing import Any, List

import frappe
from frappe import _


EQUIPMENT_DOCTYPES = ("Machinery", "Implement", "Hand Tool", "Other Tool")


def _get_equipment_doc_for_asset(asset: str) -> tuple[str, str] | None:
	"""Return (doctype, name) of the equipment document that links to this asset, or None."""
	for doctype in EQUIPMENT_DOCTYPES:
		name = frappe.db.get_value(doctype, {"asset": asset}, "name")
		if name:
			return (doctype, name)
	return None


def _set_equipment_status(asset: str, status: str) -> None:
	equipment = _get_equipment_doc_for_asset(asset)
	if equipment:
		doctype, name = equipment
		frappe.db.set_value(doctype, name, "status", status)
		frappe.db.commit()


@frappe.whitelist()
def get_open_ticket_names_for_asset(asset: str) -> List[str]:
	"""Return list of Equipment Report Ticket names (Open) for the given asset, newest first."""
	if not (asset or "").strip():
		return []
	tickets = frappe.get_all(
		"Equipment Report Ticket",
		filters={"asset": asset.strip(), "status": "Open"},
		fields=["name"],
		order_by="creation desc",
	)
	return [t["name"] for t in tickets if t.get("name")]


@frappe.whitelist()
def get_reported_asset_names() -> List[str]:
	"""Return list of Asset names that have at least one Equipment Report Ticket with status Open."""
	result = frappe.get_all(
		"Equipment Report Ticket",
		filters={"status": "Open"},
		fields=["asset"],
		distinct=True,
	)
	return [r["asset"] for r in result if r.get("asset")]


@frappe.whitelist()
def get_open_ticket_summary_for_assets(assets: Any) -> dict[str, Any]:
	"""Return for each asset a list of open ticket summaries: { reason, creation } (newest first)."""
	if isinstance(assets, str):
		try:
			assets = frappe.parse_json(assets)
		except Exception:
			return {}
	if not isinstance(assets, list):
		return {}
	asset_set = {str(a).strip() for a in assets if a}
	if not asset_set:
		return {}
	tickets = frappe.get_all(
		"Equipment Report Ticket",
		filters={"status": "Open", "asset": ["in", list(asset_set)]},
		fields=["name", "asset", "reason", "creation", "image"],
		order_by="creation desc",
	)
	ticket_names = [t["name"] for t in tickets if t.get("name")]
	parts_by_parent: dict[str, list[dict[str, Any]]] = {}
	if ticket_names:
		part_rows = frappe.get_all(
			"Equipment Report Ticket Part",
			filters={"parent": ["in", ticket_names]},
			fields=["parent", "item_code", "item_name"],
		)
		for row in part_rows:
			parent = (row.get("parent") or "").strip()
			if not parent:
				continue
			if parent not in parts_by_parent:
				parts_by_parent[parent] = []
			parts_by_parent[parent].append({
				"item_code": (row.get("item_code") or "").strip(),
				"item_name": (row.get("item_name") or "").strip() or (row.get("item_code") or "").strip(),
			})
	out: dict[str, List[dict[str, Any]]] = {}
	for t in tickets:
		asset = (t.get("asset") or "").strip()
		if not asset:
			continue
		if asset not in out:
			out[asset] = []
		creation = t.get("creation")
		if isinstance(creation, str) and creation:
			creation = creation.split()[0] if " " in creation else creation
		image = (t.get("image") or "").strip()
		ticket_name = t.get("name") or ""
		parts = parts_by_parent.get(ticket_name, [])
		out[asset].append({
			"reason": (t.get("reason") or "").strip(),
			"creation": creation or "",
			"image": image,
			"parts": parts,
		})
	return out


@frappe.whitelist()
def create_equipment_report_ticket(
	asset: str,
	report_type: str,
	reason: str,
	image_upload: str | None = None,
	parts: Any = None,
) -> dict:
	"""Create an Equipment Report Ticket. For Repair, parts must be a list of {item_code: "..."}."""
	if not (asset or "").strip():
		frappe.throw(_("Asset is required."))
	if report_type not in ("Maintenance", "Repair"):
		frappe.throw(_("Report Type must be Maintenance or Repair."))
	if not (reason or "").strip():
		frappe.throw(_("Reason is required."))

	parts_list = _parse_parts(parts)
	if report_type == "Repair" and (not parts_list or len(parts_list) == 0):
		frappe.throw(_("At least one Part is required for Repair reports."))

	doc = frappe.get_doc(
		{
			"doctype": "Equipment Report Ticket",
			"asset": asset.strip(),
			"report_type": report_type,
			"reason": (reason or "").strip(),
			"status": "Open",
		}
	)
	if image_upload and str(image_upload).strip():
		# Store only path (e.g. /files/xyz.png); strip any full URL origin
		img = str(image_upload).strip()
		if img.startswith(("http://", "https://")):
			from urllib.parse import urlparse
			parsed = urlparse(img)
			img = parsed.path or img
		doc.image = img
	for item_code in parts_list:
		doc.append("parts", {"item_code": item_code})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	_set_equipment_status(asset.strip(), "Maintenance")

	return {"name": doc.name}


@frappe.whitelist()
def resolve_equipment_report_ticket(
	ticket_name: str,
	resolution_notes: str,
	resolution_date: str | None = None,
) -> dict:
	"""Set ticket status to Resolved and fill resolution fields. Optionally set equipment status to Available."""
	if not (ticket_name or "").strip():
		frappe.throw(_("Ticket name is required."))
	if not (resolution_notes or "").strip():
		frappe.throw(_("Resolution notes are required."))

	ticket = frappe.get_doc("Equipment Report Ticket", ticket_name)
	if ticket.status == "Resolved":
		frappe.throw(_("Ticket is already resolved."))
	asset = ticket.asset
	ticket.status = "Resolved"
	ticket.resolution_notes = (resolution_notes or "").strip()
	ticket.resolution_date = resolution_date or frappe.utils.getdate()
	ticket.save(ignore_permissions=True)
	frappe.db.commit()

	_set_equipment_status(asset, "Available")

	return {"name": ticket.name}


def _parse_parts(parts: Any) -> List[str]:
	"""Accept list of dicts with item_code or a JSON string; return list of item_code strings."""
	if not parts:
		return []
	if isinstance(parts, str):
		try:
			parts = frappe.parse_json(parts)
		except Exception:
			return []
	if not isinstance(parts, list):
		return []
	out = []
	for row in parts:
		if isinstance(row, dict):
			code = (row.get("item_code") or row.get("item") or "").strip()
		else:
			code = str(row).strip()
		if code:
			out.append(code)
	return out


LAYOUT_SKIP_FIELDTYPES = frozenset(("Section Break", "Column Break", "Button", "HTML"))


@frappe.whitelist()
def get_equipment_doctype_layout(doctype: str) -> dict[str, Any]:
	"""Return section-wise field layout for an equipment doctype (same order as edit form)."""
	if doctype not in EQUIPMENT_DOCTYPES:
		return {"sections": []}
	meta = frappe.get_meta(doctype)
	sections: List[dict[str, Any]] = []
	current: dict[str, Any] | None = None
	for df in meta.fields:
		if df.fieldtype == "Section Break":
			current = {
				"label": (df.label or "Details").strip() or "Details",
				"section_fieldname": df.fieldname,
				"fields": [],
			}
			sections.append(current)
		elif df.fieldtype in LAYOUT_SKIP_FIELDTYPES:
			continue
		elif current is not None:
			current["fields"].append({
				"fieldname": df.fieldname,
				"label": (df.label or df.fieldname or "").strip() or df.fieldname,
			})
		else:
			# Data fields before any section break
			if not sections or sections[-1].get("label") != "Details":
				current = {"label": "Details", "section_fieldname": None, "fields": []}
				sections.append(current)
			current["fields"].append({
				"fieldname": df.fieldname,
				"label": (df.label or df.fieldname or "").strip() or df.fieldname,
			})
	if not sections:
		current = {"label": "Details", "section_fieldname": None, "fields": []}
		for df in meta.fields:
			if df.fieldtype not in LAYOUT_SKIP_FIELDTYPES and df.fieldtype != "Section Break":
				current["fields"].append({
					"fieldname": df.fieldname,
					"label": (df.label or df.fieldname or "").strip() or df.fieldname,
				})
		if current["fields"]:
			sections.append(current)
	return {"sections": sections}

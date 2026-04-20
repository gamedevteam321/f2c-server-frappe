# -*- coding: utf-8 -*-
"""Plan field→cluster→field LTT legs when a tractor must swap or pick up an implement for a schedule."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import frappe

if TYPE_CHECKING:
	pass


@dataclass
class FieldTractorImplementRoundTrip:
	leg1_assets: list[dict[str, Any]]
	leg2_assets: list[dict[str, Any]]
	transport_vehicle: str | None
	standalone_implement_asset_to_skip: str | None


def _machinery_child_rows(schedule_doc: Any) -> list[Any]:
	rows = (
		(schedule_doc.get("machinery") or [])
		if callable(getattr(schedule_doc, "get", None))
		else (getattr(schedule_doc, "machinery", None) or [])
	)
	return list(rows)


def _tractor_assets_on_schedule(schedule_doc: Any) -> list[str]:
	"""Machinery row assets whose Machinery doc is a Tractor (order preserved)."""
	out: list[str] = []
	for row in _machinery_child_rows(schedule_doc):
		a = getattr(row, "asset", None) or (row.get("asset") if isinstance(row, dict) else None)
		if not a:
			continue
		mt = frappe.db.get_value("Machinery", {"asset": a}, "machinery_type")
		if (mt or "").strip() == "Tractor":
			out.append(a)
	return out


def implement_doc_names_paired_to_tractors_on_schedule(schedule_doc: Any) -> set[str]:
	"""Implement doc names set as paired_implement on any machinery row."""
	names: set[str] = set()
	for row in _machinery_child_rows(schedule_doc):
		pi = getattr(row, "paired_implement", None) or (row.get("paired_implement") if isinstance(row, dict) else None)
		pi = (pi or "").strip()
		if pi and frappe.db.exists("Implement", pi):
			names.add(pi)
	return names


def implement_asset_ids_paired_to_tractors_on_schedule(schedule_doc: Any) -> set[str]:
	"""
	Asset names for implements that must not get a standalone machinery LTT.

	Includes: paired_implement on machinery rows (and child-row assets tied to that
	name). When the schedule has exactly one tractor and exactly one implements child
	line (common when paired_implement was not filled in the UI), that implement asset
	is treated as moving with that tractor.
	"""
	doc_names = implement_doc_names_paired_to_tractors_on_schedule(schedule_doc)
	out: set[str] = set()
	for impl_name in doc_names:
		asset = frappe.db.get_value("Implement", impl_name, "asset")
		if asset:
			out.add(asset)

	impl_rows = (
		(schedule_doc.get("implements") or [])
		if callable(getattr(schedule_doc, "get", None))
		else (getattr(schedule_doc, "implements", None) or [])
	)
	for row in impl_rows or []:
		ra = getattr(row, "asset", None) or (row.get("asset") if isinstance(row, dict) else None)
		if not ra:
			continue
		nm = frappe.db.get_value("Implement", {"asset": ra}, "name")
		if nm and nm in doc_names:
			out.add(ra)

	if len(_tractor_assets_on_schedule(schedule_doc)) == 1 and len(impl_rows) == 1:
		row0 = impl_rows[0]
		ra = getattr(row0, "asset", None) or (row0.get("asset") if isinstance(row0, dict) else None)
		if ra:
			out.add(ra)
	return out


def implement_asset_ids_paired_but_unattached_from_machinery_child_rows(machinery_rows: list[Any]) -> set[str]:
	"""
	Implement Asset names on rows with paired_implement (Tractor rows only) where DB attachment
	is not in sync (return LTT / execution: omit standalone implement move).

	Rows may be Crop Plan Schedule Machinery, On Demand Activity Machinery, or Farm Task
	Execution Equipment (same asset + paired_implement shape).
	"""
	out: set[str] = set()
	for row in machinery_rows or []:
		tractor_asset = getattr(row, "asset", None) or (row.get("asset") if isinstance(row, dict) else None)
		pi = getattr(row, "paired_implement", None) or (row.get("paired_implement") if isinstance(row, dict) else None)
		pi = (pi or "").strip()
		if not tractor_asset or not pi or not frappe.db.exists("Implement", pi):
			continue
		mt = frappe.db.get_value("Machinery", {"asset": tractor_asset}, "machinery_type")
		if (mt or "").strip() != "Tractor":
			continue
		mach_name = frappe.db.get_value("Machinery", {"asset": tractor_asset}, "name")
		if not mach_name:
			continue
		cur = frappe.db.get_value("Machinery", mach_name, "current_implement") or None
		attached_to = frappe.db.get_value("Implement", pi, "attached_to_machinery") or None
		if cur == pi and attached_to == mach_name:
			continue
		impl_asset = frappe.db.get_value("Implement", pi, "asset")
		if impl_asset:
			out.add(impl_asset)
	return out


def implement_asset_ids_paired_but_unattached(schedule_doc: Any) -> set[str]:
	"""
	Implement Asset names listed as paired_implement on a schedule/activity machinery row but
	not physically linked (return LTT: omit standalone row; leave implement in field).

	Does not affect round-trip planning helpers that pass required implement before attach.
	"""
	return implement_asset_ids_paired_but_unattached_from_machinery_child_rows(_machinery_child_rows(schedule_doc))


def _resolve_required_implement_for_tractor_asset(
	schedule_doc: Any, tractor_asset: str
) -> tuple[str | None, str | None]:
	"""
	Return (Implement doc name, implement Asset name) for logistics / dedupe.

	Schedule UIs store the activity-required implement as `paired_implement` on the
	machinery child row (same row as the tractor asset). The separate `implements`
	child table is optional.
	"""
	for row in _machinery_child_rows(schedule_doc):
		row_asset = getattr(row, "asset", None) or (row.get("asset") if isinstance(row, dict) else None)
		if not row_asset or row_asset != tractor_asset:
			continue
		pi = getattr(row, "paired_implement", None) or (row.get("paired_implement") if isinstance(row, dict) else None)
		pi = (pi or "").strip()
		if pi and frappe.db.exists("Implement", pi):
			impl_asset = frappe.db.get_value("Implement", pi, "asset")
			return (pi, impl_asset or None)
		break

	impl_rows = (
		(schedule_doc.get("implements") or [])
		if callable(getattr(schedule_doc, "get", None))
		else (getattr(schedule_doc, "implements", None) or [])
	)
	if len(impl_rows) != 1:
		return (None, None)
	req_asset = getattr(impl_rows[0], "asset", None) or (impl_rows[0].get("asset") if isinstance(impl_rows[0], dict) else None)
	if not req_asset:
		return (None, None)
	req_impl_name = frappe.db.get_value("Implement", {"asset": req_asset}, "name")
	if not req_impl_name:
		return (None, None)
	return (req_impl_name, req_asset)


def plan_field_tractor_implement_round_trip(
	schedule_doc: Any,
	primary_asset: str,
) -> Optional[FieldTractorImplementRoundTrip]:
	"""
	When the schedule requires an implement (paired_implement or a single implements
	child line) that differs from current_implement, return leg1+leg2 asset payloads
	(field→cluster, then cluster→field). Implement swap (current set and different):
	allowed even if Asset→warehouse is not classified as field. Pure attach (no
	current implement): only when tractor is staged at field (warehouse matches
	activity field warehouse, or Machinery/geo classifies as field). Otherwise None.
	"""
	field_name = getattr(schedule_doc, "field", None)
	if not field_name:
		return None

	cluster_wh = schedule_doc._get_cluster_warehouse_for_field(field_name)
	target_wh = schedule_doc._get_target_warehouse_for_field(field_name)
	if not cluster_wh or not target_wh or cluster_wh == target_wh:
		return None

	from_wh = schedule_doc._get_source_warehouse_for_equipment_asset(primary_asset)
	if not from_wh:
		return None

	from f2c.inventory.equipment_location_level import (
		location_level_for_equipment_doc,
		location_warehouse_level_for_warehouse_name,
	)

	mach = frappe.db.get_value(
		"Machinery",
		{"asset": primary_asset},
		["name", "machinery_type", "current_implement"],
		as_dict=True,
	)
	if not mach or (mach.get("machinery_type") or "").strip() != "Tractor":
		return None

	req_impl_name, req_asset = _resolve_required_implement_for_tractor_asset(schedule_doc, primary_asset)
	if not req_impl_name:
		return None

	cur_impl = mach.get("current_implement") or None
	if cur_impl == req_impl_name:
		return None

	# Implement swap/replace: do not require tractor source to classify as "field" — Asset→warehouse
	# often resolves to cluster/stock while the tractor is still operationally in the field for this activity.
	# Pure attach (no current implement): require tractor staged at field to avoid useless round-trips.
	# If Asset→warehouse is already this activity's field warehouse (from_wh == target_wh), treat as field
	# even when geo/doc level mislabels the warehouse (implements often staged at cluster while tractor is in field).
	implement_swap = bool(cur_impl and cur_impl != req_impl_name)
	if not implement_swap:
		at_activity_field_warehouse = bool(from_wh and target_wh and from_wh == target_wh)
		if not at_activity_field_warehouse:
			wh_level = location_warehouse_level_for_warehouse_name(from_wh)
			doc_level = location_level_for_equipment_doc("Machinery", mach.get("name"))
			if wh_level != "field" and doc_level != "field":
				return None

	from f2c.inventory.logistics_transfer_ticket_api import (
		expand_machinery_transfer_asset_requests,
		self_transport_machinery_name_for_asset,
	)

	tv = self_transport_machinery_name_for_asset(primary_asset)

	if cur_impl:
		leg1 = expand_machinery_transfer_asset_requests(primary_asset, cur_impl)
		leg2 = expand_machinery_transfer_asset_requests(primary_asset, req_impl_name)
	else:
		leg1 = [{"asset": primary_asset, "qty": 1}]
		leg2 = expand_machinery_transfer_asset_requests(primary_asset, req_impl_name)

	standalone_asset = req_asset or frappe.db.get_value("Implement", req_impl_name, "asset")
	return FieldTractorImplementRoundTrip(
		leg1_assets=leg1,
		leg2_assets=leg2,
		transport_vehicle=tv,
		standalone_implement_asset_to_skip=standalone_asset or None,
	)

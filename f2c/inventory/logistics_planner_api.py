from __future__ import annotations

import json
from typing import Any, Dict, List

import frappe
from frappe.utils import flt, now_datetime

from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket


def _get_machinery_planner_context(asset: str | None) -> Dict[str, Any]:
	result = {
		"current_implement": None,
		"current_implement_name": None,
	}
	if not asset:
		return result

	machinery = frappe.db.get_value(
		"Machinery",
		{"asset": asset},
		["name", "machinery_type", "current_implement"],
		as_dict=True,
	)
	if not machinery:
		return result

	result["current_implement"] = machinery.get("current_implement") or None
	if result["current_implement"]:
		result["current_implement_name"] = (
			frappe.db.get_value("Implement", result["current_implement"], "implement_name")
			or result["current_implement"]
		)
	return result


def _get_asset_source_warehouse(source_doc, asset: str | None, fallback_warehouse: str | None) -> str | None:
	if not asset:
		return fallback_warehouse

	current_location = frappe.db.get_value("Asset", asset, "location")
	if current_location and hasattr(source_doc, "_get_warehouse_from_location"):
		source_warehouse = source_doc._get_warehouse_from_location(current_location)
		if source_warehouse:
			return source_warehouse

	return fallback_warehouse


def _collect_input_rows(source_doc, source_warehouse: str | None, target_warehouse: str | None) -> List[Dict[str, Any]]:
	items = []
	collect_input_items = getattr(source_doc, "_collect_input_items", None)
	if callable(collect_input_items):
		items = collect_input_items() or []
	else:
		for inp in getattr(source_doc, "inputs", []) or []:
			item_code = getattr(inp, "item", None) or inp.get("item")
			qty = flt(getattr(inp, "total_quantity_to_use", None) or inp.get("total_quantity_to_use"))
			if item_code and qty > 0:
				items.append({"item_code": item_code, "qty": qty})

	return [
		{
			"transfer_category": "stocks",
			"source_warehouse": source_warehouse,
			"target_warehouse": target_warehouse,
			"stock_item": item.get("item_code"),
			"qty": flt(item.get("qty")),
			"action_type": "move",
			"group_key": _get_non_machinery_group_key(source_warehouse, target_warehouse),
		}
		for item in items
		if item.get("item_code") and flt(item.get("qty")) > 0
	]


def _get_non_machinery_group_key(source_warehouse: str | None, target_warehouse: str | None) -> str:
	return f"non_machinery::{source_warehouse or ''}::{target_warehouse or ''}"


def _build_draft_planner_rows(source_doc) -> List[Dict[str, Any]]:
	field = getattr(source_doc, "field", None)
	target_warehouse = source_doc._get_target_warehouse_for_field(field) if field else None
	cluster_warehouse = source_doc._get_cluster_warehouse_for_field(field) if field else None

	rows: List[Dict[str, Any]] = []

	for machinery_row in getattr(source_doc, "machinery", []) or []:
		asset = getattr(machinery_row, "asset", None) or machinery_row.get("asset")
		context = _get_machinery_planner_context(asset)
		rows.append(
			{
				"transfer_category": "machinery",
				"source_warehouse": _get_asset_source_warehouse(source_doc, asset, cluster_warehouse),
				"target_warehouse": target_warehouse,
				"asset": asset,
				"primary_asset": asset,
				"paired_implement": getattr(machinery_row, "paired_implement", None)
				or machinery_row.get("paired_implement"),
				"current_implement": context.get("current_implement"),
				"action_type": "move",
				"group_key": f"machinery::{asset or ''}",
			}
		)

	for tool_row in getattr(source_doc, "hand_tools", []) or []:
		asset = getattr(tool_row, "asset", None) or tool_row.get("asset")
		source_warehouse = _get_asset_source_warehouse(source_doc, asset, cluster_warehouse)
		rows.append(
			{
				"transfer_category": "hand_tools",
				"source_warehouse": source_warehouse,
				"target_warehouse": target_warehouse,
				"asset": asset,
				"primary_asset": asset,
				"qty": 1,
				"action_type": "move",
				"group_key": _get_non_machinery_group_key(source_warehouse, target_warehouse),
			}
		)

	for tool_row in getattr(source_doc, "other_tools", []) or []:
		asset = getattr(tool_row, "asset", None) or tool_row.get("asset")
		source_warehouse = _get_asset_source_warehouse(source_doc, asset, cluster_warehouse)
		rows.append(
			{
				"transfer_category": "other_tools",
				"source_warehouse": source_warehouse,
				"target_warehouse": target_warehouse,
				"asset": asset,
				"primary_asset": asset,
				"qty": 1,
				"action_type": "move",
				"group_key": _get_non_machinery_group_key(source_warehouse, target_warehouse),
			}
		)

	input_source_helper = getattr(source_doc, "_get_source_warehouse_for_inputs", None)
	input_source_warehouse = input_source_helper() if callable(input_source_helper) else cluster_warehouse
	rows.extend(_collect_input_rows(source_doc, input_source_warehouse, target_warehouse))
	return rows


def create_or_refresh_draft_logistics_batch(source_doc) -> str | None:
	field = getattr(source_doc, "field", None)
	if not field:
		return None

	batch_values = {
		"source_doctype": source_doc.doctype,
		"source_name": source_doc.name,
		"batch_type": source_doc.doctype,
		"planning_status": "Draft",
		"field": field,
		"cluster": source_doc._get_cluster_for_field(field) if hasattr(source_doc, "_get_cluster_for_field") else None,
		"target_warehouse": source_doc._get_target_warehouse_for_field(field)
		if hasattr(source_doc, "_get_target_warehouse_for_field")
		else None,
	}
	planner_rows = _build_draft_planner_rows(source_doc)

	existing_batch_name = frappe.db.get_value(
		"Logistics Batch",
		{
			"source_doctype": source_doc.doctype,
			"source_name": source_doc.name,
			"planning_status": "Draft",
		},
		"name",
	)

	if existing_batch_name:
		batch_doc = frappe.get_doc("Logistics Batch", existing_batch_name)
		batch_doc.update(batch_values)
		batch_doc.set("planner_rows", [])
		for row in planner_rows:
			batch_doc.append("planner_rows", row)
		batch_doc.save(ignore_permissions=True)
		return batch_doc.name

	batch_doc = frappe.get_doc({"doctype": "Logistics Batch", **batch_values, "planner_rows": []})
	for row in planner_rows:
		batch_doc.append("planner_rows", row)
	batch_doc.insert(ignore_permissions=True)
	return batch_doc.name


@frappe.whitelist()
def approve_logistics_batch(batch_name: str) -> List[str]:
	batch_doc = frappe.get_doc("Logistics Batch", batch_name)
	if (getattr(batch_doc, "planning_status", None) or "Draft") != "Draft":
		frappe.throw("Only draft logistics batches can be approved")

	created_ticket_names: List[str] = []

	for request in _build_ticket_requests(batch_doc):
		rows = request.get("rows") or []
		transfer_categories = [row.get("transfer_category") for row in rows if row.get("transfer_category")]
		transfer_category = transfer_categories[0] if len(set(transfer_categories)) == 1 else None
		planner_row_ref = rows[0].get("name") if len(rows) == 1 else None
		stock_items = [
			{
				"item_code": row.get("stock_item"),
				"qty": flt(row.get("qty") or 0),
			}
			for row in rows
			if row.get("transfer_category") == "stocks" and row.get("stock_item")
		]
		assets = [
			{
				"asset": row.get("asset"),
				"qty": flt(row.get("qty") or 1),
			}
			for row in rows
			if row.get("transfer_category") != "stocks" and row.get("asset")
		]

		if stock_items:
			result = create_logistics_transfer_ticket(
				from_warehouse=request.get("source_warehouse"),
				to_warehouse=request.get("target_warehouse"),
				stock_items=stock_items,
				assets=assets or None,
			)
		else:
			result = create_logistics_transfer_ticket(
				from_warehouse=request.get("source_warehouse"),
				to_warehouse=request.get("target_warehouse"),
				stock_items=None,
				assets=assets,
			)

		ticket_name = result.get("ticket") if result else None
		if not ticket_name:
			continue

		ticket_doc = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
		ticket_doc.logistics_batch = batch_doc.name
		ticket_doc.source_doctype = batch_doc.source_doctype
		ticket_doc.source_name = batch_doc.source_name
		ticket_doc.transfer_category = transfer_category
		ticket_doc.planner_row_ref = planner_row_ref

		for asset_row in getattr(ticket_doc, "asset_items", []) or []:
			for row in rows:
				if asset_row.get("asset") == row.get("asset"):
					asset_row.paired_implement = row.get("paired_implement")
					asset_row.current_implement = row.get("current_implement")
					asset_row.action_type = row.get("action_type")
					break

		ticket_doc.save(ignore_permissions=True)
		created_ticket_names.append(ticket_name)

	batch_doc.planning_status = "Approved"
	batch_doc.approved_on = now_datetime()
	batch_doc.approved_by = frappe.session.user
	batch_doc.save(ignore_permissions=True)
	return created_ticket_names


def _ensure_draft_batch(batch_doc) -> None:
	if (getattr(batch_doc, "planning_status", None) or "Draft") != "Draft":
		frappe.throw("Only draft logistics batches can be edited")


def _row_to_dict(row) -> Dict[str, Any]:
	as_dict = getattr(row, "as_dict", None)
	if callable(as_dict):
		return as_dict()
	return dict(row)


def _planner_row_requires_ticket(row) -> bool:
	source_warehouse = row.get("source_warehouse")
	target_warehouse = row.get("target_warehouse")
	return not (source_warehouse and target_warehouse and source_warehouse == target_warehouse)


def _is_grouped_non_machinery_category(transfer_category: str | None) -> bool:
	return transfer_category in {"stocks", "hand_tools", "other_tools"}


def _sync_row_group_key(row) -> None:
	transfer_category = row.get("transfer_category")
	setter = getattr(row, "set", None)

	def set_value(key: str, value: str) -> None:
		if callable(setter):
			setter(key, value)
		else:
			row[key] = value

	if _is_grouped_non_machinery_category(transfer_category):
		set_value(
			"group_key",
			_get_non_machinery_group_key(row.get("source_warehouse"), row.get("target_warehouse")),
		)
	elif transfer_category == "machinery":
		set_value("group_key", f"machinery::{row.get('asset') or ''}")


def _get_ticket_request_group_key(row) -> str:
	transfer_category = row.get("transfer_category")
	if _is_grouped_non_machinery_category(transfer_category):
		return _get_non_machinery_group_key(row.get("source_warehouse"), row.get("target_warehouse"))
	return row.get("name")


def _build_ticket_requests(batch_doc) -> List[Dict[str, Any]]:
	requests: List[Dict[str, Any]] = []
	request_by_key: Dict[str, Dict[str, Any]] = {}

	for row in getattr(batch_doc, "planner_rows", []) or []:
		if not _planner_row_requires_ticket(row):
			continue

		group_key = _get_ticket_request_group_key(row)
		request = request_by_key.get(group_key)
		if request:
			request["rows"].append(row)
			continue

		request = {
			"group_key": group_key,
			"rows": [row],
			"source_warehouse": row.get("source_warehouse"),
			"target_warehouse": row.get("target_warehouse"),
		}
		request_by_key[group_key] = request
		requests.append(request)

	return requests


@frappe.whitelist()
def get_logistics_batch_list(planning_status: str = "Draft") -> List[Dict[str, Any]]:
	filters = {}
	if planning_status:
		filters["planning_status"] = planning_status
	return frappe.get_all(
		"Logistics Batch",
		filters=filters,
		fields=["name", "source_doctype", "source_name", "field", "cluster", "target_warehouse", "planning_status", "modified"],
		order_by="modified desc",
		limit=100,
	)


@frappe.whitelist()
def get_logistics_batch_detail(batch_name: str) -> Dict[str, Any]:
	batch_doc = frappe.get_doc("Logistics Batch", batch_name)
	return batch_doc.as_dict()


@frappe.whitelist()
def update_logistics_batch_planner_row(batch_name: str, row_name: str, updates: str | Dict[str, Any]) -> Dict[str, Any]:
	batch_doc = frappe.get_doc("Logistics Batch", batch_name)
	_ensure_draft_batch(batch_doc)
	payload = json.loads(updates) if isinstance(updates, str) else dict(updates or {})

	for row in getattr(batch_doc, "planner_rows", []) or []:
		if row.get("name") != row_name:
			continue
		for key, value in payload.items():
			row.set(key, value)
		_sync_row_group_key(row)
		batch_doc.save(ignore_permissions=True)
		return _row_to_dict(row)

	frappe.throw(f"Planner row {row_name} was not found in batch {batch_name}")


@frappe.whitelist()
def add_manual_logistics_batch_row(batch_name: str, row_data: str | Dict[str, Any]) -> Dict[str, Any]:
	batch_doc = frappe.get_doc("Logistics Batch", batch_name)
	_ensure_draft_batch(batch_doc)
	payload = json.loads(row_data) if isinstance(row_data, str) else dict(row_data or {})
	payload.setdefault("action_type", "manual_add")
	batch_doc.append("planner_rows", payload)
	_sync_row_group_key(batch_doc.planner_rows[-1])
	batch_doc.save(ignore_permissions=True)
	return _row_to_dict(batch_doc.planner_rows[-1])


@frappe.whitelist()
def request_implement_recall(
	batch_name: str,
	implement: str,
	from_asset: str | None = None,
	source_warehouse: str | None = None,
	target_warehouse: str | None = None,
) -> Dict[str, Any]:
	batch_doc = frappe.get_doc("Logistics Batch", batch_name)
	_ensure_draft_batch(batch_doc)
	row = {
		"transfer_category": "implement_recall",
		"source_warehouse": source_warehouse,
		"target_warehouse": target_warehouse,
		"primary_asset": from_asset,
		"paired_implement": implement,
		"action_type": "recall_to_cluster",
		"group_key": f"implement_recall::{implement}::{from_asset or ''}",
	}
	batch_doc.append("planner_rows", row)
	batch_doc.save(ignore_permissions=True)
	return _row_to_dict(batch_doc.planner_rows[-1])


@frappe.whitelist()
def add_replacement_logistics_batch_row(
	batch_name: str,
	asset: str,
	paired_implement: str | None = None,
	source_warehouse: str | None = None,
	target_warehouse: str | None = None,
) -> Dict[str, Any]:
	batch_doc = frappe.get_doc("Logistics Batch", batch_name)
	_ensure_draft_batch(batch_doc)
	row = {
		"transfer_category": "replacement",
		"asset": asset,
		"primary_asset": asset,
		"paired_implement": paired_implement,
		"source_warehouse": source_warehouse,
		"target_warehouse": target_warehouse,
		"action_type": "replace",
		"group_key": f"replacement::{asset}",
	}
	batch_doc.append("planner_rows", row)
	batch_doc.save(ignore_permissions=True)
	return _row_to_dict(batch_doc.planner_rows[-1])

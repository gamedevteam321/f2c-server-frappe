# -*- coding: utf-8 -*-
"""Resolve cluster-linked warehouses and ERPNext locations for a Geo Fencing field/block."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe


def resolve_cluster_warehouses_locations(field: Optional[str] = None, block: Optional[str] = None) -> Optional[Dict[str, Any]]:
	"""
	From a field or block Geo Fencing Area, find parent cluster and expand to all ledger warehouses
	and their mapped Location IDs (same logic as scheduling asset availability).

	Returns:
		dict with keys: cluster (str), warehouse_list (list[str]), location_list (list[str])
		or None if area/cluster/warehouses cannot be resolved.
	"""
	target_area = block or field
	if not target_area:
		return None

	cluster = None
	try:
		area_doc = frappe.get_doc("Geo Fencing Area", target_area)
		current = area_doc
		while current:
			if current.geo_fencing_type == "Cluster":
				cluster = current.name
				break
			if current.parent_area:
				current = frappe.get_doc("Geo Fencing Area", current.parent_area)
			else:
				break
	except Exception:
		return None

	if not cluster:
		return None

	cluster_geo_areas: List[str] = [cluster]
	try:
		child_areas = frappe.get_all(
			"Geo Fencing Area",
			filters={"parent_area": cluster},
			fields=["name"],
			limit_page_length=0,
		)
		for area in child_areas:
			cluster_geo_areas.append(area.name)
			blocks = frappe.get_all(
				"Geo Fencing Area",
				filters={"parent_area": area.name},
				fields=["name"],
				limit_page_length=0,
			)
			for b in blocks:
				cluster_geo_areas.append(b.name)
	except Exception:
		pass

	warehouses = frappe.get_all(
		"Geo Fencing Area Warehouse",
		fields=["warehouse"],
		filters={"parent": ["in", cluster_geo_areas], "parenttype": "Geo Fencing Area"},
		limit_page_length=0,
	)
	cluster_warehouse_list = [w.warehouse for w in warehouses if w.warehouse]

	from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses

	warehouse_list: List[str] = []
	for cluster_wh in cluster_warehouse_list:
		child_warehouses = get_child_warehouses(cluster_wh)
		warehouse_list.extend(child_warehouses)

	warehouse_list = list(dict.fromkeys(warehouse_list))
	if not warehouse_list:
		return None

	from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse

	location_list: List[str] = []
	for wh in warehouse_list:
		try:
			result = get_location_for_warehouse(wh)
			if result and result.get("location"):
				location_list.append(result.get("location"))
		except Exception:
			continue

	return {
		"cluster": cluster,
		"warehouse_list": warehouse_list,
		"location_list": location_list,
	}

# -*- coding: utf-8 -*-
"""
Equipment transfer logic when a Farm Task Execution is completed.

When an activity completes in execution:
- If another field in the same cluster has the same equipment scheduled after this activity,
  create transfer ticket(s) to that field.
- Otherwise create transfer ticket(s) to return equipment to the cluster.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import now_datetime


def get_cluster_for_field(field_name: str) -> Optional[str]:
	"""Get the cluster (Geo Fencing Area with type='Cluster') for a given field by traversing parent_area hierarchy."""
	if not field_name:
		return None

	try:
		field_doc = frappe.get_doc("Geo Fencing Area", field_name)
		if not field_doc:
			return None

		if getattr(field_doc, "geo_fencing_type", None) == "Cluster":
			return field_name

		current = field_doc
		visited = set()
		max_depth = 10
		depth = 0

		while current and getattr(current, "parent_area", None) and depth < max_depth:
			pa = current.parent_area
			if pa in visited:
				break
			visited.add(pa)
			parent = frappe.get_doc("Geo Fencing Area", pa)
			if getattr(parent, "geo_fencing_type", None) == "Cluster":
				return parent.name
			current = parent
			depth += 1

		return None
	except Exception as e:
		frappe.log_error(
			f"Error getting cluster for field {field_name}: {str(e)}",
			"Equipment Transfer on Completion",
		)
		return None


def get_target_warehouse_for_field(field_name: str) -> Optional[str]:
	"""Get target warehouse for field/block using get_warehouses_for_geo_area()."""
	if not field_name:
		return None

	try:
		from f2c.inventory.logistics_transfer_ticket_api import get_warehouses_for_geo_area

		result = get_warehouses_for_geo_area(field_name, strict_geo_area=1)
		warehouses = result.get("warehouses", []) if result else []
		if warehouses:
			return warehouses[0]
		return None
	except Exception as e:
		frappe.log_error(
			f"Error getting target warehouse for field {field_name}: {str(e)}",
			"Equipment Transfer on Completion",
		)
		return None


def get_cluster_warehouse_for_field(field_name: str) -> Optional[str]:
	"""
	Get cluster warehouse (parent warehouse) for a field.
	Warehouse hierarchy: Farm -> Cluster -> Field. Returns the Cluster Warehouse.
	"""
	if not field_name:
		return None

	try:
		field_warehouse = get_target_warehouse_for_field(field_name)
		if not field_warehouse:
			return None

		parent_warehouse = frappe.db.get_value("Warehouse", field_warehouse, "parent_warehouse")
		if parent_warehouse:
			return parent_warehouse

		cluster = get_cluster_for_field(field_name)
		if not cluster:
			return None

		cluster_warehouses = frappe.get_all(
			"Geo Fencing Area Warehouse",
			fields=["warehouse"],
			filters={"parent": cluster, "parenttype": "Geo Fencing Area"},
			limit=1,
		)
		if cluster_warehouses and cluster_warehouses[0].get("warehouse"):
			return cluster_warehouses[0].warehouse
		return None
	except Exception as e:
		frappe.log_error(
			f"Error getting cluster warehouse for field {field_name}: {str(e)}",
			"Equipment Transfer on Completion",
		)
		return None


def get_next_scheduled_activity_in_same_cluster(
	cluster: str,
	equipment_assets: List[str],
	after_time: str,
	exclude_schedule_ref: Optional[str] = None,
	exclude_oda_ref: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
	"""
	Return the next scheduled activity (Crop Plan Schedule or On Demand Activity) in the same cluster
	that uses at least one of the given assets and has planned_start > after_time.
	Returns None or {"field": "<field_name>", "ref_type": "Crop Plan Schedule"|"On Demand Activity", "ref_name": "..."}.
	"""
	if not cluster or not equipment_assets or not after_time:
		return None

	try:
		from frappe.utils import get_datetime

		after_dt = get_datetime(after_time)
		after_date = after_dt.date()
		placeholders = ", ".join(["%s"] * len(equipment_assets))
		params_base = equipment_assets * 4  # for the 4 UNION ALL tables

		# Fields in this cluster (direct children with type Field)
		fields_in_cluster = frappe.get_all(
			"Geo Fencing Area",
			filters={"parent_area": cluster, "geo_fencing_type": "Field"},
			pluck="name",
		)
		if not fields_in_cluster:
			return None

		# Build IN clause for fields (safe: we control the list)
		field_placeholders = ", ".join(["%s"] * len(fields_in_cluster))

		# Crop Plan Schedule: same cluster, same equipment, after_time, exclude ref
		cps_query = f"""
			SELECT cps.name, cps.field, cps.planned_start
			FROM `tabCrop Plan Schedule` cps
			INNER JOIN (
				SELECT parent, asset FROM `tabCrop Plan Schedule Machinery` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset FROM `tabCrop Plan Schedule Implement` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset FROM `tabCrop Plan Schedule Hand Tool` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset FROM `tabCrop Plan Schedule Other Tool` WHERE asset IN ({placeholders})
			) eq ON cps.name = eq.parent
			WHERE cps.status IN ('Scheduled', 'Reported')
				AND cps.planned_start IS NOT NULL
				AND DATE(cps.planned_start) = %s
				AND cps.planned_start > %s
				AND cps.field IN ({field_placeholders})
		"""
		cps_params = list(params_base) + [after_date, after_time] + list(fields_in_cluster)
		if exclude_schedule_ref:
			cps_query += " AND cps.name != %s"
			cps_params.append(exclude_schedule_ref)

		cps_query += " ORDER BY cps.planned_start ASC LIMIT 1"
		cps_results = frappe.db.sql(cps_query, cps_params, as_dict=True)

		# On Demand Activity: same cluster, same equipment, after_time, exclude ref
		oda_query = f"""
			SELECT oda.name, oda.field, oda.planned_start
			FROM `tabOn Demand Activity` oda
			INNER JOIN (
				SELECT parent, asset FROM `tabOn Demand Activity Machinery` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset FROM `tabOn Demand Activity Implement` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset FROM `tabOn Demand Activity Hand Tool` WHERE asset IN ({placeholders})
				UNION ALL
				SELECT parent, asset FROM `tabOn Demand Activity Other Tool` WHERE asset IN ({placeholders})
			) eq ON oda.name = eq.parent
			WHERE oda.status IN ('Scheduled', 'Reported')
				AND oda.planned_start IS NOT NULL
				AND DATE(oda.planned_start) = %s
				AND oda.planned_start > %s
				AND oda.field IN ({field_placeholders})
		"""
		oda_params = list(params_base) + [after_date, after_time] + list(fields_in_cluster)
		if exclude_oda_ref:
			oda_query += " AND oda.name != %s"
			oda_params.append(exclude_oda_ref)

		oda_query += " ORDER BY oda.planned_start ASC LIMIT 1"
		oda_results = frappe.db.sql(oda_query, oda_params, as_dict=True)

		# Pick the single earliest across both
		candidates = []
		if cps_results:
			r = cps_results[0]
			candidates.append(
				{"planned_start": r.planned_start, "field": r.field, "ref_type": "Crop Plan Schedule", "ref_name": r.name}
			)
		if oda_results:
			r = oda_results[0]
			candidates.append(
				{"planned_start": r.planned_start, "field": r.field, "ref_type": "On Demand Activity", "ref_name": r.name}
			)

		if not candidates:
			return None
		best = min(candidates, key=lambda x: (x["planned_start"] or ""))
		return {"field": best["field"], "ref_type": best["ref_type"], "ref_name": best["ref_name"]}

	except Exception as e:
		frappe.log_error(
			f"Error in get_next_scheduled_activity_in_same_cluster: {str(e)}",
			"Equipment Transfer on Completion",
		)
		return None


def create_equipment_transfer_tickets_for_execution(execution_doc) -> None:
	"""
	When execution completes: if another field in the same cluster has the same equipment
	scheduled after this activity, create transfer ticket(s) to that field; else to the cluster.
	Does not raise; logs and returns on any failure.
	"""
	try:
		assets = [row.asset for row in (execution_doc.equipment or []) if getattr(row, "asset", None)]
		if not assets or not getattr(execution_doc, "field", None):
			return

		# End time: actual_end, else from linked schedule/activity, else now
		after_time = getattr(execution_doc, "actual_end", None)
		if not after_time and getattr(execution_doc, "schedule_ref", None):
			vals = frappe.db.get_value(
				"Crop Plan Schedule",
				execution_doc.schedule_ref,
				["planned_end", "planned_start"],
			)
			if isinstance(vals, (list, tuple)) and len(vals) >= 2:
				after_time = vals[0] or vals[1]
			elif vals is not None:
				after_time = vals
		if not after_time and getattr(execution_doc, "on_demand_activity_ref", None):
			vals = frappe.db.get_value(
				"On Demand Activity",
				execution_doc.on_demand_activity_ref,
				["planned_end", "planned_start"],
			)
			if isinstance(vals, (list, tuple)) and len(vals) >= 2:
				after_time = vals[0] or vals[1]
			elif vals is not None:
				after_time = vals
		if not after_time:
			after_time = now_datetime()

		field = execution_doc.field
		cluster = get_cluster_for_field(field)

		next_act = None
		if cluster:
			next_act = get_next_scheduled_activity_in_same_cluster(
				cluster,
				assets,
				after_time,
				exclude_schedule_ref=getattr(execution_doc, "schedule_ref", None),
				exclude_oda_ref=getattr(execution_doc, "on_demand_activity_ref", None),
			)

		if next_act and next_act.get("field"):
			to_warehouse = get_target_warehouse_for_field(next_act["field"])
		else:
			to_warehouse = get_cluster_warehouse_for_field(field)

		from_warehouse = get_target_warehouse_for_field(field)
		if not from_warehouse or not to_warehouse:
			frappe.log_error(
				f"Execution {getattr(execution_doc, 'name', '?')}: missing from_warehouse or to_warehouse (from field {field})",
				"Equipment Transfer on Completion",
			)
			return
		if from_warehouse == to_warehouse:
			return

		# Ensure target has a location (required for asset transfer)
		from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse

		loc_result = get_location_for_warehouse(to_warehouse)
		if not loc_result or not loc_result.get("location"):
			frappe.log_error(
				f"Target warehouse {to_warehouse} has no mapped Location for execution {getattr(execution_doc, 'name', '?')}",
				"Equipment Transfer on Completion",
			)
			return

		# Duplicate check: recent ticket same from/to and same assets
		from frappe.utils import add_to_date

		recent_time = add_to_date(now_datetime(), seconds=-5)
		existing = frappe.get_all(
			"Logistics Transfer Ticket",
			filters={
				"from_warehouse": from_warehouse,
				"to_warehouse": to_warehouse,
				"status": ["!=", "Cancelled"],
				"creation": [">=", recent_time],
			},
			fields=["name"],
			limit=10,
		)
		asset_set = set(assets)
		for t in existing:
			try:
				ticket_doc = frappe.get_doc("Logistics Transfer Ticket", t.name)
				ticket_assets = {ai.asset for ai in (ticket_doc.get("asset_items") or []) if getattr(ai, "asset", None)}
				if ticket_assets and ticket_assets == asset_set:
					return  # skip duplicate
			except Exception:
				pass

		from f2c.inventory.logistics_transfer_ticket_api import create_logistics_transfer_ticket

		result = create_logistics_transfer_ticket(
			from_warehouse=from_warehouse,
			to_warehouse=to_warehouse,
			stock_items=None,
			assets=[{"asset": a, "qty": 1} for a in assets],
		)
		if result and result.get("ticket"):
			frappe.log_error(
				f"Created equipment transfer ticket {result.get('ticket')} for execution {getattr(execution_doc, 'name', '?')} "
				f"from {from_warehouse} to {to_warehouse}",
				"Equipment Transfer on Completion",
			)
	except Exception as e:
		frappe.log_error(
			f"create_equipment_transfer_tickets_for_execution failed: {str(e)}",
			"Equipment Transfer on Completion",
		)

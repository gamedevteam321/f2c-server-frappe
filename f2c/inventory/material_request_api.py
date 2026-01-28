# -*- coding: utf-8 -*-
# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, nowdate
from typing import List, Dict, Any, Optional


def _ledger_warehouse(warehouse: Optional[str]) -> Optional[str]:
	"""Return ledger (stock) warehouse for the given warehouse, if available."""
	if not warehouse:
		return None
	try:
		from f2c.inventory.warehouse_utils import get_ledger_warehouse
		return get_ledger_warehouse(warehouse) or warehouse
	except Exception:
		return warehouse


@frappe.whitelist()
def get_cluster_warehouse_for_warehouse(warehouse: str) -> Optional[str]:
	"""
	Get cluster ledger warehouse (stock-holding) for a given warehouse.
	
	Warehouse hierarchy: Farm Warehouse (top) -> Cluster Warehouse (middle) -> Field Warehouse (bottom)
	Returns the ledger warehouse for the cluster, not the group.
	"""
	if not warehouse:
		return None
	
	try:
		# Get parent_warehouse from warehouse (should be cluster warehouse)
		# Hierarchy: Farm -> Cluster -> Field
		parent_warehouse = frappe.db.get_value("Warehouse", warehouse, "parent_warehouse")
		if parent_warehouse:
			# Check if parent is cluster warehouse by checking its geo area
			geo_area_warehouses = frappe.get_all(
				"Geo Fencing Area Warehouse",
				fields=["parent", "parenttype"],
				filters={"warehouse": parent_warehouse},
				limit=1
			)
			
			if geo_area_warehouses:
				geo_area = geo_area_warehouses[0].parent
				area_type = frappe.db.get_value("Geo Fencing Area", geo_area, "geo_fencing_type")
				if area_type == "Cluster":
					return _ledger_warehouse(parent_warehouse)
			
			# If parent exists but not linked to cluster, check if it's a cluster warehouse by name pattern
			# or check its parent (could be farm -> cluster -> field)
			grandparent = frappe.db.get_value("Warehouse", parent_warehouse, "parent_warehouse")
			if grandparent:
				# Check if grandparent is farm and parent is cluster
				grandparent_geo = frappe.get_all(
					"Geo Fencing Area Warehouse",
					fields=["parent"],
					filters={"warehouse": grandparent},
					limit=1
				)
				if grandparent_geo:
					grandparent_area_type = frappe.db.get_value("Geo Fencing Area", grandparent_geo[0].parent, "geo_fencing_type")
					if grandparent_area_type == "Farm":
						# Parent is likely cluster
						return _ledger_warehouse(parent_warehouse)
		
		# Fallback: Get cluster from Geo Fencing Area linked to warehouse
		warehouse_geo_areas = frappe.get_all(
			"Geo Fencing Area Warehouse",
			fields=["parent"],
			filters={"warehouse": warehouse},
			limit=1
		)
		
		if warehouse_geo_areas:
			geo_area = warehouse_geo_areas[0].parent
			# Traverse up to find cluster
			current_area = frappe.get_doc("Geo Fencing Area", geo_area)
			while current_area:
				if current_area.geo_fencing_type == "Cluster":
					# Get cluster warehouse
					cluster_warehouses = frappe.get_all(
						"Geo Fencing Area Warehouse",
						fields=["warehouse"],
						filters={"parent": current_area.name, "parenttype": "Geo Fencing Area"},
						limit=1
					)
					if cluster_warehouses and cluster_warehouses[0].warehouse:
						return _ledger_warehouse(cluster_warehouses[0].warehouse)
					break
				if current_area.parent_area:
					current_area = frappe.get_doc("Geo Fencing Area", current_area.parent_area)
				else:
					break
		
		return None
	except Exception as e:
		frappe.log_error(f"Error getting cluster warehouse for warehouse {warehouse}: {str(e)}", "Material Request API")
		return None


@frappe.whitelist()
def get_farm_warehouse_for_warehouse(warehouse: str) -> Optional[str]:
	"""
	Get farm ledger warehouse (stock-holding) for a given warehouse.
	
	Warehouse hierarchy: Farm Warehouse (top) -> Cluster Warehouse (middle) -> Field Warehouse (bottom)
	Returns the ledger warehouse for the farm, not the group.
	"""
	if not warehouse:
		return None
	
	try:
		# Traverse up the warehouse hierarchy to find farm
		current_warehouse = warehouse
		visited = set()
		
		while current_warehouse and current_warehouse not in visited:
			visited.add(current_warehouse)
			
			# Check if current warehouse is linked to a farm
			geo_area_warehouses = frappe.get_all(
				"Geo Fencing Area Warehouse",
				fields=["parent"],
				filters={"warehouse": current_warehouse},
				limit=1
			)
			
			if geo_area_warehouses:
				geo_area = geo_area_warehouses[0].parent
				area_type = frappe.db.get_value("Geo Fencing Area", geo_area, "geo_fencing_type")
				if area_type == "Farm":
					# Get farm warehouse
					farm_warehouses = frappe.get_all(
						"Geo Fencing Area Warehouse",
						fields=["warehouse"],
						filters={"parent": geo_area, "parenttype": "Geo Fencing Area"},
						limit=1
					)
					if farm_warehouses and farm_warehouses[0].warehouse:
						return _ledger_warehouse(farm_warehouses[0].warehouse)
			
			# Move to parent warehouse
			parent = frappe.db.get_value("Warehouse", current_warehouse, "parent_warehouse")
			if not parent:
				break
			current_warehouse = parent
		
		return None
	except Exception as e:
		frappe.log_error(f"Error getting farm warehouse for warehouse {warehouse}: {str(e)}", "Material Request API")
		return None


@frappe.whitelist()
def create_material_request(
	warehouse: str,
	items: List[Dict[str, Any]],
	from_warehouse: Optional[str] = None,
	material_request_type: str = "Material Transfer",
	company: Optional[str] = None,
	notes: Optional[str] = None,
	link_to_ticket: Optional[str] = None
) -> Dict[str, Any]:
	"""
	Create a Material Request for items.
	
	Args:
		warehouse: Destination warehouse (field warehouse)
		items: List of items with item_code, qty, uom, etc.
		from_warehouse: Source warehouse (cluster/farm) - auto-determined if not provided
		material_request_type: "Material Transfer" or "Purchase"
		company: Company name - auto-determined if not provided
		notes: Additional notes
		link_to_ticket: Link to Logistics Transfer Ticket (optional)
	
	Returns:
		Dict with material_request name
	"""
	if not warehouse:
		frappe.throw(_("warehouse is required"))
	
	if not items or len(items) == 0:
		frappe.throw(_("items are required"))
	
	# Auto-determine company if not provided
	if not company:
		company = frappe.db.get_value("Warehouse", warehouse, "company")
		if not company:
			frappe.throw(_("Cannot determine company for warehouse {0}").format(warehouse))
	
	# Auto-determine from_warehouse if not provided and material_request_type is Material Transfer
	if not from_warehouse and material_request_type == "Material Transfer":
		from_warehouse = get_cluster_warehouse_for_warehouse(warehouse)
		if not from_warehouse:
			# Fallback to farm warehouse
			from_warehouse = get_farm_warehouse_for_warehouse(warehouse)
		if not from_warehouse:
			frappe.throw(_("Cannot determine source warehouse (cluster/farm) for warehouse {0}").format(warehouse))
	
	# Create Material Request
	material_request = frappe.new_doc("Material Request")
	material_request.update({
		"material_request_type": material_request_type,
		"transaction_date": nowdate(),
		"company": company,
		"set_warehouse": warehouse,
		"set_from_warehouse": from_warehouse if material_request_type == "Material Transfer" else None,
	})
	
	# Add items
	for item in items:
		item_code = item.get("item_code")
		qty = flt(item.get("qty", 0))
		
		if not item_code or qty <= 0:
			continue
		
		# Get item details
		item_doc = frappe.get_cached_doc("Item", item_code)
		uom = item.get("uom") or item_doc.stock_uom
		
		mr_item = material_request.append("items", {
			"item_code": item_code,
			"qty": qty,
			"uom": uom,
			"warehouse": warehouse,
			"from_warehouse": from_warehouse if material_request_type == "Material Transfer" else None,
			"schedule_date": item.get("schedule_date") or nowdate(),
		})
		
		# Set description if provided
		if item.get("description"):
			mr_item.description = item.get("description")
	
	# Add notes if provided
	if notes:
		# Material Request doesn't have a notes field by default, but we can add it as a comment
		pass  # Will add comment after save
	
	# Save Material Request
	material_request.insert(ignore_permissions=True)
	
	# Add comment with notes and link to ticket
	if notes or link_to_ticket:
		comment_text = ""
		if notes:
			comment_text += f"Notes: {notes}\n"
		if link_to_ticket:
			comment_text += f"Requested from Logistics Transfer Ticket: {link_to_ticket}"
			# Try to add custom field link if it exists
			try:
				if frappe.db.has_column("Material Request", "custom_requested_from_logistics_ticket"):
					material_request.db_set("custom_requested_from_logistics_ticket", link_to_ticket)
			except Exception:
				pass
		
		if comment_text:
			material_request.add_comment("Comment", comment_text)
	
	return {
		"material_request": material_request.name,
		"status": material_request.status
	}


@frappe.whitelist()
def create_material_request_from_ticket(
	ticket_name: str,
	items: Optional[List[Dict[str, Any]]] = None,
	material_request_type: str = "Material Transfer"
) -> Dict[str, Any]:
	"""
	Create Material Request from Logistics Transfer Ticket.
	
	Args:
		ticket_name: Name of Logistics Transfer Ticket
		items: Optional list of items to include (if not provided, uses items with low/zero balance from ticket)
		material_request_type: "Material Transfer" or "Purchase"
	
	Returns:
		Dict with material_request name
	"""
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	
	# Get ticket
	ticket = frappe.get_doc("Logistics Transfer Ticket", ticket_name)
	
	if not ticket.from_warehouse or not ticket.to_warehouse:
		frappe.throw(_("Ticket must have from_warehouse and to_warehouse"))
	
	# If items not provided, get items with low/zero balance from ticket
	if not items:
		items = []
		if ticket.stock_items:
			for stock_item in ticket.stock_items:
				available_balance = stock_item.get("available_balance")
				# Check if balance is 0 or "Not Available" or very low
				is_low_stock = False
				if available_balance:
					try:
						balance_float = flt(available_balance)
						is_low_stock = balance_float <= 0
					except (ValueError, TypeError):
						# If it's a string like "Not Available"
						is_low_stock = str(available_balance).lower() in ["not available", "0", "0.0"]
				else:
					is_low_stock = True
				
				if is_low_stock and stock_item.item_code:
					items.append({
						"item_code": stock_item.item_code,
						"qty": stock_item.qty,
						"uom": stock_item.uom,
					})
	
	if not items:
		frappe.throw(_("No items with low stock found in ticket or items list is empty"))
	
	# Create Material Request
	# For Material Transfer: from_warehouse is cluster/farm, warehouse is field
	# The ticket's from_warehouse is the source (cluster), to_warehouse is destination (field)
	result = create_material_request(
		warehouse=ticket.to_warehouse,  # Destination (field warehouse)
		items=items,
		from_warehouse=ticket.from_warehouse,  # Source (cluster/farm warehouse)
		material_request_type=material_request_type,
		company=frappe.db.get_value("Warehouse", ticket.to_warehouse, "company"),
		notes=f"Created from Logistics Transfer Ticket {ticket_name}",
		link_to_ticket=ticket_name
	)
	
	return result


@frappe.whitelist()
def link_material_request_to_ticket(material_request_name: str, ticket_name: str):
	"""
	Link Material Request to Logistics Transfer Ticket.
	
	Args:
		material_request_name: Name of Material Request
		ticket_name: Name of Logistics Transfer Ticket
	"""
	if not material_request_name or not ticket_name:
		frappe.throw(_("material_request_name and ticket_name are required"))
	
	material_request = frappe.get_doc("Material Request", material_request_name)
	
	# Try to add custom field link if it exists
	try:
		if frappe.db.has_column("Material Request", "custom_requested_from_logistics_ticket"):
			material_request.db_set("custom_requested_from_logistics_ticket", ticket_name)
	except Exception:
		pass
	
	# Add comment
	material_request.add_comment("Comment", f"Linked to Logistics Transfer Ticket: {ticket_name}")

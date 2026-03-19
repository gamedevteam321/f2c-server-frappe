# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


def seed_geo_fencing_type():
	"""Seed Geo Fencing Type with predefined types"""
	
	geo_fencing_types = [
		{
			"geo_fencing_type_name": "Farm",
			"level": 1,
			"shape_type": "Polygon",
			"color": "#10b981",
			"is_visible": 0,
			"auto_calculate_area_child": 1,
			"auto_calculate_center_by_child": 1,
			"auto_calculate_radius_by_child": 1
		},
		{
			"geo_fencing_type_name": "Cluster",
			"level": 2,
			"shape_type": "Polygon",
			"color": "#3b82f6",
			"is_visible": 0,
			"auto_calculate_area_child": 1,
			"auto_calculate_center_by_child": 1,
			"auto_calculate_radius_by_child": 1
		},
		{
			"geo_fencing_type_name": "Field",
			"level": 3,
			"shape_type": "Polygon",
			"color": "#f59e0b",
			"is_visible": 1,
			"auto_calculate_area_child": 0,
			"auto_calculate_center_by_child": 0,
			"auto_calculate_radius_by_child": 0
		},
		{
			"geo_fencing_type_name": "Block",
			"level": 4,
			"shape_type": "Polygon",
			"color": "#8b5cf6",
			"is_visible": 1,
			"auto_calculate_area_child": 0,
			"auto_calculate_center_by_child": 0,
			"auto_calculate_radius_by_child": 0
		},
		{
			"geo_fencing_type_name": "Row",
			"level": 5,
			"shape_type": "Polygon",
			"color": "#ec4899",
			"is_visible": 1,
			"auto_calculate_area_child": 0,
			"auto_calculate_center_by_child": 0,
			"auto_calculate_radius_by_child": 0
		}
	]
	
	for type_data in geo_fencing_types:
		# Check if geo fencing type already exists
		if not frappe.db.exists("Geo Fencing Type", type_data["geo_fencing_type_name"]):
			doc = frappe.get_doc({
				"doctype": "Geo Fencing Type",
				**type_data
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print(f"Created Geo Fencing Type: {type_data['geo_fencing_type_name']}")
		else:
			print(f"Geo Fencing Type already exists: {type_data['geo_fencing_type_name']}")


if __name__ == "__main__":
	frappe.connect()
	seed_geo_fencing_type()


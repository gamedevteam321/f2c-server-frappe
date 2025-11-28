# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


def seed_activity_group_type():
	"""Seed activity group types with predefined categories"""
	
	activity_group_types = [
		{
			"activity_group_type_name": "Field Planning",
			"code": "FP",
			"description": "Activities related to planning and preparation of fields for crop cultivation."
		},
		{
			"activity_group_type_name": "Lease Management",
			"code": "LM",
			"description": "Activities related to managing lease agreements and land leasing."
		},
		{
			"activity_group_type_name": "Land Preparation",
			"code": "LP",
			"description": "Activities involved in preparing the land for planting, including tilling, digging, and clearing."
		},
		{
			"activity_group_type_name": "Plant Management",
			"code": "PM",
			"description": "Activities related to planting, transplanting, and managing plant growth and development."
		},
		{
			"activity_group_type_name": "Floor Management",
			"code": "FM",
			"description": "Activities for managing the field floor including weeding, irrigation, and soil maintenance."
		},
		{
			"activity_group_type_name": "Nutrition Management",
			"code": "NM",
			"description": "Activities for applying fertilizers and nutrients to crops."
		},
		{
			"activity_group_type_name": "Plant Protection",
			"code": "PP",
			"description": "Activities for protecting plants from pests, diseases, and other threats."
		},
		{
			"activity_group_type_name": "Crop RecordKeeping",
			"code": "RK",
			"description": "Activities for monitoring, recording, and tracking crop data and observations."
		},
		{
			"activity_group_type_name": "Harvest Management",
			"code": "HM",
			"description": "Activities related to harvesting, crate management, and post-harvest handling."
		},
		{
			"activity_group_type_name": "On Field Storage & Processing",
			"code": "FS",
			"description": "Activities for on-field storage and processing of harvested crops."
		},
		{
			"activity_group_type_name": "Livestock Management",
			"code": "LS",
			"description": "Activities for managing livestock and animals on the farm."
		},
		{
			"activity_group_type_name": "Labour Management",
			"code": "LB",
			"description": "Activities for managing labor including onboarding, hiring, time tracking, and wage payment."
		},
		{
			"activity_group_type_name": "Equipment & Infra Management",
			"code": "EI",
			"description": "Activities for managing equipment and infrastructure including hiring, purchase, maintenance, and audits."
		},
		{
			"activity_group_type_name": "R&D/Trial Management",
			"code": "RT",
			"description": "Activities related to research and development, and managing trials."
		},
		{
			"activity_group_type_name": "Assurance Management",
			"code": "AM",
			"description": "Activities for quality assurance and compliance management."
		},
		{
			"activity_group_type_name": "Inputs Purchase",
			"code": "IP",
			"description": "Activities related to purchasing inputs like seeds, fertilizers, and other materials."
		},
		{
			"activity_group_type_name": "Inventory & Logistics Management",
			"code": "IL",
			"description": "Activities for managing inventory and logistics operations."
		}
	]
	
	for activity_group_type_data in activity_group_types:
		# Check if activity group type already exists
		existing_doc = frappe.db.get_value("Activity Group Type", {"activity_group_type_name": activity_group_type_data["activity_group_type_name"]}, "name")
		
		if existing_doc:
			# Update existing document
			doc = frappe.get_doc("Activity Group Type", existing_doc)
			doc.update(activity_group_type_data)
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			print(f"Updated Activity Group Type: {activity_group_type_data['activity_group_type_name']}")
		else:
			# Create new document
			doc = frappe.get_doc({
				"doctype": "Activity Group Type",
				**activity_group_type_data
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print(f"Created Activity Group Type: {activity_group_type_data['activity_group_type_name']}")


if __name__ == "__main__":
	frappe.connect()
	seed_activity_group_type()


# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


def seed_crop_stage():
	"""Seed crop stages with predefined stages for different crop types"""
	
	crop_stages = [
		{
			"crop_type": "Cereals/Grains",
			"stage": "Germination",
			"description": "Initial stage where the seed begins to sprout and develop roots and shoots."
		},
		{
			"crop_type": "Cereals/Grains",
			"stage": "Seedling",
			"description": "Early growth stage with first leaves appearing above ground."
		},
		{
			"crop_type": "Cereals/Grains",
			"stage": "Tillering",
			"description": "Stage where additional stems develop from the base of the plant."
		},
		{
			"crop_type": "Cereals/Grains",
			"stage": "Flowering",
			"description": "Reproductive stage where flowers or panicles emerge."
		},
		{
			"crop_type": "Cereals/Grains",
			"stage": "Maturity",
			"description": "Final stage where grains are fully developed and ready for harvest."
		},
		{
			"crop_type": "Pulses/Legumes",
			"stage": "Vegetative Growth",
			"description": "Active growth phase with leaf and stem development."
		},
		{
			"crop_type": "Pulses/Legumes",
			"stage": "Pod Formation",
			"description": "Stage where pods begin to form after flowering."
		},
		{
			"crop_type": "Root & Tuber Crops",
			"stage": "Tuber Initiation",
			"description": "Stage where underground tubers begin to form and develop."
		},
		{
			"crop_type": "Oilseed Crops",
			"stage": "Seed Development",
			"description": "Critical stage where seeds develop and accumulate oil content."
		},
		{
			"crop_type": "Spices & Condiments",
			"stage": "Harvest Ready",
			"description": "Final stage indicating the crop is ready for harvesting."
		}
	]
	
	for stage_data in crop_stages:
		# Check if crop type exists
		if not frappe.db.exists("Crop Type", stage_data["crop_type"]):
			print(f"Crop Type '{stage_data['crop_type']}' does not exist. Please seed crop types first. Skipping: {stage_data['stage']}")
			continue
		
		# Check if crop stage already exists (by checking stage name and crop type combination)
		existing = frappe.db.get_value(
			"Crop Stage",
			{"crop_type": stage_data["crop_type"], "stage": stage_data["stage"]},
			"name"
		)
		
		if not existing:
			doc = frappe.get_doc({
				"doctype": "Crop Stage",
				**stage_data
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print(f"Created Crop Stage: {stage_data['stage']} ({stage_data['crop_type']})")
		else:
			print(f"Crop Stage already exists: {stage_data['stage']} ({stage_data['crop_type']})")


if __name__ == "__main__":
	frappe.connect()
	seed_crop_stage()


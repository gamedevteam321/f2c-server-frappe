# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


def seed_crop_type():
	"""Seed crop types with predefined categories"""
	
	crop_types = [
		{
			"crop_type_name": "Cereals/Grains",
			"description": "Grown for their edible seeds; staple foods worldwide. Key Examples: Rice, Wheat, Maize (Corn), Barley, Oats, Rye."
		},
		{
			"crop_type_name": "Pulses/Legumes",
			"description": "Grown for their edible seeds (dry split or whole); high in protein. Key Examples: Chickpeas (Chana), Lentils (Dal/Masoor), Peas, Beans, Soybean, Pigeon Pea (Arhar/Toor)."
		},
		{
			"crop_type_name": "Oilseed Crops",
			"description": "Grown primarily for extracting vegetable oil. Key Examples: Mustard, Soybean, Groundnut (Peanut), Sunflower, Sesame, Palm Oil, Rapeseed (Canola)."
		},
		{
			"crop_type_name": "Root & Tuber Crops",
			"description": "Grown for their starchy, edible underground parts. Key Examples: Potato, Cassava, Sweet Potato, Yam, Carrot."
		},
		{
			"crop_type_name": "Fibre Crops",
			"description": "Grown for fiber used in textiles, ropes, and materials. Key Examples: Cotton, Jute, Hemp, Flax."
		},
		{
			"crop_type_name": "Sugar Crops",
			"description": "Crops used to produce sugar/sweeteners. Key Examples: Sugarcane, Sugar Beet."
		},
		{
			"crop_type_name": "Beverage/Stimulant",
			"description": "Crops used to produce drinks or mild stimulants. Key Examples: Tea, Coffee, Cocoa, Tobacco."
		},
		{
			"crop_type_name": "Spices & Condiments",
			"description": "Grown for flavoring, aroma, or color. Key Examples: Ginger, Turmeric, Black Pepper, Chili Pepper, Cardamom."
		}
	]
	
	for crop_type_data in crop_types:
		# Check if crop type already exists
		if not frappe.db.exists("Crop Type", crop_type_data["crop_type_name"]):
			doc = frappe.get_doc({
				"doctype": "Crop Type",
				**crop_type_data
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print(f"Created Crop Type: {crop_type_data['crop_type_name']}")
		else:
			print(f"Crop Type already exists: {crop_type_data['crop_type_name']}")


if __name__ == "__main__":
	frappe.connect()
	seed_crop_type()


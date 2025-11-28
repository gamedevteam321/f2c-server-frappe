# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


def seed_crop():
	"""Seed crops with predefined common crops mapped to crop types"""
	
	crops = [
		# Cereals/Grains
		{
			"crop_name": "Rice",
			"crop_type": "Cereals/Grains",
			"scientific_name": "Oryza sativa",
			"description": "Staple food crop, one of the most important cereals worldwide."
		},
		{
			"crop_name": "Wheat",
			"crop_type": "Cereals/Grains",
			"scientific_name": "Triticum aestivum",
			"description": "Major cereal crop used for making flour and bread."
		},
		{
			"crop_name": "Maize (Corn)",
			"crop_type": "Cereals/Grains",
			"scientific_name": "Zea mays",
			"description": "Versatile cereal crop used for food, feed, and industrial purposes."
		},
		{
			"crop_name": "Barley",
			"crop_type": "Cereals/Grains",
			"scientific_name": "Hordeum vulgare",
			"description": "Cereal grain used for food, animal feed, and brewing."
		},
		{
			"crop_name": "Oats",
			"crop_type": "Cereals/Grains",
			"scientific_name": "Avena sativa",
			"description": "Cereal grain known for its nutritional value."
		},
		{
			"crop_name": "Rye",
			"crop_type": "Cereals/Grains",
			"scientific_name": "Secale cereale",
			"description": "Cereal grain used for bread and animal feed."
		},
		
		# Pulses/Legumes
		{
			"crop_name": "Chickpeas (Chana)",
			"crop_type": "Pulses/Legumes",
			"scientific_name": "Cicer arietinum",
			"description": "Protein-rich legume, widely consumed in various cuisines."
		},
		{
			"crop_name": "Lentils (Dal/Masoor)",
			"crop_type": "Pulses/Legumes",
			"scientific_name": "Lens culinaris",
			"description": "Nutritious pulse crop, rich in protein and fiber."
		},
		{
			"crop_name": "Peas",
			"crop_type": "Pulses/Legumes",
			"scientific_name": "Pisum sativum",
			"description": "Versatile legume consumed fresh or dried."
		},
		{
			"crop_name": "Beans",
			"crop_type": "Pulses/Legumes",
			"scientific_name": "Phaseolus vulgaris",
			"description": "Common legume crop with high protein content."
		},
		{
			"crop_name": "Soybean",
			"crop_type": "Pulses/Legumes",
			"scientific_name": "Glycine max",
			"description": "Important legume for protein and oil production."
		},
		{
			"crop_name": "Pigeon Pea (Arhar/Toor)",
			"crop_type": "Pulses/Legumes",
			"scientific_name": "Cajanus cajan",
			"description": "Drought-resistant legume, important in tropical regions."
		},
		
		# Oilseed Crops
		{
			"crop_name": "Mustard",
			"crop_type": "Oilseed Crops",
			"scientific_name": "Brassica juncea",
			"description": "Oilseed crop used for cooking oil and condiments."
		},
		{
			"crop_name": "Groundnut (Peanut)",
			"crop_type": "Oilseed Crops",
			"scientific_name": "Arachis hypogaea",
			"description": "Important oilseed and food crop."
		},
		{
			"crop_name": "Sunflower",
			"crop_type": "Oilseed Crops",
			"scientific_name": "Helianthus annuus",
			"description": "Oilseed crop known for its high oil content."
		},
		{
			"crop_name": "Sesame",
			"crop_type": "Oilseed Crops",
			"scientific_name": "Sesamum indicum",
			"description": "Ancient oilseed crop with high nutritional value."
		},
		{
			"crop_name": "Palm Oil",
			"crop_type": "Oilseed Crops",
			"scientific_name": "Elaeis guineensis",
			"description": "Major oil crop for edible oil production."
		},
		{
			"crop_name": "Rapeseed (Canola)",
			"crop_type": "Oilseed Crops",
			"scientific_name": "Brassica napus",
			"description": "Important oilseed crop for cooking oil."
		},
		
		# Root & Tuber Crops
		{
			"crop_name": "Potato",
			"crop_type": "Root & Tuber Crops",
			"scientific_name": "Solanum tuberosum",
			"description": "Staple tuber crop, one of the world's most important food crops."
		},
		{
			"crop_name": "Cassava",
			"crop_type": "Root & Tuber Crops",
			"scientific_name": "Manihot esculenta",
			"description": "Tropical root crop, important food source in many regions."
		},
		{
			"crop_name": "Sweet Potato",
			"crop_type": "Root & Tuber Crops",
			"scientific_name": "Ipomoea batatas",
			"description": "Nutritious root crop with high vitamin content."
		},
		{
			"crop_name": "Yam",
			"crop_type": "Root & Tuber Crops",
			"scientific_name": "Dioscorea spp.",
			"description": "Tropical tuber crop, important in many cuisines."
		},
		{
			"crop_name": "Carrot",
			"crop_type": "Root & Tuber Crops",
			"scientific_name": "Daucus carota",
			"description": "Root vegetable rich in beta-carotene."
		},
		
		# Fibre Crops
		{
			"crop_name": "Cotton",
			"crop_type": "Fibre Crops",
			"scientific_name": "Gossypium spp.",
			"description": "Primary fiber crop for textile industry."
		},
		{
			"crop_name": "Jute",
			"crop_type": "Fibre Crops",
			"scientific_name": "Corchorus capsularis",
			"description": "Natural fiber crop used for ropes and textiles."
		},
		{
			"crop_name": "Hemp",
			"crop_type": "Fibre Crops",
			"scientific_name": "Cannabis sativa",
			"description": "Versatile fiber crop with industrial applications."
		},
		{
			"crop_name": "Flax",
			"crop_type": "Fibre Crops",
			"scientific_name": "Linum usitatissimum",
			"description": "Fiber crop used for linen production."
		},
		
		# Sugar Crops
		{
			"crop_name": "Sugarcane",
			"crop_type": "Sugar Crops",
			"scientific_name": "Saccharum officinarum",
			"description": "Primary crop for sugar production worldwide."
		},
		{
			"crop_name": "Sugar Beet",
			"crop_type": "Sugar Crops",
			"scientific_name": "Beta vulgaris",
			"description": "Root crop used for sugar production in temperate regions."
		},
		
		# Beverage/Stimulant
		{
			"crop_name": "Tea",
			"crop_type": "Beverage/Stimulant",
			"scientific_name": "Camellia sinensis",
			"description": "Popular beverage crop, one of the most consumed drinks."
		},
		{
			"crop_name": "Coffee",
			"crop_type": "Beverage/Stimulant",
			"scientific_name": "Coffea arabica",
			"description": "Major beverage crop, important export commodity."
		},
		{
			"crop_name": "Cocoa",
			"crop_type": "Beverage/Stimulant",
			"scientific_name": "Theobroma cacao",
			"description": "Tropical crop used for chocolate and beverages."
		},
		{
			"crop_name": "Tobacco",
			"crop_type": "Beverage/Stimulant",
			"scientific_name": "Nicotiana tabacum",
			"description": "Stimulant crop used in various products."
		},
		
		# Spices & Condiments
		{
			"crop_name": "Ginger",
			"crop_type": "Spices & Condiments",
			"scientific_name": "Zingiber officinale",
			"description": "Aromatic spice with medicinal properties."
		},
		{
			"crop_name": "Turmeric",
			"crop_type": "Spices & Condiments",
			"scientific_name": "Curcuma longa",
			"description": "Golden spice known for its health benefits."
		},
		{
			"crop_name": "Black Pepper",
			"crop_type": "Spices & Condiments",
			"scientific_name": "Piper nigrum",
			"description": "Popular spice, often called the king of spices."
		},
		{
			"crop_name": "Chili Pepper",
			"crop_type": "Spices & Condiments",
			"scientific_name": "Capsicum annuum",
			"description": "Hot spice used for flavoring and heat."
		},
		{
			"crop_name": "Cardamom",
			"crop_type": "Spices & Condiments",
			"scientific_name": "Elettaria cardamomum",
			"description": "Aromatic spice, one of the most expensive spices."
		}
	]
	
	for crop_data in crops:
		# Check if crop already exists
		if not frappe.db.exists("Crop", crop_data["crop_name"]):
			# Verify crop type exists
			if frappe.db.exists("Crop Type", crop_data["crop_type"]):
				doc = frappe.get_doc({
					"doctype": "Crop",
					**crop_data
				})
				doc.insert(ignore_permissions=True)
				frappe.db.commit()
				print(f"Created Crop: {crop_data['crop_name']} ({crop_data['crop_type']})")
			else:
				print(f"Crop Type '{crop_data['crop_type']}' does not exist. Please seed crop types first. Skipping: {crop_data['crop_name']}")
		else:
			print(f"Crop already exists: {crop_data['crop_name']}")


if __name__ == "__main__":
	frappe.connect()
	seed_crop()


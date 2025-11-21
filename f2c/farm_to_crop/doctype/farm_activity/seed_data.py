# Seed data for Farm Activity
import frappe

def seed_farm_activity():
	"""Seed Farm Activity with default values from Activity List by Group"""
	
	activities = {
		"Field Planning": [
			"Land Preparation Planning",
			"List Land Preparation Activities",
			"Patch Row Direction Diagram",
			"Field Infrastructure Review",
			"Field Crop planning",
			"Field Block Planning",
			"Block - Variety, Spacing, POP Planning",
			"Transplanting plan / Dates for Land P"
		],
		"Land Preparation": [
			"Primary Tilling-MB Plough",
			"Primary Tilling-Disc Plough",
			"Primary Tilling-Sub Soiler",
			"Secondary Tilling-Cultivator",
			"Secondary Tilling-Duck Foot Cultivator",
			"Secondary Tilling-Rotavator",
			"Secondary Tilling-Tine Harrow",
			"Secondary Tilling-Disc Harrow",
			"Secondary Tilling-Power Harrow",
			"Secondary Tilling-Ridge & Furrow",
			"Secondary Tilling-Raised Bed",
			"Pit Digging",
			"Stone Removal",
			"Boundary Clearance"
		],
		"Plant Management": [
			"Pro-tray Preparation",
			"Pro-tray Sowing",
			"Field Sowing",
			"Planting / Transplanting",
			"Replanting / Gap Filling",
			"Staking",
			"Tying",
			"Bending",
			"Pruning",
			"Thinning"
		],
		"Floor Management": [
			"Deweeding - Row to Row",
			"Deweeding - Plant to Plant",
			"Hoeing",
			"Digging",
			"Earthing up",
			"Drain Mgmt",
			"Mulching",
			"Irrigation - Canal",
			"Irrigation - Flood",
			"Irrigation - Drip",
			"Irrigation - Sprinkler",
			"Sanitation"
		],
		"Nutrition & Plant Protection": [
			"Basal Dose",
			"Pit Dose",
			"Top Dressing",
			"BroadCasting",
			"Spraying",
			"Drip - Fertigation",
			"Drenching"
		],
		"Crop Recordkeeping": [
			"Weather Recording",
			"Daily Scouting",
			"Weekly Scouting",
			"On Demand Scouting",
			"Yield Estimate",
			"Activity Monitoring",
			"Trial Monitoring"
		],
		"Harvest Management": [
			"Crate Management",
			"Harvesting"
		],
		"Labour Management": [
			"Labour Onboarding - KYC",
			"Labour Hire",
			"Clock in",
			"Team Assignment",
			"Role Assignment",
			"Clock out",
			"Wage Calculation",
			"Wage Payment"
		],
		"Equipment & Infra": [
			"Hire Equipment",
			"Equipment - Purchase",
			"Equipment-Repair & Maintenance",
			"Equipment Audit",
			"Monthly Equipment Inspection"
		]
	}
	
	for group_type, activity_list in activities.items():
		for activity_name in activity_list:
			if not frappe.db.exists("Farm Activity", {"activity_name": activity_name}):
				doc = frappe.get_doc({
					"doctype": "Farm Activity",
					"activity_name": activity_name,
					"activity_group_type": group_type
				})
				doc.insert(ignore_permissions=True)
				frappe.db.commit()
				print(f"Created: {activity_name} ({group_type})")
			else:
				print(f"Already exists: {activity_name} ({group_type})")

if __name__ == "__main__":
	frappe.connect()
	seed_farm_activity()


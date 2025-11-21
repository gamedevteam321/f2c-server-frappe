# Seed data for Farm Activity
import frappe

def seed_farm_activity():
	"""Seed Farm Activity with hierarchical structure and sequences"""
	
	# Structure: {group_name: {activity_name: [sub_activities]}}
	activities_data = {
		"Field Planning": {
			"Land Preparation Planning": [
				"List Land Preparation Activities",
				"Patch Row Direction Diagram"
			],
			"Field Infrastructure Review": [],
			"Field Crop planning": [
				"Field Block Planning",
				"Block - Variety, Spacing, POP Planning",
				"Transplanting plan / Dates for Land P"
			]
		},
		"Land Preparation": {
			"Primary Tilling-MB Plough": [],
			"Primary Tilling-Disc Plough": [],
			"Primary Tilling-Sub Soiler": [],
			"Secondary Tilling-Cultivator": [],
			"Secondary Tilling-Duck Foot Cultivator": [],
			"Secondary Tilling-Rotavator": [],
			"Secondary Tilling-Tine Harrow": [],
			"Secondary Tilling-Disc Harrow": [],
			"Secondary Tilling-Power Harrow": [],
			"Secondary Tilling-Ridge & Furrow": [],
			"Secondary Tilling-Raised Bed": [],
			"Pit Digging": [],
			"Stone Removal": [],
			"Boundary Clearance": []
		},
		"Plant Management": {
			"Pro-tray Preparation": [],
			"Pro-tray Sowing": [],
			"Field Sowing": [],
			"Planting / Transplanting": [],
			"Replanting / Gap Filling": [],
			"Staking": [],
			"Tying": [],
			"Bending": [],
			"Pruning": [],
			"Thinning": []
		},
		"Floor Management": {
			"Deweeding - Row to Row": [],
			"Deweeding - Plant to Plant": [],
			"Hoeing": [],
			"Digging": [],
			"Earthing up": [],
			"Drain Mgmt": [],
			"Mulching": [],
			"Irrigation - Canal": [],
			"Irrigation - Flood": [],
			"Irrigation - Drip": [],
			"Irrigation - Sprinkler": [],
			"Sanitation": []
		},
		"Nutrition & Plant Protection": {
			"Basal Dose": [],
			"Pit Dose": [],
			"Top Dressing": [],
			"BroadCasting": [],
			"Spraying": [],
			"Drip - Fertigation": [],
			"Drenching": []
		},
		"Crop Recordkeeping": {
			"Weather Recording": [],
			"Daily Scouting": [],
			"Weekly Scouting": [],
			"On Demand Scouting": [],
			"Yield Estimate": [],
			"Activity Monitoring": [],
			"Trial Monitoring": []
		},
		"Harvest Management": {
			"Crate Management": [],
			"Harvesting": []
		},
		"Labour Management": {
			"Labour Onboarding - KYC": [],
			"Labour Hire": [
				"Clock in",
				"Team Assignment",
				"Role Assignment"
			],
			"Clock out": [
				"Wage Calculation"
			],
			"Wage Payment": []
		},
		"Equipment & Infra": {
			"Hire Equipment": [],
			"Equipment - Purchase": [],
			"Equipment-Repair & Maintenance": [],
			"Equipment Audit": [],
			"Monthly Equipment Inspection": []
		}
	}
	
	# Create main activities (sequence 1) and sub-activities (sequence 2)
	for group_name, activities in activities_data.items():
		seq_counter = 1
		
		for activity_name, sub_activities in activities.items():
			# Create main activity (sequence 1, no parent)
			if not frappe.db.exists("Farm Activity", {"activity_name": activity_name}):
				doc = frappe.get_doc({
					"doctype": "Farm Activity",
					"activity_name": activity_name,
					"activity_group_type": group_name,
					"sequence": 1,
					"parent_activity": None
				})
				doc.insert(ignore_permissions=True)
				frappe.db.commit()
				activity_id = doc.name
				print(f"Created main activity: {activity_name} (Sequence: 1, Group: {group_name})")
			else:
				activity_id = frappe.db.get_value("Farm Activity", {"activity_name": activity_name}, "name")
				# Update sequence and parent if not set correctly
				frappe.db.set_value("Farm Activity", activity_id, {
					"parent_activity": None,
					"sequence": 1,
					"activity_group_type": group_name
				})
				print(f"Main activity already exists: {activity_name}, updated")
			
			# Create sub-activities (sequence 2, parent = main activity)
			sub_seq = 1
			for sub_activity_name in sub_activities:
				if not frappe.db.exists("Farm Activity", {"activity_name": sub_activity_name}):
					doc = frappe.get_doc({
						"doctype": "Farm Activity",
						"activity_name": sub_activity_name,
						"activity_group_type": group_name,
						"sequence": 2,
						"parent_activity": activity_id
					})
					doc.insert(ignore_permissions=True)
					frappe.db.commit()
					print(f"Created sub-activity: {sub_activity_name} (Sequence: 2, Parent: {activity_name})")
				else:
					sub_activity_id = frappe.db.get_value("Farm Activity", {"activity_name": sub_activity_name}, "name")
					# Update parent and sequence if not set
					frappe.db.set_value("Farm Activity", sub_activity_id, {
						"parent_activity": activity_id,
						"sequence": 2,
						"activity_group_type": group_name
					})
					print(f"Sub-activity already exists: {sub_activity_name}, updated parent")
				sub_seq += 1
			
			seq_counter += 1
	
	print("\nSeed data creation completed!")

if __name__ == "__main__":
	frappe.connect()
	seed_farm_activity()

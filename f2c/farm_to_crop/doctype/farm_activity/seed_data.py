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
			"Primary Tilling": [],
			"Secondary Tilling": [],
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
			"Deweeding": [],
			"Hoeing": [],
			"Digging": [],
			"Earthing up": [],
			"Drain Mgmt": [],
			"Mulching": [],
			"Irrigation": [],
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
			# Create or update main activity (sequence 1, no parent)
			existing_activity = frappe.db.get_value("Farm Activity", {"activity_name": activity_name}, "name")
			
			if existing_activity:
				# Update existing document
				doc = frappe.get_doc("Farm Activity", existing_activity)
				doc.activity_group_type = group_name
				doc.sequence = 1
				doc.parent_activity = None
				doc.save(ignore_permissions=True)
				frappe.db.commit()
				activity_id = doc.name
				print(f"Updated main activity: {activity_name} (Sequence: 1, Group: {group_name})")
			else:
				# Create new document
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
			
			# Create or update sub-activities (sequence 2, parent = main activity)
			sub_seq = 1
			for sub_activity_name in sub_activities:
				existing_sub_activity = frappe.db.get_value("Farm Activity", {"activity_name": sub_activity_name}, "name")
				
				if existing_sub_activity:
					# Update existing document
					sub_doc = frappe.get_doc("Farm Activity", existing_sub_activity)
					sub_doc.activity_group_type = group_name
					sub_doc.sequence = 2
					sub_doc.parent_activity = activity_id
					sub_doc.save(ignore_permissions=True)
					frappe.db.commit()
					print(f"Updated sub-activity: {sub_activity_name} (Sequence: 2, Parent: {activity_name})")
				else:
					# Create new document
					sub_doc = frappe.get_doc({
						"doctype": "Farm Activity",
						"activity_name": sub_activity_name,
						"activity_group_type": group_name,
						"sequence": 2,
						"parent_activity": activity_id
					})
					sub_doc.insert(ignore_permissions=True)
					frappe.db.commit()
					print(f"Created sub-activity: {sub_activity_name} (Sequence: 2, Parent: {activity_name})")
				sub_seq += 1
			
			seq_counter += 1
	
	print("\nSeed data creation completed!")

if __name__ == "__main__":
	frappe.connect()
	seed_farm_activity()

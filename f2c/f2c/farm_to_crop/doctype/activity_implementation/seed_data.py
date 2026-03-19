# Seed data for Activity Implementation
import frappe

def seed_activity_implementation():
	"""Seed Activity Implementation with activity and implementation mappings"""
	
	# Structure: {activity_name: [implementation_names]}
	implementation_data = {
		"Primary Tilling": [
			"MB Plough",
			"Disc Plough",
			"Sub Soiler"
		],
		"Secondary Tilling": [
			"Cultivator",
			"Duck Foot Cultivator",
			"Rotavator",
			"Tine Harrow",
			"Disc Harrow",
			"Power Harrow",
			"Ridge & Furrow",
			"Raised Bed"
		],
		"Deweeding": [
			"Row to Row",
			"Plant to Plant"
		],
		"Irrigation": [
			"Canal",
			"Flood",
			"Drip",
			"Sprinkler"
		]
	}
	
	# Create Activity Implementation records
	for activity_name, implementations in implementation_data.items():
		# Get the Farm Activity ID
		activity_id = frappe.db.get_value("Farm Activity", {"activity_name": activity_name}, "name")
		
		if not activity_id:
			print(f"Warning: Activity '{activity_name}' not found. Skipping implementations.")
			continue
		
		for implementation_name in implementations:
			# Check if implementation already exists using SQL
			existing = frappe.db.sql("""
				SELECT name 
				FROM `tabActivity Implementation`
				WHERE `activity_name` = %s 
				AND `implementation_name` = %s
			""", (activity_id, implementation_name), as_dict=True)
			
			if existing:
				print(f"Implementation already exists: {implementation_name} (ID: {existing[0].name}) for activity: {activity_name}")
			else:
				# Create Activity Implementation
				# The document name (ID) will be auto-generated as IM-001, IM-002, etc.
				doc = frappe.get_doc({
					"doctype": "Activity Implementation",
					"implementation_name": implementation_name,  # Implementation name field
					"activity_name": activity_id  # Link to Farm Activity
				})
				doc.insert(ignore_permissions=True)
				frappe.db.commit()
				print(f"Created implementation: {implementation_name} (ID: {doc.name}) for activity: {activity_name}")
	
	print("\nActivity Implementation seed data creation completed!")

if __name__ == "__main__":
	frappe.connect()
	seed_activity_implementation()


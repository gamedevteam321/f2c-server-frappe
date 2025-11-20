# Seed data for Land Topography
import frappe

def seed_land_topography():
	"""Seed Land Topography with default values"""
	topography_list = [
		"Plain",
		"Plateau",
		"Valleys",
		"Mountainous & Hills"
	]
	
	for topography_name in topography_list:
		if not frappe.db.exists("Land Topography", {"topography_name": topography_name}):
			doc = frappe.get_doc({
				"doctype": "Land Topography",
				"topography_name": topography_name
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print(f"Created: {topography_name}")
		else:
			print(f"Already exists: {topography_name}")

if __name__ == "__main__":
	frappe.connect()
	seed_land_topography()


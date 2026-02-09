import frappe


@frappe.whitelist()
def get_google_maps_api_key():
	"""
	Return Google Maps JS API key from site_config / frappe.conf.

	Frontend (SPA) needs this key to load Maps + Places Autocomplete.
	"""
	return frappe.conf.get("google_maps_api_key") or ""


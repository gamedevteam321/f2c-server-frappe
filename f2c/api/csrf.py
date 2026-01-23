import frappe
from frappe import _


@frappe.whitelist()
def get_csrf_token():
	"""
	Return a CSRF token for the current session.

	Frappe's built-in get_csrf_token helper isn't RPC-whitelisted, so this wrapper
	lets our React app fetch it after login and set window.csrf_token.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)
	return {"csrf_token": frappe.sessions.get_csrf_token()}



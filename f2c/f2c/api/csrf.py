import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def get_csrf_token():
	"""
	Return a CSRF token for the current session.

	Frappe's built-in get_csrf_token helper isn't RPC-whitelisted, so this wrapper
	lets our React app fetch it after login and set window.csrf_token.
	
	For guest users, returns null to avoid 401 errors. The frontend will handle
	this gracefully and fetch the token again after login.
	"""
	try:
		if frappe.session.user == "Guest":
			return {"csrf_token": None}
		return {"csrf_token": frappe.sessions.get_csrf_token()}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c.api.csrf.get_csrf_token")
		# Avoid 500 on the React bootstrap path; caller can retry after login/session fix.
		return {"csrf_token": None}



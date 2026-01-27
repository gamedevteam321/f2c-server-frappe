import frappe
from frappe import _
from frappe.utils.password import check_password


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login_and_get_token(usr: str, pwd: str):
	"""
	Token-auth login for public clients (SPA/mobile).

	Validates username/password and returns a Frappe API token in the format:
	  <api_key>:<api_secret>

	This mirrors the "API Keys" feature in Frappe, but is meant to be called by the user
	themselves (after password verification). This avoids session cookies + CSRF.

	Security note:
	- Returning an API secret to a browser client is inherently sensitive. Treat the token
	  like a password and store it securely (prefer short-lived tokens in ideal setups).
	"""
	usr = (usr or "").strip()
	pwd = pwd or ""
	if not usr or not pwd:
		frappe.throw(_("Incomplete login details"), frappe.AuthenticationError)

	# Validate credentials without creating a session cookie.
	user = check_password(usr, pwd)
	if user in ("Guest",):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	user_doc = frappe.get_doc("User", user)
	if not getattr(user_doc, "enabled", 1):
		frappe.throw(_("User is disabled"), frappe.AuthenticationError)

	# Rotate secret each login; keep api_key stable once created.
	api_secret = frappe.generate_hash(length=15)
	if not user_doc.api_key:
		user_doc.api_key = frappe.generate_hash(length=15)
	user_doc.api_secret = api_secret
	user_doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"token": f"{user_doc.api_key}:{api_secret}",
		"user": user,
		"api_key": user_doc.api_key,
		"api_secret": api_secret,
	}


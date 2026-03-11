import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.utils.password import get_encryption_key


def _get_proxy_secret() -> str:
	return frappe.local.conf.get("secret") or get_encryption_key()


def _get_signature(file_url: str, expires: int) -> str:
	message = f"{file_url}\n{expires}".encode("utf-8")
	secret = _get_proxy_secret().encode("utf-8")
	return hmac.new(secret, message, digestmod=hashlib.sha256).hexdigest()


def _validate_file_access(file_url: str):
	file = find_file_by_url(file_url)
	if not file:
		frappe.throw(_("File not found or not permitted"), frappe.PermissionError)
	return file


@frappe.whitelist(methods=["GET"])
def get_signed_file_proxy_url(file_url: str, expires_in_seconds: int = 300):
	"""Return a short-lived same-origin proxy URL for inline file viewing."""
	file_url = (file_url or "").strip()
	if not file_url:
		frappe.throw(_("File URL is required"))

	_validate_file_access(file_url)

	try:
		ttl = max(30, min(int(expires_in_seconds or 300), 900))
	except Exception:
		ttl = 300

	expires = int(time.time()) + ttl
	signature = _get_signature(file_url, expires)
	query = urlencode(
		{
			"file_url": file_url,
			"expires": expires,
			"signature": signature,
		}
	)

	return {
		"proxy_url": f"/api/method/f2c.inventory.file_proxy_api.proxy_file?{query}",
		"expires": expires,
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def proxy_file(file_url: str, expires: str, signature: str):
	"""Stream a previously signed file URL inline for browser previews."""
	file_url = (file_url or "").strip()
	signature = (signature or "").strip()

	if not file_url or not expires or not signature:
		frappe.throw(_("Missing file proxy parameters"), frappe.PermissionError)

	try:
		expires_int = int(expires)
	except Exception:
		frappe.throw(_("Invalid file proxy expiry"), frappe.PermissionError)

	if expires_int < int(time.time()):
		frappe.throw(_("File proxy link has expired"), frappe.PermissionError)

	expected = _get_signature(file_url, expires_int)
	if not hmac.compare_digest(expected, signature):
		frappe.throw(_("Invalid file proxy signature"), frappe.PermissionError)

	file = _validate_file_access(file_url)
	filename = os.path.basename(file.file_name or file_url)

	frappe.local.response.filename = filename
	frappe.local.response.filecontent = file.get_content()
	frappe.local.response.type = "download"
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.content_type = file.content_type

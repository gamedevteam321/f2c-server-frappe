# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt
"""
Visiting card / business card extraction via Gemini.
This module does not import numpy or heavy OCR deps, so it works on arm64 even if ocr.py has arch issues.
"""

import base64
import frappe
from io import BytesIO
from typing import Dict

try:
	from PIL import Image
except ImportError:
	Image = None


@frappe.whitelist(allow_guest=False)
def extract_visiting_card_details(image: str) -> Dict:
	"""
	Extract company and contact details from a visiting card image using Gemini.

	Args:
		image: Base64 encoded visiting card image (optional data URL prefix)

	Returns:
		Dictionary with success, data (company_name, address_line_1, address_line_2, city, state,
		country, pincode, phone_number, email, contact_person_name, contact_person_designation), error.
	"""
	if Image is None:
		frappe.throw("PIL (Pillow) is not installed. Please install it using: pip install Pillow")

	try:
		img_data = base64.b64decode(image.split(",")[-1] if "," in image else image)
		img = Image.open(BytesIO(img_data))
		if img.mode != "RGB":
			img = img.convert("RGB")

		from f2c.farm_worker.api.gemini_extract import extract_visiting_card_details as gemini_extract

		result = gemini_extract(img)
		return result
	except Exception as e:
		import traceback
		frappe.log_error(
			f"Visiting card extraction error: {str(e)}\n{traceback.format_exc()}",
			"Visiting Card Gemini Extraction Failed",
		)
		return {
			"success": False,
			"error": str(e),
			"data": {
				"company_name": "",
				"address_line_1": "",
				"address_line_2": "",
				"city": "",
				"state": "",
				"country": "",
				"pincode": "",
				"phone_number": "",
				"email": "",
				"contact_person_name": "",
				"contact_person_designation": "",
			},
		}

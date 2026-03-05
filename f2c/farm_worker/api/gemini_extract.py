# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import base64
import json
from io import BytesIO
from PIL import Image
from typing import Dict, Optional


def extract_aadhaar_details(front_img: Image.Image, back_img: Image.Image) -> Dict:
	"""
	Extract Aadhaar card details using Gemini 2.5 Flash Lite API.
	
	Args:
		front_img: PIL Image object of front side
		back_img: PIL Image object of back side
		
	Returns:
		Dictionary with extracted fields: name, dob, gender, aadhaar_number, address
	"""
	try:
		import google.generativeai as genai
	except ImportError:
		frappe.throw("google-generativeai is not installed. Please install it using: pip install google-generativeai")
	
	# Get API key from Frappe Site Config
	api_key = frappe.conf.get("gemini_api_key")
	if not api_key:
		error_msg = "Gemini API key not configured. Please set 'gemini_api_key' in Site Config."
		frappe.throw(error_msg)
	
	# Configure Gemini API
	genai.configure(api_key=api_key)
	
	# Create prompt for Gemini with emphasis on gender extraction
	prompt = """Extract information from Indian Aadhaar card images (front and back).

FRONT SIDE contains:
- Name (usually at top)
- Date of Birth (format as YYYY-MM-DD)
- Gender (CRITICAL - look for "Male"/"Female"/"M"/"F" text, usually near DOB)
- Aadhaar Number (12 digits)

BACK SIDE contains:
- Full Address (all lines)
- Sometimes Aadhaar Number again

Return ONLY valid JSON with this exact structure:
{
  "name": "extracted name",
  "dob": "YYYY-MM-DD",
  "gender": "Male" or "Female" (EXACTLY one of these, case-sensitive),
  "aadhaar_number": "12 digits without spaces",
  "address": "complete address"
}
"""
	
	# Convert PIL Images to bytes for Gemini
	def image_to_bytes(img: Image.Image) -> bytes:
		"""Convert PIL Image to bytes."""
		buffer = BytesIO()
		img.save(buffer, format='PNG')
		return buffer.getvalue()
	
	front_img_bytes = image_to_bytes(front_img)
	back_img_bytes = image_to_bytes(back_img)
	
	# List of models to try (primary and fallbacks)
	models_to_try = [
		('gemini-2.5-flash-lite', 'gemini-2.5-flash')
	]
	
	last_error = None
	
	for model_name, method_name in models_to_try:
		try:
			# Initialize model
			current_model = genai.GenerativeModel(model_name)
			
			# Call Gemini API with both images
			response = current_model.generate_content([
				prompt,
				{"mime_type": "image/png", "data": front_img_bytes},
				{"mime_type": "image/png", "data": back_img_bytes}
			])
			
			# Extract JSON from response
			response_text = response.text.strip()
			
			# Try to extract JSON from response (handle markdown code blocks)
			if "```json" in response_text:
				response_text = response_text.split("```json")[1].split("```")[0].strip()
			elif "```" in response_text:
				response_text = response_text.split("```")[1].split("```")[0].strip()
			
			# Parse JSON
			try:
				extracted_data = json.loads(response_text)
			except json.JSONDecodeError:
				# If JSON parsing fails, try to extract fields manually
				frappe.log_error(f"Failed to parse Gemini response as JSON: {response_text}", "Gemini Extraction Error")
				extracted_data = {
					"name": "",
					"dob": "",
					"gender": "",
					"aadhaar_number": "",
					"address": ""
				}
			
			# Return parsed data directly without any processing
			final_result = {
				"success": True,
				"_api_version": f"4.0 ({model_name})",
				"data": extracted_data,
				"raw_text": {
					"front": "",
					"back": ""
				},
				"debug": {
					"method": method_name,
					"model_used": model_name,
					"response_preview": response_text[:200] if response_text else ""
				}
			}
			
			return final_result
			
		except Exception as e:
			error_str = str(e)
			last_error = e
			
			# Check if it's a rate limit error
			is_rate_limit = (
				"429" in error_str or
				"rate limit" in error_str.lower() or
				"quota" in error_str.lower() or
				"resource_exhausted" in error_str.lower()
			)
			
			if is_rate_limit and model_name != models_to_try[-1][0]:
				# Rate limit hit, try next model
				frappe.log_error(
					f"Rate limit hit for {model_name}, falling back to next model. Error: {error_str}",
					"Gemini Rate Limit Fallback"
				)
				continue  # Try next model
			else:
				# Not a rate limit error, or it's the last model - log and break
				if not is_rate_limit:
					# For non-rate-limit errors on primary model, try fallback once
					if model_name == models_to_try[0][0]:
						frappe.log_error(
							f"Error with {model_name}, trying fallback. Error: {error_str}",
							"Gemini Model Fallback"
						)
						continue
					else:
						# Error on fallback model, return error
						break
				else:
					# Rate limit on last model too
					break
	
	# All models failed
	error_msg = f"All Gemini models failed. Last error: {str(last_error)}"
	frappe.log_error(error_msg, "Gemini Extraction Error")
	
	final_error_result = {
		"success": False,
		"_api_version": "4.0 (All Models Failed)",
		"data": {
			"name": "",
			"dob": "",
			"gender": "",
			"aadhaar_number": "",
			"address": ""
		},
		"error": error_msg,
		"raw_text": {
			"front": "",
			"back": ""
		},
		"debug": {
			"method": "all_models_failed",
			"error": str(last_error) if last_error else "Unknown error"
		}
	}
	return final_error_result


def _normalize_dob(dob_str: str) -> str:
	"""Normalize date of birth to YYYY-MM-DD format."""
	if not dob_str:
		return ""
	
	import re
	from datetime import datetime
	
	# Try to parse various date formats
	date_formats = [
		"%Y-%m-%d",
		"%d-%m-%Y",
		"%d/%m/%Y",
		"%Y/%m/%d",
		"%d-%m-%y",
		"%d/%m/%y"
	]
	
	for fmt in date_formats:
		try:
			dt = datetime.strptime(dob_str, fmt)
			return dt.strftime("%Y-%m-%d")
		except ValueError:
			continue
	
	# If all formats fail, try regex extraction
	match = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', dob_str)
	if match:
		day, month, year = match.groups()
		try:
			dt = datetime(int(year), int(month), int(day))
			return dt.strftime("%Y-%m-%d")
		except ValueError:
			pass
	
	# Return as-is if cannot parse
	return dob_str


def _normalize_gender(gender_str: str) -> str:
	"""
	Normalize gender to Male or Female format.
	Returns exactly "Male" or "Female" to match frontend dropdown options.
	"""
	if not gender_str:
		return ""
	
	gender_lower = gender_str.lower().strip()
	
	# Remove common prefixes/suffixes
	gender_clean = gender_lower.replace("gender:", "").replace("sex:", "").strip()
	
	# IMPORTANT: Check for "female" FIRST before "male" to avoid substring matching issue
	# "female" contains "male" as a substring, so we must check female first
	
	# Check for Female variations (check most specific first)
	# Check exact match first
	if gender_clean == "female" or gender_clean == "f":
		return "Female"
	
	# Check if it starts with "female"
	if gender_clean.startswith("female"):
		return "Female"
	
	# Check for Female indicators
	female_indicators = ["f/", "/f", "f.", "f:"]
	if any(gender_clean.startswith(x) or gender_clean.endswith(x) or gender_clean == x.replace("/", "").replace(".", "").replace(":", "") for x in female_indicators):
		return "Female"
	
	# Check for single character F
	if gender_clean == "f":
		return "Female"
	
	# Now check for Male variations (after checking female)
	# Check exact match first
	if gender_clean == "male" or gender_clean == "m":
		return "Male"
	
	# Check if it starts with "male" (but not "female")
	if gender_clean.startswith("male") and not gender_clean.startswith("female"):
		return "Male"
	
	# Check for Male indicators
	male_indicators = ["m/", "/m", "m.", "m:"]
	if any(gender_clean.startswith(x) or gender_clean.endswith(x) or gender_clean == x.replace("/", "").replace(".", "").replace(":", "") for x in male_indicators):
		return "Male"
	
	# Check for single character M
	if gender_clean == "m":
		return "Male"
	
	# Additional safety check: if contains "female" anywhere, return Female
	if "female" in gender_clean:
		return "Female"
	
	# Additional check: if contains "male" but NOT "female", return Male
	if "male" in gender_clean and "female" not in gender_clean:
		return "Male"
	
	# Return empty string if cannot determine
	return ""


def extract_visiting_card_details(img: Image.Image) -> Dict:
	"""
	Extract supplier/contact details from a visiting card (business card) image using Gemini.

	Args:
		img: PIL Image object of the visiting card (single image).

	Returns:
		Dictionary with success, data (company_name, address_line_1, address_line_2, city, state,
		country, pincode, phone_number, email, contact_person_name, contact_person_designation), error.
	"""
	try:
		import google.generativeai as genai
	except ImportError:
		frappe.throw("google-generativeai is not installed. Please install it using: pip install google-generativeai")

	api_key = frappe.conf.get("gemini_api_key")
	if not api_key:
		error_msg = "Gemini API key not configured. Please set 'gemini_api_key' in Site Config."
		frappe.throw(error_msg)

	genai.configure(api_key=api_key)

	prompt = """Extract information from this visiting card / business card image.

Return ONLY valid JSON with this exact structure (use empty string "" for any field not found):
{
  "company_name": "company or organization name",
  "address_line_1": "first line of address (street, building)",
  "address_line_2": "second line of address (suite, unit, etc.)",
  "city": "city",
  "state": "state or region",
  "country": "country",
  "pincode": "pincode / zip / postal code",
  "phone_number": "phone number(s) - if multiple, put each on a new line (one per line)",
  "email": "email address(es) - if multiple, put each on a new line (one per line)",
  "contact_person_name": "name of contact person",
  "contact_person_designation": "designation or title (e.g. Sales Manager)"
}
"""

	def image_to_bytes(image: Image.Image) -> bytes:
		buffer = BytesIO()
		image.save(buffer, format="PNG")
		return buffer.getvalue()

	img_bytes = image_to_bytes(img)

	models_to_try = [
		("gemini-2.5-flash-lite", "gemini-2.5-flash"),
	]
	last_error = None
	default_data = {
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
	}

	for model_name, method_name in models_to_try:
		try:
			current_model = genai.GenerativeModel(model_name)
			response = current_model.generate_content([
				prompt,
				{"mime_type": "image/png", "data": img_bytes},
			])
			response_text = response.text.strip()
			if "```json" in response_text:
				response_text = response_text.split("```json")[1].split("```")[0].strip()
			elif "```" in response_text:
				response_text = response_text.split("```")[1].split("```")[0].strip()

			try:
				extracted_data = json.loads(response_text)
			except json.JSONDecodeError:
				frappe.log_error(f"Failed to parse Gemini visiting card response: {response_text}", "Gemini Visiting Card Extraction")
				extracted_data = default_data.copy()

			# Ensure all keys exist
			for key in default_data:
				if key not in extracted_data:
					extracted_data[key] = default_data.get(key, "")
				elif not isinstance(extracted_data[key], str):
					extracted_data[key] = str(extracted_data[key]) if extracted_data[key] is not None else ""

			return {
				"success": True,
				"_api_version": f"4.0 ({model_name})",
				"data": extracted_data,
				"error": None,
				"debug": {"model_used": model_name},
			}
		except Exception as e:
			error_str = str(e)
			last_error = e
			is_rate_limit = (
				"429" in error_str
				or "rate limit" in error_str.lower()
				or "quota" in error_str.lower()
				or "resource_exhausted" in error_str.lower()
			)
			if is_rate_limit and model_name != models_to_try[-1][0]:
				continue
			if not is_rate_limit and model_name == models_to_try[0][0]:
				continue
			break

	error_msg = f"All Gemini models failed. Last error: {str(last_error)}"
	frappe.log_error(error_msg, "Gemini Visiting Card Extraction")
	return {
		"success": False,
		"_api_version": "4.0 (All Models Failed)",
		"data": default_data,
		"error": error_msg,
		"debug": {"error": str(last_error) if last_error else "Unknown error"},
	}


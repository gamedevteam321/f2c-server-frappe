# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import base64
import re
import numpy as np
from io import BytesIO
from PIL import Image
from typing import Dict, Optional

# Global OCR instance to avoid reinitializing on every request
_ocr_instance = None


def get_ocr_instance():
	"""Get or create PaddleOCR instance (singleton pattern for performance)."""
	# Global OCR instance - DISABLED for debugging (potential threading/state issue)
	# global _ocr_instance
	# if _ocr_instance is None:
	
	try:
		from paddleocr import PaddleOCR
	except ImportError:
		frappe.throw("PaddleOCR is not installed. Please install it using: pip install paddlepaddle paddleocr")
	
	# Suppress PaddleOCR logs
	import logging
	logging.getLogger('ppocr').setLevel(logging.ERROR)
	
	# Initialize NEW PaddleOCR instance every time
	print("Initializing NEW PaddleOCR instance (Singleton disabled)...")
	ocr_instance = PaddleOCR(
		use_angle_cls=True,  # Enable angle classification for better accuracy
		lang='en'            # English language
	)
	print("PaddleOCR instance initialized successfully")
	
	return ocr_instance


@frappe.whitelist(allow_guest=False)
def extract_aadhaar_details(front_image: str, back_image: str) -> Dict:
	"""
	Extract details from Aadhaar card images using PaddleOCR.
	
	Args:
		front_image: Base64 encoded front Aadhaar card image
		back_image: Base64 encoded back Aadhaar card image
		
	Returns:
		Dictionary with extracted fields: name, dob, address, aadhaar_number
	"""
	try:
		# Log incoming data
		print(f"\n{'='*60}")
		print(f"OCR API CALLED - extract_aadhaar_details")
		print(f"Front image data length: {len(front_image) if front_image else 0}")
		print(f"Back image data length: {len(back_image) if back_image else 0}")
		print(f"Front image starts with: {front_image[:50] if front_image else 'None'}...")
		print(f"Back image starts with: {back_image[:50] if back_image else 'None'}...")
		
		# Get OCR instance (reused across requests for performance)
		# First call will initialize (slow), subsequent calls will be fast
		print(f"Getting OCR instance...")
		ocr = get_ocr_instance()
		print(f"OCR instance obtained: {type(ocr)}")
		
		# Decode base64 images
		print(f"Decoding base64 images...")
		front_img_data = base64.b64decode(front_image.split(',')[-1] if ',' in front_image else front_image)
		back_img_data = base64.b64decode(back_image.split(',')[-1] if ',' in back_image else back_image)
		print(f"Front image decoded: {len(front_img_data)} bytes")
		print(f"Back image decoded: {len(back_img_data)} bytes")
		
		# Convert to PIL Image and then to numpy array (PaddleOCR expects numpy array)
		print(f"Opening images with PIL...")
		front_img = Image.open(BytesIO(front_img_data))
		back_img = Image.open(BytesIO(back_img_data))
		print(f"Front image: size={front_img.size}, mode={front_img.mode}, format={front_img.format}")
		print(f"Back image: size={back_img.size}, mode={back_img.mode}, format={back_img.format}")
		
		# Preprocess images for better OCR accuracy
		# Convert to RGB if needed (some images might be RGBA)
		if front_img.mode != 'RGB':
			print(f"Converting front image from {front_img.mode} to RGB...")
			front_img = front_img.convert('RGB')
		if back_img.mode != 'RGB':
			print(f"Converting back image from {back_img.mode} to RGB...")
			back_img = back_img.convert('RGB')
		
		# Enhance images for better OCR - resize if too small, increase contrast
		# PaddleOCR works better with larger images (at least 32px height for text)
		def preprocess_image(img):
			"""Preprocess image for better OCR accuracy."""
			# Get current size
			width, height = img.size
			print(f"  Original size: {width}x{height}")
			
			# Resize if image is too small (minimum 300px width for better OCR)
			if width < 300:
				scale_factor = 300 / width
				new_width = 300
				new_height = int(height * scale_factor)
				img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
				print(f"  Resized to: {new_width}x{new_height} (scale: {scale_factor:.2f})")
			
			# Enhance contrast using ImageOps
			try:
				from PIL import ImageEnhance, ImageOps
				# Convert to grayscale for better contrast enhancement
				img_gray = img.convert('L')
				# Apply auto contrast
				img_gray = ImageOps.autocontrast(img_gray, cutoff=2)
				# Convert back to RGB
				img = img_gray.convert('RGB')
				print(f"  Applied contrast enhancement")
			except Exception as e:
				print(f"  Could not enhance contrast: {e}")
			
			return img
		
		print(f"Preprocessing front image...")
		front_img = preprocess_image(front_img)
		print(f"Preprocessing back image...")
		back_img = preprocess_image(back_img)
		
		# Convert PIL Images to numpy arrays
		print(f"Converting PIL images to numpy arrays...")
		front_img_array = np.array(front_img)
		back_img_array = np.array(back_img)
		
		# Log image info for debugging
		print(f"Front image array: shape={front_img_array.shape}, dtype={front_img_array.dtype}, min={front_img_array.min()}, max={front_img_array.max()}")
		print(f"Back image array: shape={back_img_array.shape}, dtype={back_img_array.dtype}, min={back_img_array.min()}, max={back_img_array.max()}")
		
		# Save images temporarily for debugging (optional - can be removed later)
		# This helps verify the images are being processed correctly
		try:
			import os
			debug_dir = "/tmp/ocr_debug"
			os.makedirs(debug_dir, exist_ok=True)
			front_img.save(f"{debug_dir}/front_debug_{frappe.utils.now().replace(' ', '_').replace(':', '-')}.png")
			back_img.save(f"{debug_dir}/back_debug_{frappe.utils.now().replace(' ', '_').replace(':', '-')}.png")
			print(f"Debug images saved to {debug_dir}/")
		except Exception as e:
			print(f"Could not save debug images: {e}")
		
		frappe.log_error(
			f"OCR Images - Front: shape={front_img_array.shape}, dtype={front_img_array.dtype}, "
			f"Back: shape={back_img_array.shape}, dtype={back_img_array.dtype}",
			"OCR Debug"
		)
		
		# Perform OCR using external script for process isolation
		try:
			import subprocess
			import json
			import os
			import sys
			
			# Save images to temp files (if not already done)
			# Re-using the logic from debug save but making it mandatory
			temp_dir = "/tmp/ocr_process"
			os.makedirs(temp_dir, exist_ok=True)
			timestamp = frappe.utils.now().replace(' ', '_').replace(':', '-')
			front_path = f"{temp_dir}/front_{timestamp}.png"
			back_path = f"{temp_dir}/back_{timestamp}.png"
			
			front_img.save(front_path)
			back_img.save(back_path)
			
			runner_path = os.path.join(os.path.dirname(__file__), "ocr_runner.py")
			python_path = sys.executable 
			
			cmd = [python_path, runner_path, front_path, back_path]
			print(f"Running OCR subprocess: {' '.join(cmd)}")
			
			# Run subprocess with timeout
			result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
			
			# Debug: Log subprocess output
			print(f"Subprocess returncode: {result.returncode}")
			print(f"Subprocess stdout length: {len(result.stdout)}")
			print(f"Subprocess stderr length: {len(result.stderr)}")
			if result.stderr:
				print(f"Subprocess stderr: {result.stderr[:500]}")
			if result.stdout:
				print(f"Subprocess stdout preview: {result.stdout[:500]}")
			
			if result.returncode != 0:
				error_msg = f"OCR Subprocess failed with return code {result.returncode}. stderr: {result.stderr}, stdout: {result.stdout}"
				print(error_msg)
				frappe.log_error(error_msg, "OCR Subprocess Error")
				return {"success": False, "error": f"OCR Service Error: {result.stderr[:200] if result.stderr else 'Unknown error'}"}
			
			# Parse JSON output
			# The script outputs JSON to stdout, might have logs before it
			stdout_text = result.stdout.strip()
			if not stdout_text:
				raise ValueError(f"Subprocess returned empty output. stderr: {result.stderr[:200] if result.stderr else 'None'}")
			
			# Try to find JSON in the output
			ocr_data = None
			# First, try parsing the last line (most common case)
			lines = stdout_text.split('\n')
			for line in reversed(lines):
				line = line.strip()
				if not line:
					continue
				try:
					ocr_data = json.loads(line)
					break
				except json.JSONDecodeError:
					continue
			
			# If that didn't work, try finding JSON anywhere in the output
			if ocr_data is None:
				import re
				json_match = re.search(r'\{[^{}]*"success"[^{}]*\}', stdout_text, re.DOTALL)
				if json_match:
					try:
						ocr_data = json.loads(json_match.group(0))
					except json.JSONDecodeError:
						pass
			
			# If still no JSON, try the whole output
			if ocr_data is None:
				try:
					ocr_data = json.loads(stdout_text)
				except json.JSONDecodeError:
					raise ValueError(f"Invalid JSON output. stdout: {stdout_text[:500]}, stderr: {result.stderr[:200] if result.stderr else 'None'}")

			# Log debug info if available
			if ocr_data.get("front_debug"):
				debug_msg = ocr_data.get('front_debug')
				print(f"Front image debug info: {debug_msg}")
				# Use message parameter for long debug messages
				frappe.log_error(message=f"Front OCR Debug: {debug_msg}", title="OCR Debug")
			if ocr_data.get("back_debug"):
				debug_msg = ocr_data.get('back_debug')
				print(f"Back image debug info: {debug_msg}")
				# Use message parameter for long debug messages
				frappe.log_error(message=f"Back OCR Debug: {debug_msg}", title="OCR Debug")
			
			if not ocr_data.get("success"):
				error_msg = ocr_data.get("error", "Unknown error in OCR script")
				print(f"OCR script returned success=False: {error_msg}")
				# Truncate title if too long, put full message in message parameter
				title = error_msg[:100] if len(error_msg) <= 100 else error_msg[:97] + "..."
				frappe.log_error(message=error_msg, title=title)
				raise Exception(error_msg)
				
			front_text = "\n".join(ocr_data.get("front_text", []))
			back_text = "\n".join(ocr_data.get("back_text", []))
			
			# Log if no text was extracted
			if not front_text and not back_text:
				print(f"WARNING: No text extracted from either image")
				print(f"Front text list: {ocr_data.get('front_text', [])}")
				print(f"Back text list: {ocr_data.get('back_text', [])}")
				debug_msg = (
					f"No text extracted. Front: {len(ocr_data.get('front_text', []))} items, "
					f"Back: {len(ocr_data.get('back_text', []))} items. "
					f"Front debug: {ocr_data.get('front_debug', 'N/A')}, "
					f"Back debug: {ocr_data.get('back_debug', 'N/A')}"
				)
				frappe.log_error(message=debug_msg, title="OCR No Text Warning")
			
			# Clean up temp files
			try:
				os.remove(front_path)
				os.remove(back_path)
			except: pass

		except Exception as e:
			import traceback
			error_traceback = traceback.format_exc()
			frappe.log_error(f"OCR Execution Error: {str(e)}\n{error_traceback}", "OCR Error")
			return {
				"success": False, 
				"error": str(e),
				"data": {"name": "", "dob": "", "address": "", "aadhaar_number": "", "gender": ""}
			}
		
		print(f"\n{'='*60}")
		print(f"EXTRACTED TEXT FROM SUPROCESS")
		print(f"Front: {len(front_text)} chars")
		print(f"Back: {len(back_text)} chars")
		
		# Parse data
		try:
			extracted_data = {
				"name": extract_name(front_text) if front_text else "",
				"dob": extract_dob(front_text) if front_text else "",
				"address": extract_address(back_text) if back_text else "",
				"aadhaar_number": extract_aadhaar_number(front_text, back_text) if (front_text or back_text) else "",
				"gender": extract_gender(front_text) if front_text else ""
			}
		except Exception as e:
			frappe.log_error(f"Parsing Error: {str(e)}", "OCR Parse Error")
			extracted_data = {"name": "", "dob": "", "address": "", "aadhaar_number": "", "gender": ""}
		
		return {
			"success": True,
			"_api_version": "2.2 (Refined Name Algo)",
			"data": extracted_data,
			"raw_text": {
				"front": front_text,
				"back": back_text
			},
			"debug": {
				"method": "subprocess",
				"front_text_len": len(front_text),
				"back_text_len": len(back_text)
			}
		}

		
	except Exception as e:
		import traceback
		error_traceback = traceback.format_exc()
		frappe.log_error(
			f"OCR Error: {str(e)}\n\nTraceback:\n{error_traceback}",
			"Aadhaar OCR Extraction Failed"
		)
		frappe.log_error(f"OCR Error Details: {str(e)}\nFull traceback logged above", "OCR Error Summary")
		return {
			"success": False,
			"error": str(e),
			"data": {
				"name": "",
				"dob": "",
				"address": "",
				"aadhaar_number": ""
			}
		}


def extract_text_from_ocr_result(ocr_result) -> str:
	"""Extract text from PaddleOCR result format.
	
	Handles multiple formats:
	1. Standard PaddleOCR (List): [[text_box1, ...], [text_box2, ...]]
	2. Server PaddleOCR (Dict): {'rec_texts': [...], ...}
	3. PaddleOCR Predict (List of OCRResult): [OCRResult(rec_texts=[...]), ...]
	
	Note: OCRResult objects are not dicts but support __getitem__ or have attributes.
	"""
	try:
		# Log the input for debugging
		frappe.log_error(
			f"extract_text_from_ocr_result: Input - type={type(ocr_result)}, "
			f"is_list={isinstance(ocr_result, list)}, "
			f"is_dict={isinstance(ocr_result, dict)}, "
			f"length={len(ocr_result) if isinstance(ocr_result, (list, dict)) else 'N/A'}",
			"OCR Debug"
		)
		
		if not ocr_result:
			frappe.log_error("extract_text_from_ocr_result: Empty result", "OCR Debug")
			return ""
		
		text_lines = []
		
		# Helper to safely get rec_texts from an item
		def get_rec_texts(item):
			# Try dict access
			if isinstance(item, dict):
				return item.get('rec_texts')
			# Try attribute access (for OCRResult objects)
			if hasattr(item, 'rec_texts'):
				return item.rec_texts
			# Try __getitem__ (for OCRResult acting like dict)
			try:
				return item['rec_texts']
			except (TypeError, KeyError, AttributeError):
				return None

		# Case 1: Result is a Dictionary (Single result)
		if isinstance(ocr_result, dict):
			print(f"  extract_text_from_ocr_result: Detected DICT format")
			texts = get_rec_texts(ocr_result)
			if texts and isinstance(texts, list):
				print(f"  Found 'rec_texts' with {len(texts)} items")
				for text in texts:
					if text and str(text).strip():
						text_lines.append(str(text).strip())

		# Case 2: Result is a List (List of results or List of Pages)
		elif isinstance(ocr_result, list):
			print(f"  extract_text_from_ocr_result: Processing LIST with length {len(ocr_result)}")
			if len(ocr_result) == 0:
				print(f"  ⚠️  List is EMPTY - no pages detected")
				return ""
				
			first_element = ocr_result[0]
			print(f"  first_element type: {type(first_element)}")
			print(f"  first_element value: {repr(first_element)[:300]}")
			
			# Check if first element has 'rec_texts' (OCRResult or Dict)
			first_element_texts = get_rec_texts(first_element)
			
			if first_element_texts is not None:
				# It is a list of results (OCRResult or Dicts)
				print(f"  extract_text_from_ocr_result: Detected LIST regarding as RESULTS (OCRResult/Dict)")
				for page in ocr_result:
					texts = get_rec_texts(page)
					if texts and isinstance(texts, list):
						for text in texts:
							if text and str(text).strip():
								text_lines.append(str(text).strip())
			else:
				# Fallback to Legacy List-of-Lists format
				print(f"  extract_text_from_ocr_result: Detected LIST OF LISTS format (Legacy)")
				all_boxes = []
				# Flatten pages if it's nested list items
				if isinstance(first_element, list):
					if len(first_element) > 0 and isinstance(first_element[0], list) and len(first_element[0]) >= 2:
						for page in ocr_result:
							if isinstance(page, list):
								all_boxes.extend(page)
					else:
						# Just one page
						all_boxes = ocr_result
				
				for box in all_boxes:
					try:
						if isinstance(box, (list, tuple)) and len(box) >= 2:
							text_info = box[1] # (text, conf)
							if isinstance(text_info, (list, tuple)) and len(text_info) > 0:
								text = text_info[0]
								if text and str(text).strip():
									text_lines.append(str(text).strip())
							elif isinstance(text_info, str):
								text_lines.append(text_info.strip())
					except Exception:
						continue

		final_text = "\n".join(text_lines)
		# Log success/failure
		if final_text:
			frappe.log_error(f"OCR Success: Extracted {len(text_lines)} lines", "OCR Info")
		else:
			frappe.log_error("OCR Warning: No text lines extracted from valid result structure", "OCR Warning")
			
		return final_text

	except Exception as e:
		import traceback
		error_traceback = traceback.format_exc()
		frappe.log_error(
			f"extract_text_from_ocr_result: Error: {str(e)}\nTraceback:\n{error_traceback}",
			"OCR Parse Error"
		)
		return ""


def extract_name(text: str) -> str:
	"""Extract name from Aadhaar card text."""
	if not text:
		return ""
	
	# Common Aadhaar card keywords to skip
	skip_keywords = ['GOVERNMENT', 'GOVENMENT', 'INDIA', 'AADHAAR', 'MALE', 'FEMALE', 'YEAR', 'DOB', 'DATE OF BIRTH', 'UNIQUE', 'IDENTIFICATION', 'AUTHORITY', 'FATHER', 'HUSBAND']
	
	# Helper function to clean name (remove "Name:" or "Name " prefix)
	def clean_name(name_str):
		if not name_str:
			return ""
		# Remove "Name:" or "Name " prefix (case insensitive)
		name_str = re.sub(r'^name\s*:\s*', '', name_str, flags=re.IGNORECASE).strip()
		name_str = re.sub(r'^name\s+', '', name_str, flags=re.IGNORECASE).strip()
		return name_str
	
	try:
		lines = text.split('\n')
		
		# Strategy 1: Look for name before date pattern
		for i, line in enumerate(lines):
			if not line or len(line.strip()) < 3:
				continue
			
			line_clean = line.strip()
			line_upper = line_clean.upper()
			
			# Skip if contains skip keywords
			if any(keyword in line_upper for keyword in skip_keywords):
				continue
			
			# If this line is a date, check previous line for name
			if re.match(r'^\d+[/-]\d+[/-]\d+', line_clean):
				if i > 0 and i - 1 < len(lines):
					prev_line = lines[i-1].strip()
					# Check if previous line looks like a name
					if len(prev_line) > 2 and not re.search(r'^\d+$', prev_line):
						name = re.sub(r'[^\w\s]', '', prev_line).strip()
						name = clean_name(name)  # Remove "Name:" prefix
						if len(name) > 2 and not any(kw in name.upper() for kw in skip_keywords):
							return name.title()
			
			# Strategy 2: Look for lines that are mostly letters (name-like)
			# Remove special chars and check
			clean_line = re.sub(r'[^\w\s]', '', line_clean)
			# Check if it's mostly letters and spaces, 3-50 chars, no numbers
			if re.match(r'^[A-Za-z\s]{3,50}$', clean_line):
				# Make sure it's not all caps single word (might be a label)
				words = clean_line.split()
				if 1 <= len(words) <= 5:  # Name usually 1-5 words
					if not any(kw in clean_line.upper() for kw in skip_keywords):
						clean_line = clean_name(clean_line)  # Remove "Name:" prefix
						if clean_line:
							return clean_line.title()
		
		# Strategy 3: Look for first substantial text line (usually name)
		for line in lines:
			line_clean = line.strip()
			if len(line_clean) < 3:
				continue
			
			# Skip if it's clearly not a name
			if re.match(r'^\d+', line_clean):  # Starts with number
				continue
			if any(kw in line_clean.upper() for kw in skip_keywords):
				continue
			
			# If it has letters and looks like a name
			if re.search(r'[A-Za-z]', line_clean) and len(line_clean.split()) <= 5:
				clean_name_text = re.sub(r'[^\w\s]', '', line_clean).strip()
				clean_name_text = clean_name(clean_name_text)  # Remove "Name:" prefix
				if len(clean_name_text) > 2:
					return clean_name_text.title()
	except Exception:
		pass
	
	return ""


def extract_dob(text: str) -> str:
	"""Extract date of birth from Aadhaar card text."""
	if not text:
		return ""
	
	# DOB format: DD/MM/YYYY, DD-MM-YYYY, or YYYY/MM/DD
	# Also handle spaces: DD / MM / YYYY
	dob_patterns = [
		r'\b(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})\b',  # DD/MM/YYYY or DD-MM-YYYY (with optional spaces)
		r'\b(\d{4})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{1,2})\b',  # YYYY/MM/DD (with optional spaces)
		r'\b(\d{1,2})\s+(\d{1,2})\s+(\d{4})\b',  # DD MM YYYY (space separated)
	]
	
	for pattern in dob_patterns:
		try:
			matches = re.findall(pattern, text)
			if matches:
				for match in matches:
					# Ensure match is a tuple/list with at least 3 elements
					if not isinstance(match, (tuple, list)) or len(match) < 3:
						continue
					
					# Try to determine format
					try:
						part1, part2, part3 = match[0].strip(), match[1].strip(), match[2].strip()
						
						# Check if part3 is 4 digits (year) - DD/MM/YYYY format
						if len(part3) == 4 and part3.isdigit():
							day, month, year = part1, part2, part3
							day_int, month_int = int(day), int(month)
							if 1 <= day_int <= 31 and 1 <= month_int <= 12:
								# Validate year is reasonable (1900-2100)
								year_int = int(year)
								if 1900 <= year_int <= 2100:
									return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
						# Check if part1 is 4 digits (year) - YYYY/MM/DD format
						elif len(part1) == 4 and part1.isdigit():
							year, month, day = part1, part2, part3
							day_int, month_int = int(day), int(month)
							if 1 <= day_int <= 31 and 1 <= month_int <= 12:
								year_int = int(year)
								if 1900 <= year_int <= 2100:
									return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
					except (ValueError, IndexError, TypeError):
						continue
		except Exception:
			continue
	
	return ""


def extract_address(text: str) -> str:
	"""Extract address from back of Aadhaar card - text after 'Address:' until pincode, or address-like lines if no keyword."""
	if not text:
		return ""
	
	# Patterns to filter out
	email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
	url_pattern = r'(www\.|http://|https://)[^\s]+'
	
	address_lines = []
	pincode = None
	lines = text.split('\n')
	address_started = False
	pincode_found = False
	has_address_keyword = False
	
	# First, check if "Address:" keyword exists
	for line in lines:
		if 'address:' in line.lower():
			has_address_keyword = True
			break
	
	# If no "Address:" keyword, we'll start collecting from the beginning
	# looking for address-like patterns
	if not has_address_keyword:
		address_started = True
	
	for line in lines:
		line_clean = line.strip()
		line_lower = line_clean.lower()
		
		# Skip empty lines
		if not line_clean:
			continue
		
		# Check if this line contains "Address:" keyword
		if 'address:' in line_lower:
			# Extract text after "Address:"
			address_match = re.search(r'address:\s*(.+)', line_clean, re.IGNORECASE)
			if address_match:
				address_part = address_match.group(1).strip()
				# Remove care-of patterns from the extracted part
				care_of_patterns = [
					r'\b[CcSsDdWw]/[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
					r'\b[CcSsDdWw]\s*/\s*[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
					r'\b(?:C/O|S/O|D/O|W/O|c/o|s/o|d/o|w/o)[\s:]*[A-Za-z\s,]+(?:,|$)',
				]
				for pattern in care_of_patterns:
					address_part = re.sub(pattern, '', address_part, flags=re.IGNORECASE).strip()
				# Remove "Addrens" if present
				address_part = re.sub(r'\b(addrens)[\s:]*', '', address_part, flags=re.IGNORECASE).strip()
				if address_part:
					address_lines.append(address_part)
				address_started = True
			else:
				# If "Address:" is the whole line, start collecting from next line
				address_started = True
			continue
		
		# If we haven't found "Address:" yet and keyword is required, skip this line
		if not address_started:
			continue
		
		# Check if this line contains the pincode (6 digits)
		pincode_match = re.search(r'\b(\d{6})\b', line_clean)
		if pincode_match:
			pincode = pincode_match.group(1)
			# Clean the line (remove email/URL if present) before adding
			line_cleaned = re.sub(email_pattern, '', line_clean, flags=re.IGNORECASE)
			line_cleaned = re.sub(url_pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
			
			# Remove care-of patterns (C/O, S/O, D/O, W/O, etc.) with the name that follows
			care_of_patterns = [
				r'\b[CcSsDdWw]/[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
				r'\b[CcSsDdWw]\s*/\s*[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
				r'\b(?:C/O|S/O|D/O|W/O|c/o|s/o|d/o|w/o)[\s:]*[A-Za-z\s,]+(?:,|$)',
			]
			for pattern in care_of_patterns:
				line_cleaned = re.sub(pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
			
			# Remove "Addrens" or "Address" if it appears in the line
			line_cleaned = re.sub(r'\b(addrens|address)[\s:]*', '', line_cleaned, flags=re.IGNORECASE).strip()
			
			# Skip if it's just a URL or website
			if not any(skip_kw in line_cleaned.lower() for skip_kw in ['www.', 'http', '.gov', '.in']):
				# Add the full line (which includes state and pincode)
				if line_cleaned and len(line_cleaned) >= 2:
					address_lines.append(line_cleaned)
			pincode_found = True
			break
		
		# If it's just 6 digits (pincode only), add it and stop
		if re.match(r'^\d{6}$', line_clean.replace(' ', '').replace('-', '')):
			pincode = line_clean.replace(' ', '').replace('-', '')
			if pincode not in "\n".join(address_lines):
				address_lines.append(pincode)
			pincode_found = True
			break
		
		# Skip email addresses
		if re.search(email_pattern, line_clean, re.IGNORECASE):
			continue
		
		# Skip URLs/websites
		if re.search(url_pattern, line_clean, re.IGNORECASE):
			continue
		if any(skip_kw in line_lower for skip_kw in ['www.', 'http', '.gov', '.in', '.com', 'uidal']):
			continue
		
		# Skip if it's Aadhaar number (12 digits) - stop collecting
		if re.match(r'^\d{12}$', line_clean.replace(' ', '').replace('-', '')):
			break
		
		# Skip if it's a date
		if re.match(r'^\d+[/-]\d+[/-]\d+', line_clean):
			continue
		
		# Skip if it's just numbers (but not pincode)
		if re.match(r'^\d+$', line_clean.replace(' ', '').replace('-', '')):
			if len(line_clean.replace(' ', '').replace('-', '')) != 6:
				continue
		
		# If no "Address:" keyword was found, filter lines to only include address-like patterns
		if not has_address_keyword:
			# Skip lines that are too short or look like OCR noise
			if len(line_clean) < 5:
				continue
			
			# Skip lines that are mostly special characters or single characters
			if re.match(r'^[^A-Za-z0-9\s]{3,}$', line_clean):
				continue
			
			# Skip lines that look like OCR errors (e.g., "/c", "2,2-", etc.)
			if re.match(r'^[/\\\-_]{2,}', line_clean) or re.match(r'^[^A-Za-z]{4,}$', line_clean):
				continue
			
			# Prefer lines that contain address-like keywords or patterns
			address_keywords = ['house', 'road', 'street', 'lane', 'city', 'state', 'village', 'town', 'district', 'pincode', 'pin']
			line_has_address_keyword = any(kw in line_lower for kw in address_keywords)
			
			# Also accept lines with letters, numbers, and common punctuation (looks like address)
			has_letters = bool(re.search(r'[A-Za-z]', line_clean))
			has_numbers = bool(re.search(r'\d', line_clean))
			has_common_punct = bool(re.search(r'[,.\-]', line_clean))
			
			# Skip if line doesn't look address-like (no address keywords and doesn't have letters+numbers)
			if not line_has_address_keyword and not (has_letters and (has_numbers or has_common_punct)):
				continue
		
		# Clean the line (remove email/URL if present)
		line_cleaned = re.sub(email_pattern, '', line_clean, flags=re.IGNORECASE)
		line_cleaned = re.sub(url_pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
		
		# Remove care-of patterns (C/O, S/O, D/O, W/O, etc.) with the name that follows
		# Pattern matches: C/O, S/O, D/O, W/O, C/O:, S/O:, etc. followed by name (until comma or end)
		care_of_patterns = [
			r'\b[CcSsDdWw]/[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
			r'\b[CcSsDdWw]\s*/\s*[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
			r'\b(?:C/O|S/O|D/O|W/O|c/o|s/o|d/o|w/o)[\s:]*[A-Za-z\s,]+(?:,|$)',
		]
		for pattern in care_of_patterns:
			line_cleaned = re.sub(pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
		
		# Remove "Addrens" or "Address" if it appears in the line (OCR error)
		line_cleaned = re.sub(r'\b(addrens|address)[\s:]*', '', line_cleaned, flags=re.IGNORECASE).strip()
		
		# Skip if line is now empty or too short
		if not line_cleaned or len(line_cleaned) < 2:
			continue
		
		# Skip if it's just a URL or website
		if any(skip_kw in line_cleaned.lower() for skip_kw in ['www.', 'http', '.gov', '.in']):
			continue
			
		# Add the line to address
		address_lines.append(line_cleaned)
		
		# Stop if we've collected too many lines (safety limit)
		if len(address_lines) >= 15:
			break
	
	# If we found address but no pincode yet, look for pincode in the remaining text
	if address_lines and not pincode_found:
		# Check if pincode is already in the address lines
		for line in address_lines:
			pincode_match = re.search(r'\b(\d{6})\b', line)
			if pincode_match:
				pincode_found = True
				break
	
	# Join address lines
	address_text = "\n".join(address_lines)
	
	return address_text


def extract_gender(text: str) -> str:
	"""Extract gender from Aadhaar card text."""
	if not text:
		return ""
	
	text_upper = text.upper()
	
	# Look for gender indicators
	if 'MALE' in text_upper:
		return "Male"
	elif 'FEMALE' in text_upper:
		return "Female"
	
	# Sometimes it's abbreviated
	if re.search(r'\bM\b', text_upper):
		# Check context - if it's near gender-related keywords
		if re.search(r'(MALE|GENDER|SEX)', text_upper):
			return "Male"
	
	if re.search(r'\bF\b', text_upper):
		# Check context - if it's near gender-related keywords
		if re.search(r'(FEMALE|GENDER|SEX)', text_upper):
			return "Female"
	
	return ""


def extract_aadhaar_number(front_text: str, back_text: str) -> str:
	"""Extract Aadhaar number from text."""
	# Aadhaar number is 12 digits, may be formatted as XXXX XXXX XXXX or XXXX-XXXX-XXXX
	# Priority: formatted numbers first (more reliable), then plain 12 digits
	aadhaar_patterns = [
		(r'\b(\d{4}[\s-]\d{4}[\s-]\d{4})\b', 0),  # Formatted with separator: XXXX XXXX XXXX (highest priority)
		(r'\b(\d{4}\s+\d{4}\s+\d{4})\b', 1),  # Space separated: XXXX XXXX XXXX (explicit spaces)
		(r'\b(\d{12})\b', 2),  # Plain: 12 consecutive digits (lowest priority)
	]
	
	all_matches = []
	
	# Check both front and back
	for text in [front_text, back_text]:
		if not text:
			continue
		
		for pattern, priority in aadhaar_patterns:
			try:
				matches = re.findall(pattern, text)
				if matches and len(matches) > 0:
					for match in matches:
						if isinstance(match, (tuple, list)):
							# If it's a tuple/list, get the first element
							aadhaar = str(match[0]) if len(match) > 0 else str(match)
						else:
							aadhaar = str(match)
						
						# Clean up: remove spaces, dashes, and other separators
						aadhaar_clean = re.sub(r'[\s\-]', '', aadhaar)
						
						# Validate: must be exactly 12 digits
						if len(aadhaar_clean) == 12 and aadhaar_clean.isdigit():
							# Store with priority (lower priority number = higher priority)
							all_matches.append((priority, aadhaar_clean))
			except (IndexError, TypeError, AttributeError):
				continue
	
	# Return the match with highest priority (lowest priority number)
	# If multiple matches with same priority, return the first one
	if all_matches:
		# Sort by priority (lower number = higher priority), then take first
		all_matches.sort(key=lambda x: x[0])
		return all_matches[0][1]
	
	return ""


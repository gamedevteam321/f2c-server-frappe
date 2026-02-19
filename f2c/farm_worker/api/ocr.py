# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import base64
import re
import numpy as np
import threading
import hashlib
import json
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps
from typing import Dict, Optional

# Global OCR instance with thread-safe singleton pattern
_ocr_lock = threading.Lock()
_ocr_instance = None


def get_ocr_instance():
	"""Get or create PaddleOCR instance (thread-safe singleton pattern for performance)."""
	global _ocr_instance
	
	if _ocr_instance is None:
		with _ocr_lock:
			# Double-check locking pattern
			if _ocr_instance is None:
				try:
					from paddleocr import PaddleOCR
				except ImportError:
					frappe.throw("PaddleOCR is not installed. Please install it using: pip install paddlepaddle paddleocr")
				
				# Suppress PaddleOCR logs
				import logging
				logging.getLogger('ppocr').setLevel(logging.ERROR)
				
				# Initialize PaddleOCR instance
				print("Initializing PaddleOCR instance (Singleton enabled with thread safety)...")
				_ocr_instance = PaddleOCR(
					use_textline_orientation=True,  # Enable textline orientation (replaces use_angle_cls)
					lang='en'            # English language
				)
				print("PaddleOCR instance initialized successfully")
	
	return _ocr_instance


def extract_qr_code_data(img: Image.Image) -> Optional[Dict]:
	"""
	Extract Aadhaar data from Secure QR Code on the card.
	
	Args:
		img: PIL Image object
		
	Returns:
		Dictionary with extracted fields if QR code found, None otherwise
	"""
	try:
		import pyzbar.pyzbar as pyzbar
		import cv2
		
		# Convert PIL Image to numpy array for OpenCV
		img_array = np.array(img.convert('RGB'))
		
		# Convert RGB to BGR for OpenCV
		img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
		
		# Convert to grayscale for better QR code detection
		gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
		
		# Try to decode QR codes
		qr_codes = pyzbar.decode(gray)
		
		if not qr_codes:
			# Try with different preprocessing if no QR code found
			# Apply thresholding
			_, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
			qr_codes = pyzbar.decode(thresh)
		
		if not qr_codes:
			return None
		
		# Process the first QR code found
		qr_data = qr_codes[0].data.decode('utf-8')
		
		# Aadhaar Secure QR Code contains XML-like data
		# Parse the XML to extract fields
		extracted = {}
		
		# Extract name
		name_match = re.search(r'<name>([^<]+)</name>', qr_data, re.IGNORECASE)
		if name_match:
			extracted['name'] = name_match.group(1).strip()
		
		# Extract DOB (format: YYYY-MM-DD or DD-MM-YYYY)
		dob_match = re.search(r'<dob>([^<]+)</dob>', qr_data, re.IGNORECASE)
		if dob_match:
			dob_str = dob_match.group(1).strip()
			# Convert to YYYY-MM-DD format
			dob_parts = re.split(r'[-/]', dob_str)
			if len(dob_parts) == 3:
				if len(dob_parts[0]) == 4:  # YYYY-MM-DD
					extracted['dob'] = dob_str
				else:  # DD-MM-YYYY
					extracted['dob'] = f"{dob_parts[2]}-{dob_parts[1].zfill(2)}-{dob_parts[0].zfill(2)}"
		
		# Extract gender
		gender_match = re.search(r'<gender>([^<]+)</gender>', qr_data, re.IGNORECASE)
		if gender_match:
			gender = gender_match.group(1).strip().upper()
			if gender in ['M', 'MALE']:
				extracted['gender'] = 'Male'
			elif gender in ['F', 'FEMALE']:
				extracted['gender'] = 'Female'
		
		# Extract Aadhaar number
		aadhaar_match = re.search(r'<uid>([^<]+)</uid>', qr_data, re.IGNORECASE)
		if aadhaar_match:
			aadhaar = re.sub(r'[\s\-]', '', aadhaar_match.group(1).strip())
			if len(aadhaar) == 12 and aadhaar.isdigit():
				extracted['aadhaar_number'] = aadhaar
		
		# Extract address (may be in <co> or <house> or <street> or <lm> or <loc> or <vtc> or <po> or <dist> or <state> or <pc>)
		address_parts = []
		address_fields = ['co', 'house', 'street', 'lm', 'loc', 'vtc', 'po', 'dist', 'state', 'pc']
		for field in address_fields:
			field_match = re.search(rf'<{field}>([^<]+)</{field}>', qr_data, re.IGNORECASE)
			if field_match:
				address_parts.append(field_match.group(1).strip())
		
		if address_parts:
			extracted['address'] = ', '.join(address_parts)
		
		# Return extracted data if we found at least name or Aadhaar number
		if extracted.get('name') or extracted.get('aadhaar_number'):
			return extracted
		
		return None
		
	except ImportError:
		# pyzbar or cv2 not installed
		return None
	except Exception as e:
		frappe.log_error(f"QR Code extraction error: {str(e)}", "QR Code Error")
		return None


def preprocess_image(img: Image.Image) -> Image.Image:
	"""
	Enhanced image preprocessing for better OCR accuracy.
	
	Args:
		img: PIL Image object
		
	Returns:
		Preprocessed PIL Image object
	"""
	try:
		import cv2
	except ImportError:
		# Fallback to basic preprocessing if OpenCV not available
		return _basic_preprocess_image(img)
	
	# Get current size
	width, height = img.size
	
	# Resize if image is too small (minimum 300px width for better OCR)
	if width < 300:
		scale_factor = 300 / width
		new_width = 300
		new_height = int(height * scale_factor)
		img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
	
	# Convert PIL Image to numpy array for OpenCV processing
	img_array = np.array(img.convert('RGB'))
	img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
	
	# Convert to grayscale
	gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
	
	# Denoising
	try:
		denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
	except:
		# Fallback to bilateral filter if fastNlMeansDenoising fails
		denoised = cv2.bilateralFilter(gray, 9, 75, 75)
	
	# Deskewing - detect and correct rotation
	try:
		# Use Hough transform to detect lines
		edges = cv2.Canny(denoised, 50, 150, apertureSize=3)
		lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
		
		if lines is not None and len(lines) > 0:
			angles = []
			for rho, theta in lines[:min(20, len(lines))]:
				angle = (theta * 180 / np.pi) - 90
				if -45 <= angle <= 45:
					angles.append(angle)
			
			if angles:
				median_angle = np.median(angles)
				if abs(median_angle) > 0.5:  # Only correct if angle is significant
					# Rotate image
					center = (denoised.shape[1] // 2, denoised.shape[0] // 2)
					rotation_matrix = cv2.getRotationMatrix2D(center, median_angle, 1.0)
					denoised = cv2.warpAffine(denoised, rotation_matrix, (denoised.shape[1], denoised.shape[0]),
					                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
	except:
		pass  # Skip deskewing if it fails
	
	# Sharpening using unsharp mask
	try:
		gaussian = cv2.GaussianBlur(denoised, (0, 0), 2.0)
		sharpened = cv2.addWeighted(denoised, 1.5, gaussian, -0.5, 0)
		denoised = sharpened
	except:
		pass
	
	# Adaptive thresholding (binarization) - Otsu's method
	try:
		_, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
		denoised = binary
	except:
		pass
	
	# Convert back to RGB PIL Image
	denoised_rgb = cv2.cvtColor(denoised, cv2.COLOR_GRAY2RGB)
	img_processed = Image.fromarray(denoised_rgb)
	
	return img_processed


def _basic_preprocess_image(img: Image.Image) -> Image.Image:
	"""Basic preprocessing fallback when OpenCV is not available."""
	width, height = img.size
	
	# Resize if image is too small
	if width < 300:
		scale_factor = 300 / width
		new_width = 300
		new_height = int(height * scale_factor)
		img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
	
	# Enhance contrast using ImageOps
	try:
		img_gray = img.convert('L')
		img_gray = ImageOps.autocontrast(img_gray, cutoff=2)
		img = img_gray.convert('RGB')
	except Exception:
		pass
	
	return img


def validate_aadhaar_number(aadhaar: str) -> bool:
	"""
	Validate Aadhaar number using Verhoeff checksum algorithm.
	
	Args:
		aadhaar: 12-digit Aadhaar number string
		
	Returns:
		True if valid, False otherwise
	"""
	if not aadhaar or len(aadhaar) != 12 or not aadhaar.isdigit():
		return False
	
	# Verhoeff algorithm multiplication table
	mult = [
		[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		[1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
		[2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
		[3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
		[4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
		[5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
		[6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
		[7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
		[8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
		[9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
	]
	
	# Verhoeff permutation table
	perm = [
		[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		[1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
		[5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
		[8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
		[9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
		[4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
		[2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
		[7, 0, 4, 6, 9, 1, 3, 2, 5, 8]
	]
	
	check = 0
	for i in range(len(aadhaar)):
		check = mult[check][perm[((i + 1) % 8)][int(aadhaar[len(aadhaar) - 1 - i])]]
	
	return check == 0


def validate_date(date_str: str) -> bool:
	"""
	Validate date string in YYYY-MM-DD format.
	
	Args:
		date_str: Date string
		
	Returns:
		True if valid, False otherwise
	"""
	if not date_str:
		return False
	
	try:
		from datetime import datetime
		date_obj = datetime.strptime(date_str, '%Y-%m-%d')
		# Check if date is reasonable (between 1900 and 2100)
		return 1900 <= date_obj.year <= 2100
	except:
		return False


def validate_name(name: str) -> bool:
	"""
	Validate name format.
	
	Args:
		name: Name string
		
	Returns:
		True if valid, False otherwise
	"""
	if not name:
		return False
	
	# Name should be at least 2 characters, contain letters, and be reasonable length
	if len(name.strip()) < 2 or len(name.strip()) > 100:
		return False
	
	# Should contain at least some letters
	if not re.search(r'[A-Za-z]', name):
		return False
	
	return True


def get_image_hash(img_data: bytes) -> str:
	"""Generate SHA256 hash of image data for caching."""
	return hashlib.sha256(img_data).hexdigest()


@frappe.whitelist(allow_guest=False)
def extract_aadhaar_details(front_image: str, back_image: str) -> Dict:
	"""
	Extract details from Aadhaar card images using Gemini 2.5 Flash Lite API.
	
	Args:
		front_image: Base64 encoded front Aadhaar card image
		back_image: Base64 encoded back Aadhaar card image
		
	Returns:
		Dictionary with extracted fields: name, dob, address, aadhaar_number, gender
	"""
	try:
		# Decode base64 images
		front_img_data = base64.b64decode(front_image.split(',')[-1] if ',' in front_image else front_image)
		back_img_data = base64.b64decode(back_image.split(',')[-1] if ',' in back_image else back_image)
		
		# Check cache first
		front_hash = get_image_hash(front_img_data)
		back_hash = get_image_hash(back_img_data)
		cache_key = f"aadhaar_gemini_{front_hash}_{back_hash}"
		
		cached_result = frappe.cache().get(cache_key)
		if cached_result:
			print("Returning cached Gemini extraction result")
			return json.loads(cached_result)
		
		# Convert to PIL Images
		front_img = Image.open(BytesIO(front_img_data))
		back_img = Image.open(BytesIO(back_img_data))
		
		# Convert to RGB if needed
		if front_img.mode != 'RGB':
			front_img = front_img.convert('RGB')
		if back_img.mode != 'RGB':
			back_img = back_img.convert('RGB')
		
		# Use Gemini API for extraction
		from f2c.farm_worker.api.gemini_extract import extract_aadhaar_details as gemini_extract
		
		# Call Gemini extraction
		result = gemini_extract(front_img, back_img)
		
		# Update cache key for Gemini
		cache_key = f"aadhaar_gemini_{front_hash}_{back_hash}"
		
		# Cache the result (24 hours)
		if result.get("success"):
			frappe.cache().setex(cache_key, 86400, json.dumps(result))
		
		return result
		
	except Exception as e:
		import traceback
		error_traceback = traceback.format_exc()
		frappe.log_error(
			f"Gemini Extraction Error: {str(e)}\n\nTraceback:\n{error_traceback}",
			"Aadhaar Gemini Extraction Failed"
		)
		return {
			"success": False,
			"_api_version": "4.0 (Gemini 2.5 Flash Lite)",
			"error": str(e),
			"data": {
				"name": "",
				"dob": "",
				"address": "",
				"aadhaar_number": "",
				"gender": ""
			},
			"raw_text": {
				"front": "",
				"back": ""
			},
			"debug": {
				"method": "error",
				"error": str(e)
			}
		}




def _ocr_via_subprocess_parallel(front_img: Image.Image, back_img: Image.Image) -> tuple:
	"""
	Fallback OCR via subprocess with parallel processing (if direct OCR fails).
	Processes front and back images in parallel using separate subprocesses.
	
	Args:
		front_img: Preprocessed front image
		back_img: Preprocessed back image
		
	Returns:
		Tuple of (front_text, back_text)
	"""
	try:
		import subprocess
		import os
		import sys
		
		# Save preprocessed images to temp files
		temp_dir = "/tmp/ocr_process"
		os.makedirs(temp_dir, exist_ok=True)
		timestamp = frappe.utils.now().replace(' ', '_').replace(':', '-')
		front_path = f"{temp_dir}/front_{timestamp}.png"
		back_path = f"{temp_dir}/back_{timestamp}.png"
		
		front_img.save(front_path)
		back_img.save(back_path)
		
		runner_path = os.path.join(os.path.dirname(__file__), "ocr_runner.py")
		python_path = sys.executable
		
		front_text = ""
		back_text = ""
		front_error = None
		back_error = None
		
		def process_front_subprocess():
			nonlocal front_text, front_error
			try:
				# Run subprocess for front image only
				# Increased timeout to 45s to account for PaddleOCR initialization (~5-10s) + processing (~15-20s)
				cmd = [python_path, runner_path, front_path]
				result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
				
				if result.returncode != 0:
					raise Exception(f"Front OCR subprocess failed: {result.stderr[:200] if result.stderr else 'Unknown error'}")
				
				# Parse JSON output
				stdout_text = result.stdout.strip()
				ocr_data = None
				
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
				
				if not ocr_data or not ocr_data.get("success"):
					raise Exception("Front OCR subprocess returned invalid data")
				
				front_text = "\n".join(ocr_data.get("front_text", []))
			except Exception as e:
				front_error = str(e)
			finally:
				# Clean up temp file
				try:
					os.remove(front_path)
				except:
					pass
		
		def process_back_subprocess():
			nonlocal back_text, back_error
			try:
				# Run subprocess for back image only
				# Increased timeout to 45s to account for PaddleOCR initialization (~5-10s) + processing (~15-20s)
				cmd = [python_path, runner_path, back_path]
				result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
				
				if result.returncode != 0:
					raise Exception(f"Back OCR subprocess failed: {result.stderr[:200] if result.stderr else 'Unknown error'}")
				
				# Parse JSON output
				stdout_text = result.stdout.strip()
				ocr_data = None
				
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
				
				if not ocr_data or not ocr_data.get("success"):
					raise Exception("Back OCR subprocess returned invalid data")
				
				back_text = "\n".join(ocr_data.get("back_text", []))
			except Exception as e:
				back_error = str(e)
			finally:
				# Clean up temp file
				try:
					os.remove(back_path)
				except:
					pass
		
		# Start both threads for parallel subprocess execution
		front_thread = threading.Thread(target=process_front_subprocess)
		back_thread = threading.Thread(target=process_back_subprocess)
		
		front_thread.start()
		back_thread.start()
		
		# Wait for both threads to complete (with timeout)
		# Increased timeout to 45s per image for subprocess (needs OCR initialization)
		front_thread.join(timeout=45)
		back_thread.join(timeout=45)
		
		# Check if threads completed
		if front_thread.is_alive() or back_thread.is_alive():
			raise Exception("OCR subprocess processing timed out (45s per image)")
		
		# Check for errors
		if front_error:
			frappe.log_error(f"Front subprocess OCR error: {front_error}", "OCR Subprocess Error")
		if back_error:
			frappe.log_error(f"Back subprocess OCR error: {back_error}", "OCR Subprocess Error")
		
		return front_text, back_text
		
	except Exception as e:
		frappe.log_error(f"Subprocess OCR error: {str(e)}", "OCR Subprocess Error")
		return "", ""


def extract_text_from_ocr_result(ocr_result) -> str:
	"""Extract text from PaddleOCR result format."""
	try:
		if not ocr_result:
			return ""
		
		text_lines = []
		
		def get_rec_texts(item):
			if isinstance(item, dict):
				return item.get('rec_texts')
			if hasattr(item, 'rec_texts'):
				return item.rec_texts
			try:
				return item['rec_texts']
			except (TypeError, KeyError, AttributeError):
				return None
		
		# Case 1: Result is a Dictionary
		if isinstance(ocr_result, dict):
			texts = get_rec_texts(ocr_result)
			if texts and isinstance(texts, list):
				for text in texts:
					if text and str(text).strip():
						text_lines.append(str(text).strip())
		
		# Case 2: Result is a List
		elif isinstance(ocr_result, list):
			if len(ocr_result) == 0:
				return ""
			
			first_element = ocr_result[0]
			first_element_texts = get_rec_texts(first_element)
			
			if first_element_texts is not None:
				for page in ocr_result:
					texts = get_rec_texts(page)
					if texts and isinstance(texts, list):
						for text in texts:
							if text and str(text).strip():
								text_lines.append(str(text).strip())
			else:
				# Legacy List-of-Lists format
				all_boxes = []
				if isinstance(first_element, list):
					if len(first_element) > 0 and isinstance(first_element[0], list) and len(first_element[0]) >= 2:
						for page in ocr_result:
							if isinstance(page, list):
								all_boxes.extend(page)
					else:
						all_boxes = ocr_result
				
				for box in all_boxes:
					try:
						if isinstance(box, (list, tuple)) and len(box) >= 2:
							text_info = box[1]
							if isinstance(text_info, (list, tuple)) and len(text_info) > 0:
								text = text_info[0]
								if text and str(text).strip():
									text_lines.append(str(text).strip())
							elif isinstance(text_info, str):
								text_lines.append(text_info.strip())
					except Exception:
						continue
		
		return "\n".join(text_lines)
		
	except Exception as e:
		frappe.log_error(f"extract_text_from_ocr_result error: {str(e)}", "OCR Parse Error")
		return ""


def extract_name(text: str) -> str:
	"""Extract name from Aadhaar card text."""
	if not text:
		return ""
	
	skip_keywords = ['GOVERNMENT', 'GOVENMENT', 'INDIA', 'AADHAAR', 'MALE', 'FEMALE', 'YEAR', 'DOB', 'DATE OF BIRTH', 'UNIQUE', 'IDENTIFICATION', 'AUTHORITY', 'FATHER', 'HUSBAND']
	
	def clean_name(name_str):
		if not name_str:
			return ""
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
			
			if any(keyword in line_upper for keyword in skip_keywords):
				continue
			
			if re.match(r'^\d+[/-]\d+[/-]\d+', line_clean):
				if i > 0 and i - 1 < len(lines):
					prev_line = lines[i-1].strip()
					if len(prev_line) > 2 and not re.search(r'^\d+$', prev_line):
						name = re.sub(r'[^\w\s]', '', prev_line).strip()
						name = clean_name(name)
						if len(name) > 2 and not any(kw in name.upper() for kw in skip_keywords):
							return name.title()
			
			# Strategy 2: Look for lines that are mostly letters
			clean_line = re.sub(r'[^\w\s]', '', line_clean)
			if re.match(r'^[A-Za-z\s]{3,50}$', clean_line):
				words = clean_line.split()
				if 1 <= len(words) <= 5:
					if not any(kw in clean_line.upper() for kw in skip_keywords):
						clean_line = clean_name(clean_line)
						if clean_line:
							return clean_line.title()
		
		# Strategy 3: Look for first substantial text line
		for line in lines:
			line_clean = line.strip()
			if len(line_clean) < 3:
				continue
			
			if re.match(r'^\d+', line_clean):
				continue
			if any(kw in line_clean.upper() for kw in skip_keywords):
				continue
			
			if re.search(r'[A-Za-z]', line_clean) and len(line_clean.split()) <= 5:
				clean_name_text = re.sub(r'[^\w\s]', '', line_clean).strip()
				clean_name_text = clean_name(clean_name_text)
				if len(clean_name_text) > 2:
					return clean_name_text.title()
	except Exception:
		pass
	
	return ""


def extract_dob(text: str) -> str:
	"""Extract date of birth from Aadhaar card text."""
	if not text:
		return ""
	
	dob_patterns = [
		r'\b(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})\b',
		r'\b(\d{4})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{1,2})\b',
		r'\b(\d{1,2})\s+(\d{1,2})\s+(\d{4})\b',
	]
	
	for pattern in dob_patterns:
		try:
			matches = re.findall(pattern, text)
			if matches:
				for match in matches:
					if not isinstance(match, (tuple, list)) or len(match) < 3:
						continue
					
					try:
						part1, part2, part3 = match[0].strip(), match[1].strip(), match[2].strip()
						
						if len(part3) == 4 and part3.isdigit():
							day, month, year = part1, part2, part3
							day_int, month_int = int(day), int(month)
							if 1 <= day_int <= 31 and 1 <= month_int <= 12:
								year_int = int(year)
								if 1900 <= year_int <= 2100:
									return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
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
	"""Extract address from back of Aadhaar card."""
	if not text:
		return ""
	
	email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
	url_pattern = r'(www\.|http://|https://)[^\s]+'
	
	address_lines = []
	lines = text.split('\n')
	address_started = False
	pincode_found = False
	has_address_keyword = False
	
	for line in lines:
		if 'address:' in line.lower():
			has_address_keyword = True
			break
	
	if not has_address_keyword:
		address_started = True
	
	for line in lines:
		line_clean = line.strip()
		line_lower = line_clean.lower()
		
		if not line_clean:
			continue
		
		if 'address:' in line_lower:
			address_match = re.search(r'address:\s*(.+)', line_clean, re.IGNORECASE)
			if address_match:
				address_part = address_match.group(1).strip()
				care_of_patterns = [
					r'\b[CcSsDdWw]/[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
					r'\b[CcSsDdWw]\s*/\s*[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
					r'\b(?:C/O|S/O|D/O|W/O|c/o|s/o|d/o|w/o)[\s:]*[A-Za-z\s,]+(?:,|$)',
				]
				for pattern in care_of_patterns:
					address_part = re.sub(pattern, '', address_part, flags=re.IGNORECASE).strip()
				address_part = re.sub(r'\b(addrens)[\s:]*', '', address_part, flags=re.IGNORECASE).strip()
				if address_part:
					address_lines.append(address_part)
				address_started = True
			else:
				address_started = True
			continue
		
		if not address_started:
			continue
		
		pincode_match = re.search(r'\b(\d{6})\b', line_clean)
		if pincode_match:
			pincode = pincode_match.group(1)
			line_cleaned = re.sub(email_pattern, '', line_clean, flags=re.IGNORECASE)
			line_cleaned = re.sub(url_pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
			
			care_of_patterns = [
				r'\b[CcSsDdWw]/[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
				r'\b[CcSsDdWw]\s*/\s*[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
				r'\b(?:C/O|S/O|D/O|W/O|c/o|s/o|d/o|w/o)[\s:]*[A-Za-z\s,]+(?:,|$)',
			]
			for pattern in care_of_patterns:
				line_cleaned = re.sub(pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
			
			line_cleaned = re.sub(r'\b(addrens|address)[\s:]*', '', line_cleaned, flags=re.IGNORECASE).strip()
			
			if not any(skip_kw in line_cleaned.lower() for skip_kw in ['www.', 'http', '.gov', '.in']):
				if line_cleaned and len(line_cleaned) >= 2:
					address_lines.append(line_cleaned)
			pincode_found = True
			break
		
		if re.match(r'^\d{6}$', line_clean.replace(' ', '').replace('-', '')):
			pincode = line_clean.replace(' ', '').replace('-', '')
			if pincode not in "\n".join(address_lines):
				address_lines.append(pincode)
			pincode_found = True
			break
		
		if re.search(email_pattern, line_clean, re.IGNORECASE):
			continue
		
		if re.search(url_pattern, line_clean, re.IGNORECASE):
			continue
		if any(skip_kw in line_lower for skip_kw in ['www.', 'http', '.gov', '.in', '.com', 'uidal']):
			continue
		
		if re.match(r'^\d{12}$', line_clean.replace(' ', '').replace('-', '')):
			break
		
		if re.match(r'^\d+[/-]\d+[/-]\d+', line_clean):
			continue
		
		if re.match(r'^\d+$', line_clean.replace(' ', '').replace('-', '')):
			if len(line_clean.replace(' ', '').replace('-', '')) != 6:
				continue
		
		if not has_address_keyword:
			if len(line_clean) < 5:
				continue
			
			if re.match(r'^[^A-Za-z0-9\s]{3,}$', line_clean):
				continue
			
			if re.match(r'^[/\\\-_]{2,}', line_clean) or re.match(r'^[^A-Za-z]{4,}$', line_clean):
				continue
			
			address_keywords = ['house', 'road', 'street', 'lane', 'city', 'state', 'village', 'town', 'district', 'pincode', 'pin']
			line_has_address_keyword = any(kw in line_lower for kw in address_keywords)
			
			has_letters = bool(re.search(r'[A-Za-z]', line_clean))
			has_numbers = bool(re.search(r'\d', line_clean))
			has_common_punct = bool(re.search(r'[,.\-]', line_clean))
			
			if not line_has_address_keyword and not (has_letters and (has_numbers or has_common_punct)):
				continue
		
		line_cleaned = re.sub(email_pattern, '', line_clean, flags=re.IGNORECASE)
		line_cleaned = re.sub(url_pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
		
		care_of_patterns = [
			r'\b[CcSsDdWw]/[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
			r'\b[CcSsDdWw]\s*/\s*[Oo][\s:]*[A-Za-z\s,]+(?:,|$)',
			r'\b(?:C/O|S/O|D/O|W/O|c/o|s/o|d/o|w/o)[\s:]*[A-Za-z\s,]+(?:,|$)',
		]
		for pattern in care_of_patterns:
			line_cleaned = re.sub(pattern, '', line_cleaned, flags=re.IGNORECASE).strip()
		
		line_cleaned = re.sub(r'\b(addrens|address)[\s:]*', '', line_cleaned, flags=re.IGNORECASE).strip()
		
		if not line_cleaned or len(line_cleaned) < 2:
			continue
		
		if any(skip_kw in line_cleaned.lower() for skip_kw in ['www.', 'http', '.gov', '.in']):
			continue
		
		address_lines.append(line_cleaned)
		
		if len(address_lines) >= 15:
			break
	
	if address_lines and not pincode_found:
		for line in address_lines:
			pincode_match = re.search(r'\b(\d{6})\b', line)
			if pincode_match:
				pincode_found = True
				break
	
	return "\n".join(address_lines)


def extract_gender(text: str) -> str:
	"""Extract gender from Aadhaar card text."""
	if not text:
		return ""
	
	text_upper = text.upper()
	
	if 'MALE' in text_upper:
		return "Male"
	elif 'FEMALE' in text_upper:
		return "Female"
	
	if re.search(r'\bM\b', text_upper):
		if re.search(r'(MALE|GENDER|SEX)', text_upper):
			return "Male"
	
	if re.search(r'\bF\b', text_upper):
		if re.search(r'(FEMALE|GENDER|SEX)', text_upper):
			return "Female"
	
	return ""


def extract_aadhaar_number(front_text: str, back_text: str) -> str:
	"""Extract Aadhaar number from text."""
	aadhaar_patterns = [
		(r'\b(\d{4}[\s-]\d{4}[\s-]\d{4})\b', 0),
		(r'\b(\d{4}\s+\d{4}\s+\d{4})\b', 1),
		(r'\b(\d{12})\b', 2),
	]
	
	all_matches = []
	
	for text in [front_text, back_text]:
		if not text:
			continue
		
		for pattern, priority in aadhaar_patterns:
			try:
				matches = re.findall(pattern, text)
				if matches and len(matches) > 0:
					for match in matches:
						if isinstance(match, (tuple, list)):
							aadhaar = str(match[0]) if len(match) > 0 else str(match)
						else:
							aadhaar = str(match)
						
						aadhaar_clean = re.sub(r'[\s\-]', '', aadhaar)
						
						if len(aadhaar_clean) == 12 and aadhaar_clean.isdigit():
							all_matches.append((priority, aadhaar_clean))
			except (IndexError, TypeError, AttributeError):
				continue
	
	if all_matches:
		all_matches.sort(key=lambda x: x[0])
		return all_matches[0][1]
	
	return ""

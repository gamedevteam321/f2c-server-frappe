# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import base64
import numpy as np
from io import BytesIO
from PIL import Image
from typing import Dict, List, Tuple, Optional

# Aadhaar card aspect ratio (credit card format)
AADHAAR_ASPECT_RATIO = 1.586  # Width/Height ratio
ASPECT_RATIO_TOLERANCE = 0.30  # ±30% tolerance (more lenient)


def convert_to_native_types(obj):
	"""
	Convert numpy types and other non-JSON-serializable types to native Python types.
	
	Args:
		obj: Object that may contain numpy types
		
	Returns:
		Object with all numpy types converted to native Python types
	"""
	if isinstance(obj, np.bool_):
		return bool(obj)
	elif isinstance(obj, np.integer):
		return int(obj)
	elif isinstance(obj, np.floating):
		return float(obj)
	elif isinstance(obj, np.ndarray):
		return obj.tolist()
	elif isinstance(obj, dict):
		return {key: convert_to_native_types(value) for key, value in obj.items()}
	elif isinstance(obj, (list, tuple)):
		return [convert_to_native_types(item) for item in obj]
	else:
		return obj


# Validation endpoint disabled - using Gemini API for extraction
# @frappe.whitelist(allow_guest=False)
def validate_aadhaar_capture(image: str, side: str) -> Dict:
	"""
	Validate Aadhaar card image before OCR processing.
	
	Args:
		image: Base64 encoded image (with or without data URL prefix)
		side: 'front' or 'back'
		
	Returns:
		{
			"valid": bool,
			"score": float,  # 0-100 quality score
			"issues": List[str],
			"feedback": str,
			"details": {
				"aspect_ratio_valid": bool,
				"card_detected": bool,
				"card_in_frame": bool,
				"rois_detected": Dict,
				"blur_score": float,
				"lighting_score": float,
				"glare_detected": bool,
				"orientation_angle": float
			}
		}
	"""
	try:
		# Decode base64 image
		img_data = base64.b64decode(image.split(',')[-1] if ',' in image else image)
		img = Image.open(BytesIO(img_data))
		img = img.convert('RGB')
		
		# Convert to numpy array for OpenCV processing
		img_array = np.array(img)
		
		# Run all validation checks
		details = {}
		issues = []
		score_components = {}
		
		# 1. Card Detection & Aspect Ratio
		card_result = detect_card_and_validate_aspect_ratio(img_array)
		details.update(card_result)
		
		# More lenient scoring - give partial credit
		card_detected = card_result.get("card_detected", False)
		aspect_ratio_valid = card_result.get("aspect_ratio_valid", False)
		card_in_frame = card_result.get("card_in_frame", False)
		
		if not card_detected:
			issues.append("Card not detected in image")
		if not aspect_ratio_valid and card_detected:
			issues.append("Card aspect ratio doesn't match Aadhaar format")
		if not card_in_frame and card_detected:
			issues.append("Card doesn't fit completely within frame")
		
		# Give partial credit for card detection
		if card_detected:
			score_components["card_detection"] = 20  # Base points for detection
			if aspect_ratio_valid:
				score_components["card_detection"] += 5
			if card_in_frame:
				score_components["card_detection"] += 5
		else:
			score_components["card_detection"] = 0
		
		# 2. Image Quality Checks
		quality_result = validate_image_quality(img_array)
		details.update(quality_result)
		
		# Blur check (more lenient)
		blur_score = quality_result.get("blur_score", 0)
		if blur_score < 50:
			issues.append("Image is blurry - hold steady")
		# More lenient scoring - give credit for any reasonable blur score
		score_components["blur"] = min(blur_score / 50.0 * 20, 20)  # Max 20 points, normalized to 50 instead of 100
		
		# Lighting check (more lenient)
		lighting_score = quality_result.get("lighting_score", 0)
		if lighting_score < 30:
			issues.append("Image is too dark - improve lighting")
		elif lighting_score > 95:
			issues.append("Image is too bright - reduce lighting")
		# More lenient scoring - wider acceptable range
		score_components["lighting"] = (
			20 if 30 <= lighting_score <= 95
			else max(0, 20 - abs(lighting_score - 62.5) / 62.5 * 20)
		)
		
		# Glare check
		if quality_result.get("glare_detected"):
			issues.append("Glare detected - reduce reflections")
		score_components["glare"] = 10 if not quality_result.get("glare_detected") else 0
		
		# Orientation check (more lenient)
		orientation_angle = quality_result.get("orientation_angle", 0)
		if abs(orientation_angle) > 25:
			issues.append(f"Card is rotated ({orientation_angle:.1f}°) - align horizontally")
		# More lenient - accept up to 25 degrees
		score_components["orientation"] = (
			10 if abs(orientation_angle) <= 25
			else max(0, 10 - abs(orientation_angle) / 25.0 * 10)
		)
		
		# 3. ROI Detection
		roi_result = detect_rois(img_array, side)
		details["rois_detected"] = roi_result
		
		# Calculate ROI score based on detected regions (more lenient - ROI detection is optional)
		roi_score = 0
		if side == 'front':
			required_rois = ['name', 'dob', 'aadhaar_number']
			detected_count = sum(1 for roi in required_rois if roi_result.get(roi, False))
			# Give full score if at least one ROI is detected, partial otherwise
			if detected_count > 0:
				roi_score = 10  # Full score if any ROI detected
			else:
				roi_score = 5  # Partial score even if no ROI detected (OCR will handle it)
		else:  # back
			required_rois = ['address', 'aadhaar_number', 'qr_code']
			detected_count = sum(1 for roi in required_rois if roi_result.get(roi, False))
			# Give full score if at least one ROI is detected
			if detected_count > 0:
				roi_score = 10  # Full score if any ROI detected
			else:
				roi_score = 5  # Partial score even if no ROI detected
		
		score_components["rois"] = roi_score
		
		# Calculate total score
		total_score = sum(score_components.values())
		
		# Generate feedback message
		feedback = generate_feedback(issues, total_score, details)
		
		# Determine if valid (more lenient - score >= 60 and card detected)
		# Allow capture if card is detected and score is reasonable
		is_valid = (
			total_score >= 60 and
			card_result.get("card_detected") and
			blur_score >= 30 and  # Much lower blur threshold
			abs(orientation_angle) <= 30  # More lenient orientation
		)
		
		# Prepare response
		response = {
			"valid": bool(is_valid),
			"score": float(round(total_score, 2)),
			"issues": list(issues),
			"feedback": str(feedback),
			"details": details,
			"score_components": score_components
		}
		
		# Convert all numpy types to native Python types
		response = convert_to_native_types(response)
		
		return response
		
	except Exception as e:
		import traceback
		frappe.log_error(f"Aadhaar validation error: {str(e)}\n{traceback.format_exc()}", "Aadhaar Validation Error")
		return {
			"valid": False,
			"score": 0,
			"issues": [f"Validation error: {str(e)}"],
			"feedback": "Error validating image. Please try again.",
			"details": {},
			"score_components": {}
		}


def detect_card_and_validate_aspect_ratio(img_array: np.ndarray) -> Dict:
	"""
	Detect card in image and validate aspect ratio.
	
	Returns:
		{
			"card_detected": bool,
			"aspect_ratio_valid": bool,
			"card_in_frame": bool,
			"detected_aspect_ratio": float
		}
	"""
	try:
		import cv2
	except ImportError:
		return {
			"card_detected": False,
			"aspect_ratio_valid": False,
			"card_in_frame": False,
			"detected_aspect_ratio": 0.0
		}
	
	try:
		# Convert to grayscale
		if len(img_array.shape) == 3:
			gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
		else:
			gray = img_array
		
		# Apply Gaussian blur to reduce noise
		blurred = cv2.GaussianBlur(gray, (5, 5), 0)
		
		# Edge detection
		edges = cv2.Canny(blurred, 50, 150)
		
		# Find contours
		contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
		
		if not contours:
			return {
				"card_detected": False,
				"aspect_ratio_valid": False,
				"card_in_frame": False,
				"detected_aspect_ratio": 0.0
			}
		
		# Find largest contour that could be a card
		largest_contour = max(contours, key=cv2.contourArea)
		area = cv2.contourArea(largest_contour)
		
		# Filter out very small contours (more lenient - 5% instead of 10%)
		img_area = img_array.shape[0] * img_array.shape[1]
		if area < img_area * 0.05:  # Card should be at least 5% of image (more lenient)
			return {
				"card_detected": False,
				"aspect_ratio_valid": False,
				"card_in_frame": False,
				"detected_aspect_ratio": 0.0
			}
		
		# Get bounding rectangle directly (more lenient - don't require perfect 4 corners)
		x, y, w, h = cv2.boundingRect(largest_contour)
		
		# Calculate aspect ratio
		aspect_ratio = w / h if h > 0 else 0
		
		# Check if aspect ratio matches Aadhaar card (more lenient)
		aspect_ratio_valid = (
			abs(aspect_ratio - AADHAAR_ASPECT_RATIO) / AADHAAR_ASPECT_RATIO <= ASPECT_RATIO_TOLERANCE
		)
		
		# Check if card fits in frame (more lenient margin - 2% instead of 5%)
		margin = 0.02  # 2% margin (more lenient)
		card_in_frame = (
			x >= img_array.shape[1] * margin and
			y >= img_array.shape[0] * margin and
			(x + w) <= img_array.shape[1] * (1 - margin) and
			(y + h) <= img_array.shape[0] * (1 - margin)
		)
		
		# If we have a reasonable rectangle, consider card detected
		if w > 0 and h > 0 and area > img_area * 0.05:
			return {
				"card_detected": bool(True),
				"aspect_ratio_valid": bool(aspect_ratio_valid),
				"card_in_frame": bool(card_in_frame),
				"detected_aspect_ratio": float(round(aspect_ratio, 3))
			}
		else:
			return {
				"card_detected": False,
				"aspect_ratio_valid": False,
				"card_in_frame": False,
				"detected_aspect_ratio": 0.0
			}
			
	except Exception as e:
		frappe.log_error(f"Card detection error: {str(e)}", "Card Detection Error")
		return {
			"card_detected": False,
			"aspect_ratio_valid": False,
			"card_in_frame": False,
			"detected_aspect_ratio": 0.0
		}


def validate_image_quality(img_array: np.ndarray) -> Dict:
	"""
	Validate image quality: blur, lighting, glare, orientation.
	
	Returns:
		{
			"blur_score": float,
			"lighting_score": float,
			"glare_detected": bool,
			"orientation_angle": float
		}
	"""
	try:
		import cv2
	except ImportError:
		return {
			"blur_score": 0,
			"lighting_score": 0,
			"glare_detected": False,
			"orientation_angle": 0.0
		}
	
	try:
		# Convert to grayscale if needed
		if len(img_array.shape) == 3:
			gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
		else:
			gray = img_array
		
		# 1. Blur Detection using Laplacian variance
		laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
		blur_score = min(laplacian_var, 200)  # Cap at 200 for scoring
		
		# 2. Lighting Analysis
		mean_brightness = np.mean(gray)
		lighting_score = (mean_brightness / 255.0) * 100  # Convert to 0-100 scale
		
		# 3. Glare Detection
		# Look for high-intensity regions (specular highlights)
		_, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
		glare_pixels = np.sum(thresh > 0)
		total_pixels = gray.shape[0] * gray.shape[1]
		glare_ratio = glare_pixels / total_pixels
		glare_detected = glare_ratio > 0.05  # More than 5% of image is very bright
		
		# 4. Orientation Detection
		# Use Hough transform to detect lines and calculate angle
		edges = cv2.Canny(gray, 50, 150)
		lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
		
		orientation_angle = 0.0
		if lines is not None and len(lines) > 0:
			angles = []
			for rho, theta in lines[:min(20, len(lines))]:
				angle = (theta * 180 / np.pi) - 90
				if -45 <= angle <= 45:
					angles.append(angle)
			
			if angles:
				orientation_angle = np.median(angles)
		
		return {
			"blur_score": float(round(float(blur_score), 2)),
			"lighting_score": float(round(float(lighting_score), 2)),
			"glare_detected": bool(glare_detected),
			"orientation_angle": float(round(float(orientation_angle), 2))
		}
		
	except Exception as e:
		frappe.log_error(f"Image quality validation error: {str(e)}", "Quality Validation Error")
		return {
			"blur_score": 0,
			"lighting_score": 0,
			"glare_detected": False,
			"orientation_angle": 0.0
		}


def detect_rois(img_array: np.ndarray, side: str) -> Dict:
	"""
	Detect Regions of Interest (ROIs) in the image.
	
	Args:
		img_array: Image as numpy array
		side: 'front' or 'back'
		
	Returns:
		{
			"name": bool,
			"dob": bool,
			"gender": bool,
			"aadhaar_number": bool,
			"address": bool,
			"qr_code": bool
		}
	"""
	result = {
		"name": False,
		"dob": False,
		"gender": False,
		"aadhaar_number": False,
		"address": False,
		"qr_code": False
	}
	
	try:
		import cv2
		import pyzbar.pyzbar as pyzbar
	except ImportError:
		return result
	
	try:
		# Convert to grayscale
		if len(img_array.shape) == 3:
			gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
			img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
		else:
			gray = img_array
			img_bgr = cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)
		
		# QR Code detection (for back side)
		if side == 'back':
			qr_codes = pyzbar.decode(gray)
			result["qr_code"] = bool(len(qr_codes) > 0)
		
		# Use lightweight OCR pre-scan to detect text regions
		# This is a quick check, not full OCR
		try:
			from paddleocr import PaddleOCR
			
			# Use a lightweight OCR instance for quick ROI detection
			ocr = PaddleOCR(use_textline_orientation=False, lang='en', show_log=False)
			# Try new API first, fallback to old API
			try:
				ocr_result = ocr.predict(img_array)
			except Exception:
				# Fallback to old API
				ocr_result = ocr.ocr(img_array, cls=False)
			
			if ocr_result and len(ocr_result) > 0:
				# Extract all text from OCR result
				all_text = ""
				for page in ocr_result:
					if page:
						for line in page:
							if line and len(line) >= 2:
								text = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
								all_text += " " + text
				
				all_text_upper = all_text.upper()
				
				# Check for Aadhaar number (12 digits)
				aadhaar_pattern = r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'
				import re
				if re.search(aadhaar_pattern, all_text):
					result["aadhaar_number"] = bool(True)
				
				if side == 'front':
					# Check for name (usually contains letters, 2-5 words)
					if re.search(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+', all_text):
						result["name"] = bool(True)
					
					# Check for DOB (date pattern)
					if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b', all_text):
						result["dob"] = bool(True)
					
					# Check for gender
					if 'MALE' in all_text_upper or 'FEMALE' in all_text_upper:
						result["gender"] = bool(True)
				else:  # back
					# Check for address keywords
					address_keywords = ['ADDRESS', 'HOUSE', 'ROAD', 'STREET', 'CITY', 'STATE', 'PIN']
					if any(keyword in all_text_upper for keyword in address_keywords):
						result["address"] = bool(True)
					
					# Also check for Aadhaar number on back
					if re.search(aadhaar_pattern, all_text):
						result["aadhaar_number"] = bool(True)
						
		except Exception as ocr_error:
			# If OCR fails, we can't detect ROIs
			frappe.log_error(f"ROI detection OCR error: {str(ocr_error)}", "ROI Detection Error")
		
		return result
		
	except Exception as e:
		frappe.log_error(f"ROI detection error: {str(e)}", "ROI Detection Error")
		return result


def generate_feedback(issues: List[str], score: float, details: Dict) -> str:
	"""
	Generate user-friendly feedback message.
	
	Args:
		issues: List of issues found
		score: Quality score (0-100)
		details: Validation details
		
	Returns:
		Feedback message string
	"""
	if score >= 60 and not issues:
		return "Perfect! Card is aligned and ready to capture."
	elif score >= 60:
		return "Good quality! Ready to capture."
	elif score >= 40:
		return "Almost there! Make minor adjustments."
	elif score >= 30:
		return "Needs improvement. Please adjust the card."
	else:
		return "Poor quality. Please retake the photo."
	
	# Add specific guidance based on issues
	if issues:
		primary_issue = issues[0]
		if "blurry" in primary_issue.lower():
			return "Hold the camera steady and wait for focus."
		elif "dark" in primary_issue.lower() or "bright" in primary_issue.lower():
			return "Adjust lighting - ensure card is well lit but not too bright."
		elif "glare" in primary_issue.lower():
			return "Reduce glare by changing angle or lighting."
		elif "rotated" in primary_issue.lower():
			return "Align the card horizontally within the frame."
		elif "not detected" in primary_issue.lower():
			return "Ensure the entire card is visible within the frame."
	
	return "Please adjust the card position and try again."


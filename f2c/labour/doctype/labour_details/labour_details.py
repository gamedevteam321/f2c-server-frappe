import frappe
from frappe.model.document import Document
from frappe.utils.file_manager import get_files_path
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# ✅ global cache for speed
_OCR = None

class LabourDetails(Document):
    pass


def _get_ocr():
    global _OCR
    if _OCR is None:
        from paddleocr import PaddleOCR
        _OCR = PaddleOCR(use_angle_cls=False, lang="en")
    return _OCR


@frappe.whitelist()
def extract_text_from_image(file_url, photo_type="front"):
    try:
        # Import PaddleOCR
        try:
            from paddleocr import PaddleOCR  # keep import check
        except ImportError:
            return {"error": "PaddleOCR not installed. Run: pip install paddleocr paddlepaddle"}

        # Resolve path using File document lookup (most reliable method)
        try:
            # Try to get File document by file_url (works in main thread)
            file_list = frappe.get_all("File", filters={"file_url": file_url}, fields=["name"], limit=1)
            if file_list:
                file_doc = frappe.get_doc("File", file_list[0].name)
                file_path = file_doc.get_full_path()
            else:
                # Fallback: use get_files_path with URL decoding
                import urllib.parse
                if file_url.startswith("/private/files/"):
                    path_after_private = file_url.split("/private/files/", 1)[1]
                    path_parts = [urllib.parse.unquote(p) for p in path_after_private.split("/")]
                    file_path = get_files_path(*path_parts, is_private=1)
                elif file_url.startswith("/files/"):
                    path_after_files = file_url.split("/files/", 1)[1]
                    path_parts = [urllib.parse.unquote(p) for p in path_after_files.split("/")]
                    file_path = get_files_path(*path_parts)
                else:
                    file_path = get_files_path(urllib.parse.unquote(file_url))
        except Exception as path_error:
            import traceback
            error_trace = traceback.format_exc()
            print(f"❌ File path resolution error: {str(path_error)}")
            print(f"   URL: {file_url}")
            print(f"   Traceback: {error_trace[:500]}...")
            return {"error": f"Failed to resolve file path: {str(path_error)} (from URL: {file_url})"}

        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path} (from URL: {file_url})"}

        ocr = _get_ocr()

        result = ocr.ocr(file_path)

        # ---- Extract lines from BOTH formats ----
        rec_texts, rec_scores = [], []

        # ✅ NEW dict format (your current output)
        if isinstance(result, list) and result and isinstance(result[0], dict) and "rec_texts" in result[0]:
            rec_texts = result[0].get("rec_texts", []) or []
            rec_scores = result[0].get("rec_scores", []) or []
            rec_scores = [float(s) if s is not None else 0.0 for s in rec_scores]

        # ✅ OLD classic format fallback
        else:
            page = result[0] if isinstance(result, list) and result else result
            if isinstance(page, list):
                for line in page:
                    try:
                        txt, score = line[1]
                        rec_texts.append(txt)
                        rec_scores.append(float(score))
                    except Exception:
                        pass

        # Keep only confident lines
        clean_lines = []
        clean_scores = []
        for t, s in zip(rec_texts, rec_scores):
            t = " ".join((t or "").split()).strip()
            if not t:
                continue
            if s >= 0.50:
                clean_lines.append(t)
                clean_scores.append(s)

        raw_text = " ".join(clean_lines)

        extracted = parse_ocr_text(clean_lines, clean_scores, photo_type=photo_type)

        return {
            "success": True,
            "raw_text": raw_text,
            "extracted_data": extracted
        }

    except Exception as e:
        frappe.log_error(f"OCR extraction error: {str(e)}", "Labour OCR Error")
        return {"error": f"Error extracting text from image: {str(e)}"}


def _resolve_file_path(file_url):
    """
    Resolve file path from file_url in main thread context (has session access)
    Returns the file path or None if error
    """
    try:
        # Try to get File document by file_url (works in main thread)
        file_list = frappe.get_all("File", filters={"file_url": file_url}, fields=["name"], limit=1)
        if file_list:
            file_doc = frappe.get_doc("File", file_list[0].name)
            return file_doc.get_full_path()
        else:
            # Fallback: use get_files_path with URL decoding
            import urllib.parse
            if file_url.startswith("/private/files/"):
                path_after_private = file_url.split("/private/files/", 1)[1]
                path_parts = [urllib.parse.unquote(p) for p in path_after_private.split("/")]
                return get_files_path(*path_parts, is_private=1)
            elif file_url.startswith("/files/"):
                path_after_files = file_url.split("/files/", 1)[1]
                path_parts = [urllib.parse.unquote(p) for p in path_after_files.split("/")]
                return get_files_path(*path_parts)
            else:
                return get_files_path(urllib.parse.unquote(file_url))
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ File path resolution error: {str(e)}")
        print(f"   URL: {file_url}")
        print(f"   Traceback: {error_trace[:500]}...")
        return None


def _process_single_image(file_path, photo_type):
    """
    Helper function to process a single image for parallel execution
    Takes file_path directly (resolved in main thread)
    Returns the extracted data or error
    """
    try:
        if not file_path:
            return {"error": f"File path not provided for {photo_type}", "photo_type": photo_type}

        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}", "photo_type": photo_type}

        ocr = _get_ocr()
        result = ocr.ocr(file_path)

        # Extract lines from both formats
        rec_texts, rec_scores = [], []

        # NEW dict format
        if isinstance(result, list) and result and isinstance(result[0], dict) and "rec_texts" in result[0]:
            rec_texts = result[0].get("rec_texts", []) or []
            rec_scores = result[0].get("rec_scores", []) or []
            rec_scores = [float(s) if s is not None else 0.0 for s in rec_scores]

        # OLD classic format fallback
        else:
            page = result[0] if isinstance(result, list) and result else result
            if isinstance(page, list):
                for line in page:
                    try:
                        txt, score = line[1]
                        rec_texts.append(txt)
                        rec_scores.append(float(score))
                    except Exception:
                        pass

        # Keep only confident lines
        clean_lines = []
        clean_scores = []
        for t, s in zip(rec_texts, rec_scores):
            t = " ".join((t or "").split()).strip()
            if not t:
                continue
            if s >= 0.50:
                clean_lines.append(t)
                clean_scores.append(s)

        raw_text = " ".join(clean_lines)
        extracted = parse_ocr_text(clean_lines, clean_scores, photo_type=photo_type)
        
        # Debug: Log what was extracted from this image
        print(f"\n📸 {photo_type.upper()} Image OCR Results:")
        print(f"   - Name: '{extracted.get('labour_name', '')}'")
        print(f"   - DOB: '{extracted.get('dob', '')}'")
        print(f"   - Aadhaar: '{extracted.get('adhaar_number', '')}'")
        print(f"   - Gender: '{extracted.get('gender', '')}'")
        print(f"   - Address: '{extracted.get('address', '')[:50]}...' (truncated)" if extracted.get('address') else "   - Address: ''")
        print(f"   - Total lines extracted: {len(clean_lines)}")

        return {
            "success": True,
            "raw_text": raw_text,
            "extracted_data": extracted,
            "photo_type": photo_type
        }

    except Exception as e:
        frappe.log_error(f"OCR extraction error for {photo_type}: {str(e)}", "Labour OCR Error")
        return {"error": f"Error extracting text from {photo_type} image: {str(e)}", "photo_type": photo_type}


@frappe.whitelist()
def extract_text_from_images(file_url_front, file_url_back=None):
    """
    Extract text from both front and back images in parallel
    Merges results intelligently: front typically has name/DOB, back has address
    
    Args:
        file_url_front: URL to the front side image
        file_url_back: URL to the back side image (optional)
        
    Returns:
        dict: Combined extracted data from both images
    """
    try:
        # Import check
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            return {"error": "PaddleOCR not installed. Run: pip install paddleocr paddlepaddle"}

        # Resolve file paths in main thread (has session context)
        file_path_front = _resolve_file_path(file_url_front)
        file_path_back = _resolve_file_path(file_url_back) if file_url_back else None
        
        if not file_path_front:
            return {"error": f"Failed to resolve file path for front image: {file_url_front}"}
        if file_url_back and not file_path_back:
            return {"error": f"Failed to resolve file path for back image: {file_url_back}"}
        
        # Process images in parallel using ThreadPoolExecutor
        tasks = [("front", file_path_front)]
        if file_path_back:
            tasks.append(("back", file_path_back))

        results = {}
        
        # Use ThreadPoolExecutor to process images in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit all tasks with file_paths (already resolved)
            future_to_type = {
                executor.submit(_process_single_image, file_path, photo_type): photo_type
                for photo_type, file_path in tasks
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_type):
                photo_type = future_to_type[future]
                try:
                    result = future.result()
                    results[photo_type] = result
                    # Debug: Log processing status
                    if result.get("error"):
                        print(f"\n⚠️ {photo_type.upper()} image processing failed: {result.get('error')}")
                    else:
                        print(f"✅ {photo_type.upper()} image processed successfully")
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    print(f"\n❌ Exception while processing {photo_type.upper()} image: {str(e)}")
                    print(f"   Traceback: {error_trace[:300]}...")
                    try:
                        frappe.log_error(f"Error processing {photo_type} image: {str(e)}\n{error_trace}", "Labour OCR Error")
                    except:
                        pass
                    results[photo_type] = {
                        "error": f"Error processing {photo_type} image: {str(e)}",
                        "photo_type": photo_type
                    }

        # Merge results intelligently
        merged_data = {
            "labour_name": "",
            "dob": "",
            "address": "",
            "gender": "",
            "adhaar_number": ""
        }

        # Get extracted data from results, handling both success and error cases
        front_result = results.get("front", {})
        back_result = results.get("back", {}) if file_url_back else {}
        
        # Extract data only if processing was successful (no error)
        front_data = front_result.get("extracted_data", {}) if not front_result.get("error") else {}
        back_data = back_result.get("extracted_data", {}) if not back_result.get("error") else {}

        # Prefer front for name, DOB, Aadhaar (usually more accurate)
        merged_data["labour_name"] = (front_data.get("labour_name", "") or back_data.get("labour_name", "") or "").strip()
        merged_data["dob"] = (front_data.get("dob", "") or back_data.get("dob", "") or "").strip()
        merged_data["adhaar_number"] = (front_data.get("adhaar_number", "") or back_data.get("adhaar_number", "") or "").strip()
        merged_data["gender"] = (front_data.get("gender", "") or back_data.get("gender", "") or "").strip()

        # Prefer back for address (usually more complete)
        merged_data["address"] = (back_data.get("address", "") or front_data.get("address", "") or "").strip()
        
        # Debug: Show what was merged
        print(f"\n🔄 MERGED RESULTS:")
        print(f"   - Name: '{merged_data['labour_name']}' (front: '{front_data.get('labour_name', '')}', back: '{back_data.get('labour_name', '')}')")
        print(f"   - DOB: '{merged_data['dob']}' (front: '{front_data.get('dob', '')}', back: '{back_data.get('dob', '')}')")
        print(f"   - Aadhaar: '{merged_data['adhaar_number']}' (front: '{front_data.get('adhaar_number', '')}', back: '{back_data.get('adhaar_number', '')}')")
        print(f"   - Gender: '{merged_data['gender']}' (front: '{front_data.get('gender', '')}', back: '{back_data.get('gender', '')}')")
        print(f"   - Address: '{merged_data['address'][:80]}...' (front: {bool(front_data.get('address'))}, back: {bool(back_data.get('address'))})")

        # Combine raw text from both images
        raw_texts = []
        if results.get("front", {}).get("raw_text"):
            raw_texts.append(f"Front: {results['front']['raw_text']}")
        if results.get("back", {}).get("raw_text"):
            raw_texts.append(f"Back: {results['back']['raw_text']}")
        combined_raw_text = " | ".join(raw_texts)

        # Check for errors
        errors = []
        if results.get("front", {}).get("error"):
            errors.append(results["front"]["error"])
        if results.get("back", {}).get("error"):
            errors.append(results["back"]["error"])

        # Final debug output
        print(f"\n📤 RETURNING TO FRONTEND:")
        print(f"   - Success: True")
        print(f"   - Merged data: {merged_data}")
        print(f"   - Errors: {errors if errors else 'None'}")
        
        return {
            "success": True,
            "raw_text": combined_raw_text,
            "extracted_data": merged_data,
            "errors": errors if errors else None
        }

    except Exception as e:
        frappe.log_error(f"Batch OCR extraction error: {str(e)}", "Labour OCR Error")
        return {"error": f"Error extracting text from images: {str(e)}"}


def parse_ocr_text(lines, scores, photo_type="front"):
    """
    ✅ IMPORTANT CHANGE:
    We parse NAME from OCR lines (not full_text).
    That avoids junk like: 91890 8018 getting selected as name.
    """

    extracted = {
        "labour_name": "",
        "dob": "",
        "address": "",
        "gender": "",
        "adhaar_number": ""
    }

    # join for regex-based fields like Aadhaar/DOB
    full_text = " ".join(lines)
    upper_text = full_text.upper()

    # ---------------- Aadhaar ----------------
    m = re.search(r"\b(\d{4}\s?\d{4}\s?\d{4})\b", full_text)
    if m:
        extracted["adhaar_number"] = re.sub(r"\s+", "", m.group(1))

    # ---------------- DOB ----------------
    # handle OCR confusion: DOB / D0B / D0 / DATE OF BIRTH:
    try:
        dob_match = re.search(r"(DOB|D0B|D0|DATE\s+OF\s+BIRTH)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})", upper_text)
        if dob_match and dob_match.groups() and len(dob_match.groups()) >= 2:
            d = dob_match.group(2).replace("-", "/")
            parts = d.split("/")
            if len(parts) == 3:
                day, month, year = parts[0].zfill(2), parts[1].zfill(2), parts[2]
                # Validate date
                if 1900 <= int(year) <= 2100 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                    extracted["dob"] = f"{year}-{month}-{day}"
    except (IndexError, ValueError, AttributeError):
        pass
    
    # fallback: any DD/MM/YYYY or D/M/YYYY pattern
    if not extracted["dob"]:
        try:
            m2 = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", full_text)
            if m2 and m2.groups():
                d = m2.group(1).replace("-", "/")
                parts = d.split("/")
                if len(parts) == 3:
                    day, month, year = parts[0].zfill(2), parts[1].zfill(2), parts[2]
                    # Validate date
                    if 1900 <= int(year) <= 2100 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                        extracted["dob"] = f"{year}-{month}-{day}"
        except (IndexError, ValueError, AttributeError):
            pass

    # ---------------- Gender ----------------
    if re.search(r"\bMALE\b", upper_text):
        extracted["gender"] = "Male"
    elif re.search(r"\bFEMALE\b", upper_text):
        extracted["gender"] = "Female"

    # ---------------- NAME (IMPROVED) ----------------
    # pick best line: no digits, 1-5 words, not keywords, highest score
    banned = re.compile(r"\b(GOVERNMENT|INDIA|UIDAI|AADHAAR|ADDRESS|DOB|D0B|MALE|FEMALE|DATE|BIRTH|YEAR|FATHER|MOTHER|HUSBAND|WIFE|S/O|D/O|W/O)\b", re.I)

    candidates = []
    for idx, (t, s) in enumerate(zip(lines, scores)):
        t_clean = t.strip()
        if not t_clean or len(t_clean) < 3:
            continue
        
        # Skip lines with digits (likely dates, numbers, etc.)
        if any(ch.isdigit() for ch in t_clean):
            continue
        
        # Skip lines with special characters that indicate it's not a name
        if "/" in t_clean or ":" in t_clean or "|" in t_clean:
            continue
        
        # Skip banned keywords
        if banned.search(t_clean):
            continue

        # Allow 1-5 words (some names can be single word or longer)
        words = t_clean.split()
        if 1 <= len(words) <= 5:
            # Check if all words are alphabetic (allow apostrophes and hyphens)
            if all(re.match(r"^[A-Za-z'-]+$", w) for w in words):
                # Prefer lines that appear early in the text (usually name is at top)
                position_bonus = max(0, (len(lines) - idx) / len(lines)) * 0.1
                final_score = s + position_bonus
                candidates.append((final_score, t_clean))

    if candidates:
        candidates.sort(reverse=True, key=lambda x: x[0])
        extracted["labour_name"] = candidates[0][1]

    # ---------------- Address (IMPROVED & CORRECTED) ----------------
    # Collect address lines more comprehensively, handling Aadhaar card format
    # IMPORTANT: Exclude name from address - name is usually 2-4 words, address is longer with keywords
    
    # First, identify the name line to exclude it from address
    name_line = extracted.get("labour_name", "").upper()
    
    addr_idx = -1
    for i, t in enumerate(lines):
        if re.search(r"\b(ADDRESS|ADDR)\b", t.upper()):
            addr_idx = i
            break

    if addr_idx != -1:
        addr_parts = []
        # Collect more lines (up to 12) for complete address
        for j in range(addr_idx + 1, min(addr_idx + 12, len(lines))):  # Start from next line after "ADDRESS"
            txt = lines[j].strip()
            if not txt:
                continue
            
            # Remove "ADDRESS" label if present in this line
            txt = re.sub(r"\b(ADDRESS|ADDR)\b[:\-]?\s*", "", txt, flags=re.I).strip()
            if not txt:
                continue
            
            # EXCLUDE: Skip if this line matches the name (exact match or very similar)
            txt_upper = txt.upper()
            if name_line and (txt_upper == name_line or txt_upper in name_line or name_line in txt_upper):
                continue
            
            # EXCLUDE: Skip if it looks like a name (2-4 words, all alphabetic, no numbers)
            words = txt.split()
            if 2 <= len(words) <= 4:
                if all(re.match(r"^[A-Za-z'-]+$", w) for w in words) and not any(ch.isdigit() for ch in txt):
                    # This looks like a name, skip it
                    continue
            
            # Stop if we hit Aadhaar number (12 digits) - this is usually after address
            if re.search(r"\b\d{4}\s?\d{4}\s?\d{4}\b", txt):
                break
            
            # Stop if we hit other fields (but allow PIN codes which are part of address)
            if re.search(r"\b(DOB|D0B|DATE\s+OF\s+BIRTH|MALE|FEMALE|GOVERNMENT\s+OF\s+INDIA|UIDAI)\b", txt.upper()):
                break
            
            # Skip if it's just a number without context (likely Aadhaar or unrelated)
            if re.match(r"^\d+$", txt):
                continue
            
            # Include the line if it looks like address content
            # Address lines typically have text, may have numbers (PIN codes, house numbers), 
            # and contain address keywords
            addr_keywords = ["C/O", "AT-", "PO-", "VILLAGE", "TOWN", "DISTRICT", "STATE", "PIN", "POST", "ROAD", "STREET", "LANE", "ODISHA", "MAHARASHTRA", "GUJARAT", "KARNATAKA", "TAMIL", "KERALA", "WEST", "EAST", "NORTH", "SOUTH"]
            has_address_keyword = any(kw in txt_upper for kw in addr_keywords)
            has_text_and_numbers = bool(re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", txt))
            is_textual = bool(re.search(r"[A-Za-z]{2,}", txt))  # Has at least 2 consecutive letters
            
            # Address should have at least one of: address keyword, text with numbers, or be longer text
            if has_address_keyword or (has_text_and_numbers and is_textual) or (is_textual and len(words) >= 3):
                addr_parts.append(txt)
        
        if addr_parts:
            # Clean up: remove duplicate spaces, join with single space
            cleaned_address = " ".join(addr_parts)
            # Remove extra whitespace
            cleaned_address = re.sub(r"\s+", " ", cleaned_address).strip()
            extracted["address"] = cleaned_address[:400]  # Increased limit for complete addresses
    else:
        # Fallback: if no "ADDRESS" label, try to find address-like content
        # Look for lines with common address keywords or patterns
        # IMPORTANT: Exclude name from address
        name_line = extracted.get("labour_name", "").upper()
        
        addr_keywords = ["C/O", "AT-", "PO-", "VILLAGE", "TOWN", "DISTRICT", "STATE", "PIN", "POST", "ROAD", "STREET", "ODISHA", "MAHARASHTRA", "GUJARAT", "KARNATAKA", "TAMIL", "KERALA"]
        addr_parts = []
        found_address_start = False
        
        for t in lines:
            t_clean = t.strip()
            t_upper = t_clean.upper()
            
            # EXCLUDE: Skip if this line matches the name
            if name_line and (t_upper == name_line or t_upper in name_line or name_line in t_upper):
                continue
            
            # EXCLUDE: Skip if it looks like a name (2-4 words, all alphabetic, no numbers)
            words = t_clean.split()
            if 2 <= len(words) <= 4:
                if all(re.match(r"^[A-Za-z'-]+$", w) for w in words) and not any(ch.isdigit() for ch in t_clean):
                    # This looks like a name, skip it
                    continue
            
            # Skip if it's clearly not address (has DOB, Aadhaar, etc.)
            if re.search(r"\b(DOB|D0B|DATE\s+OF\s+BIRTH|AADHAAR|MALE|FEMALE|GOVERNMENT\s+OF\s+INDIA|UIDAI)\b", t_upper):
                if found_address_start:
                    break  # Stop if we've started collecting address and hit a non-address field
                continue
            
            # Check if this looks like address content
            has_keyword = any(kw in t_upper for kw in addr_keywords)
            has_text_and_numbers = bool(re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", t_clean))
            is_textual = bool(re.search(r"[A-Za-z]{2,}", t_clean))
            
            # Skip pure numbers (likely Aadhaar or unrelated)
            if re.match(r"^\d+$", t_clean):
                continue
            
            # Address should have keywords, or text with numbers, or be longer text (3+ words)
            if has_keyword or (has_text_and_numbers and is_textual) or (is_textual and len(words) >= 3):
                addr_parts.append(t_clean)
                found_address_start = True
        
        if addr_parts:
            # Clean up: remove duplicate spaces, join with single space
            cleaned_address = " ".join(addr_parts)
            cleaned_address = re.sub(r"\s+", " ", cleaned_address).strip()
            extracted["address"] = cleaned_address[:400]

    return extracted

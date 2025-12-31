import sys
import json
import os
import logging
import warnings

# Suppress logs
os.environ['GLOG_minloglevel'] = '3'
warnings.filterwarnings("ignore")

def run_ocr(front_path, back_path=None):
    result = {
        "success": False,
        "front_text": [],
        "back_text": [],
        "error": None
    }
    
    try:
        from paddleocr import PaddleOCR
        import numpy as np
        from PIL import Image, ImageOps

        # Initialize PaddleOCR
        ocr = PaddleOCR(
            use_angle_cls=True,
            lang='en'
        )

        def process_single_image(img_path):
            if not img_path or not os.path.exists(img_path):
                return [], f"Image path does not exist: {img_path}"
            
            try:
                # Load and preprocess
                img = Image.open(img_path).convert('RGB')
                
                # Resize if small (same logic as before)
                w, h = img.size
                if w < 300:
                    scale = 300 / w
                    img = img.resize((300, int(h * scale)), Image.Resampling.LANCZOS)
                
                # Apply auto contrast for better OCR
                img_gray = img.convert('L')
                img_gray = ImageOps.autocontrast(img_gray, cutoff=2)
                img = img_gray.convert('RGB')
                
                img_array = np.array(img)
                
                # Perform OCR - try with numpy array first, fallback to image path
                try:
                    ocr_res = ocr.ocr(img_array)
                except Exception as e1:
                    # If numpy array fails, try with image path directly
                    try:
                        ocr_res = ocr.ocr(img_path)
                    except Exception as e2:
                        # If both fail, raise the first error
                        raise Exception(f"OCR failed with numpy array: {str(e1)}. Also failed with image path: {str(e2)}")
                
                # DEBUG: Log OCR result structure
                debug_info = {
                    "ocr_res_type": str(type(ocr_res)),
                    "ocr_res_is_none": ocr_res is None,
                    "ocr_res_is_list": isinstance(ocr_res, list),
                    "ocr_res_length": len(ocr_res) if isinstance(ocr_res, list) else "N/A",
                }
                if ocr_res and isinstance(ocr_res, list) and len(ocr_res) > 0:
                    first_elem = ocr_res[0]
                    debug_info["first_page_type"] = str(type(first_elem))
                    debug_info["first_page_is_none"] = first_elem is None
                    debug_info["first_page_is_list"] = isinstance(first_elem, list)
                    debug_info["first_page_repr"] = repr(first_elem)[:200]  # Show actual content
                    if isinstance(first_elem, list):
                        debug_info["first_page_length"] = len(first_elem)
                        if len(first_elem) > 0:
                            debug_info["first_box_type"] = str(type(first_elem[0]))
                            debug_info["first_box_repr"] = repr(first_elem[0])[:200]
                            if isinstance(first_elem[0], list) and len(first_elem[0]) >= 2:
                                debug_info["text_info_type"] = str(type(first_elem[0][1]))
                                debug_info["text_info_value"] = str(first_elem[0][1])[:100] if first_elem[0][1] else "None"
                    else:
                        debug_info["first_page_length"] = "N/A (not a list)"
                
                # Extract text
                texts = []
                
                # Handle PaddleOCR result format: [[[bbox, (text, confidence)], ...]]
                if ocr_res is None:
                    debug_info["extraction_note"] = "OCR result is None"
                elif isinstance(ocr_res, list):
                    if len(ocr_res) == 0:
                        debug_info["extraction_note"] = "OCR result is empty list"
                    else:
                        # Standard format: [[[bbox, (text, confidence)], ...]]
                        # But PaddleOCR might return different structures
                        for page_idx, page in enumerate(ocr_res):
                            if page is None:
                                debug_info[f"page_{page_idx}_note"] = "Page is None"
                                continue
                            
                            # Check if page is a list (standard format)
                            if isinstance(page, list):
                                if len(page) == 0:
                                    debug_info[f"page_{page_idx}_note"] = "Page is empty list"
                                    continue
                                
                                # Process boxes in the page
                                for box_idx, box in enumerate(page):
                                    if box is None:
                                        continue
                                    if isinstance(box, list) and len(box) >= 2:
                                        text_info = box[1]
                                        if isinstance(text_info, (list, tuple)) and len(text_info) > 0:
                                            text = text_info[0]
                                            if text and str(text).strip():
                                                texts.append(str(text).strip())
                                        elif isinstance(text_info, str):
                                            # Sometimes text_info is directly a string
                                            if text_info.strip():
                                                texts.append(text_info.strip())
                                    elif isinstance(box, str):
                                        # Sometimes box is directly text
                                        if box.strip():
                                            texts.append(box.strip())
                            elif isinstance(page, str):
                                # Sometimes page is directly text
                                if page.strip():
                                    texts.append(page.strip())
                            else:
                                # Page is not a list or string - might be a dict or other structure
                                debug_info[f"page_{page_idx}_unexpected_type"] = str(type(page))
                                debug_info[f"page_{page_idx}_repr"] = repr(page)[:200]
                                
                                # Try to extract text from dict-like structures
                                if isinstance(page, dict):
                                    # Check for common dict keys that might contain text
                                    for key in ['text', 'rec_text', 'rec_texts', 'texts']:
                                        if key in page:
                                            value = page[key]
                                            if isinstance(value, str) and value.strip():
                                                texts.append(value.strip())
                                            elif isinstance(value, list):
                                                for item in value:
                                                    if isinstance(item, str) and item.strip():
                                                        texts.append(item.strip())
                elif isinstance(ocr_res, str):
                    # Sometimes OCR returns text directly
                    if ocr_res.strip():
                        texts.append(ocr_res.strip())
                else:
                    debug_info["extraction_note"] = f"Unexpected OCR result type: {type(ocr_res)}"
                
                debug_info["extracted_texts_count"] = len(texts)
                debug_info["extracted_texts_preview"] = texts[:5] if texts else []
                
                # Include debug info in error if no texts found
                if not texts:
                    debug_msg = f"DEBUG: {json.dumps(debug_info)}"
                    return [], f"No text extracted. {debug_msg}"
                
                return texts, None
            except Exception as e:
                # Return empty list and error message with traceback
                import traceback
                return [], f"{str(e)}\n{traceback.format_exc()}"

        # Process Front
        front_texts, front_error = process_single_image(front_path)
        result["front_text"] = front_texts
        if front_error:
            result["error"] = f"Failed to process front image: {front_error}"
            result["front_debug"] = front_error  # Include debug info
        
        # Process Back
        if back_path:
            back_texts, back_error = process_single_image(back_path)
            result["back_text"] = back_texts
            if back_error:
                if result.get("error"):
                    result["error"] += f"; Failed to process back image: {back_error}"
                else:
                    result["error"] = f"Failed to process back image: {back_error}"
                result["back_debug"] = back_error  # Include debug info
        
        # Set success to True even if no text found (as long as no errors occurred)
        # This allows the main script to handle empty results gracefully
        if not result.get("error"):
            result["success"] = True
        elif "No text extracted" in str(result.get("error", "")):
            # If only issue is no text found, still mark as success
            # but include the debug info
            result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()

    # Print ONLY the JSON to stdout
    print(json.dumps(result))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "No image path provided"}))
        sys.exit(1)
    
    front = sys.argv[1]
    back = sys.argv[2] if len(sys.argv) > 2 else None
    
    run_ocr(front, back)

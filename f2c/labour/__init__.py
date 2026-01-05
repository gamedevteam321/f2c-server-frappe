# Initialize OCR on module import (server start)
# This runs when the module is first imported during server startup
def initialize_ocr_on_startup():
    """Initialize OCR when module is imported (server startup)"""
    try:
        from f2c.labour.doctype.labour_details.labour_details import _get_ocr
        _get_ocr()  # This will initialize OCR on first call
        print("✅ OCR (PP-OCRv3 Mobile) initialized on server startup - ready for fast processing!")
    except Exception as e:
        print(f"⚠️ OCR initialization on startup failed (will initialize on first use): {str(e)}")
        # Don't fail server startup if OCR init fails - it will initialize on first use

# Initialize OCR on server start (runs when module is imported)
# This ensures models are loaded once at startup, not on first request
try:
    initialize_ocr_on_startup()
except Exception as e:
    # Silently fail - OCR will initialize on first use
    pass


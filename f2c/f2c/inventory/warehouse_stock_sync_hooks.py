"""
Hooks to automatically trigger warehouse stock sync when stock quantities or asset locations change.
"""
import frappe
from frappe.utils import now_datetime

# Global state for debouncing
_last_sync_time = None
_sync_lock = False


def trigger_warehouse_stock_sync(doc=None, method=None):
	"""
	Enqueue warehouse stock sync as background job with debouncing.
	
	This function prevents multiple simultaneous syncs and debounces rapid changes
	to avoid excessive background job creation.
	
	Args:
		doc: Document object (optional, provided by Frappe hooks)
		method: Method name (optional, provided by Frappe hooks)
	"""
	global _last_sync_time, _sync_lock
	
	# Debounce: Don't sync more than once per 5 seconds
	if _last_sync_time:
		time_diff = (now_datetime() - _last_sync_time).total_seconds()
		if time_diff < 5:
			return
	
	# Prevent concurrent sync attempts
	if _sync_lock:
		return
	
	_sync_lock = True
	_last_sync_time = now_datetime()
	
	try:
		frappe.enqueue(
			"f2c.inventory.doctype.warehouse_stock.warehouse_stock.sync_warehouse_stock_async",
			queue="long",
			refresh_existing=1,
			job_name="auto_sync_warehouse_stock",
			timeout=3600,
		)
	finally:
		_sync_lock = False


def trigger_warehouse_stock_sync_on_asset_update(doc, method):
	"""
	Trigger warehouse stock sync when Asset location changes.
	
	Only triggers sync if the location field actually changed to avoid
	unnecessary syncs on other asset updates.
	"""
	# Check if location field changed
	if doc.has_value_changed("location"):
		trigger_warehouse_stock_sync()

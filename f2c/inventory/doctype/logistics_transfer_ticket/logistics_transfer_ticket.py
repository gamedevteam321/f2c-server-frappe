import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from erpnext.stock.utils import get_stock_balance


class LogisticsTransferTicket(Document):
	def validate(self):
		"""Calculate available balances for stock items and assets"""
		self._validate_transport_vehicle()
		self._calculate_available_balances()
		self._calculate_asset_availability()
		self._check_low_stock_and_suggest_request()

	def _validate_transport_vehicle(self):
		if not getattr(self, "transport_vehicle", None):
			return
		mtype = frappe.db.get_value("Machinery", self.transport_vehicle, "machinery_type")
		if mtype not in ("Vehicle", "Tractor"):
			frappe.throw(_("Transport vehicle must be Machinery with type Vehicle or Tractor"))
	
	def _calculate_available_balances(self):
		"""Calculate and set available balance for each stock item in the source warehouse"""
		if not self.from_warehouse or not self.stock_items:
			return
		
		for row in self.stock_items:
			if not row.item_code:
				row.available_balance = None
				continue
			
			# Check if item is stock item
			try:
				is_stock_item = frappe.db.get_value("Item", row.item_code, "is_stock_item")
			except Exception:
				row.available_balance = "Not Available"
				continue
			
			if is_stock_item:
				try:
					balance = get_stock_balance(row.item_code, self.from_warehouse)
					if balance is not None and balance > 0:
						row.available_balance = str(balance)
					else:
						row.available_balance = "0"
				except Exception:
					row.available_balance = "Not Available"
			else:
				row.available_balance = "Not Available"
	
	def _calculate_asset_availability(self):
		"""Calculate and set available balance for each asset in the source warehouse"""
		if not self.from_warehouse or not self.asset_items:
			return
		
		# Get warehouse location
		try:
			from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
			location_result = get_location_for_warehouse(self.from_warehouse)
			warehouse_location = location_result.get("location") if location_result else None
		except Exception:
			warehouse_location = None
		
		for row in self.asset_items:
			if not row.asset:
				row.available_balance = None
				continue
			
			# Check if asset is at the warehouse location
			try:
				asset_location = frappe.db.get_value("Asset", row.asset, "location")
				if warehouse_location and asset_location == warehouse_location:
					row.available_balance = "Available"
				else:
					row.available_balance = "Not Available"
			except Exception:
				row.available_balance = "Not Available"
	
	def _check_low_stock_and_suggest_request(self):
		"""Check for low stock items and set flag for suggesting Material Request"""
		if not self.from_warehouse or not self.stock_items:
			return
		
		low_stock_items = []
		for row in self.stock_items:
			if not row.item_code:
				continue
			
			available_balance = row.get("available_balance")
			if available_balance:
				try:
					balance_float = flt(available_balance)
					if balance_float <= 0:
						low_stock_items.append({
							"item_code": row.item_code,
							"item_name": row.get("item_name") or row.item_code,
							"qty": row.qty,
							"available_balance": available_balance
						})
				except (ValueError, TypeError):
					# If it's a string like "Not Available"
					if str(available_balance).lower() in ["not available", "0", "0.0"]:
						low_stock_items.append({
							"item_code": row.item_code,
							"item_name": row.get("item_name") or row.item_code,
							"qty": row.qty,
							"available_balance": available_balance
						})
			else:
				# No balance available, consider it low stock
				low_stock_items.append({
					"item_code": row.item_code,
					"item_name": row.get("item_name") or row.item_code,
					"qty": row.qty,
					"available_balance": "Unknown"
				})
		
		# Set flag for client-side to show warning
		if low_stock_items:
			self.flags.has_low_stock = True
			self.flags.low_stock_items = low_stock_items



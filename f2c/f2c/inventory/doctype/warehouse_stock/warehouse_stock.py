import frappe
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import flt, now_datetime
from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse, ASSET_DOCSTATUS_NOT_CANCELLED


class WarehouseStock(Document):
	def validate(self):
		if not self.warehouse:
			return

		company = frappe.db.get_value("Warehouse", self.warehouse, "company")
		if company:
			self.company = company


def _get_ws_docname_for_warehouse(warehouse: str) -> str | None:
	"""
	Return an existing Warehouse Stock docname for a given warehouse.
	Preferred docname is the warehouse itself (autoname=field:warehouse), but we also
	handle older/out-of-sync records where doc.name != doc.warehouse.
	"""
	if not warehouse:
		return None
	if frappe.db.exists("Warehouse Stock", warehouse):
		return warehouse
	return frappe.db.get_value("Warehouse Stock", {"warehouse": warehouse}, "name", order_by="modified desc")


@frappe.whitelist()
def refresh_from_ledger(warehouse_stock_name: str) -> str:
	"""
	Rebuild the child table rows by computing net qty per item from Stock Ledger Entry.
	"""
	# Note: this method can be called in quick succession from the frontend (e.g. auto-refresh + modal open).
	# To avoid TimestampMismatchError race conditions, we compute the snapshot once and then save with a reload+retry.
	doc0 = frappe.get_doc("Warehouse Stock", warehouse_stock_name)
	if not doc0.warehouse:
		frappe.throw("Please set Warehouse before refreshing.")

	company = frappe.db.get_value("Warehouse", doc0.warehouse, "company")
	if not company:
		frappe.throw("Warehouse has no Company set; cannot refresh.")

	warehouse = doc0.warehouse

	sle = frappe.qb.DocType("Stock Ledger Entry")
	rows = (
		frappe.qb.from_(sle)
		.select(sle.item_code, Sum(sle.actual_qty).as_("qty"))
		.where(
			(sle.warehouse == warehouse)
			& (sle.company == company)
			& (sle.is_cancelled == 0)
			& (sle.item_code.isnotnull())
		)
		.groupby(sle.item_code)
	).run(as_dict=True)

	item_codes = [r.get("item_code") for r in rows if r.get("item_code")]
	item_meta = {}
	if item_codes:
		for it in frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "item_name", "item_group", "stock_uom"],
		):
			item_meta[it["name"]] = it

	# Build snapshot rows (so we can retry save without re-querying SLE)
	snapshot_items: list[dict] = []
	for r in rows:
		item_code = r.get("item_code")
		qty = flt(r.get("qty") or 0, 3)
		if not item_code or abs(qty) < 0.0001:
			continue
		meta = item_meta.get(item_code) or {}
		snapshot_items.append(
			{
				"item_code": item_code,
				"item_name": meta.get("item_name"),
				"category": meta.get("item_group"),
				"qty": qty,
				"stock_uom": meta.get("stock_uom"),
			}
		)

	# Also get assets for the warehouse
	snapshot_assets = _get_assets_for_warehouse(warehouse)

	for attempt in range(2):
		doc = frappe.get_doc("Warehouse Stock", warehouse_stock_name)
		doc.company = company
		doc.set("items", [])
		for it in snapshot_items:
			doc.append("items", it)
		# Also refresh assets when refreshing items
		doc.set("assets", [])
		for asset in snapshot_assets:
			doc.append("assets", asset)
		doc.last_refreshed_on = now_datetime()
		try:
			doc.save(ignore_permissions=True)
			return doc.name
		except frappe.TimestampMismatchError:
			if attempt == 0:
				continue
			raise

	return warehouse_stock_name


def _get_assets_for_warehouse(warehouse: str) -> list[dict]:
	"""
	Get assets for a warehouse by querying Asset doctype filtered by warehouse location.
	Returns a list of asset dictionaries ready to be appended to the assets child table.
	"""
	if not warehouse:
		return []

	# Get location for warehouse
	location_result = get_location_for_warehouse(warehouse)
	location = location_result.get("location") if location_result else None

	if not location:
		return []

	# Query assets by location (draft and submitted, not cancelled); ignore_permissions for inventory view
	assets = frappe.get_all(
		"Asset",
		fields=["name", "asset_name", "status", "location", "asset_category"],
		filters=[["location", "=", location], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
		limit=1000,
		ignore_permissions=True,
	)

	# Build snapshot rows
	snapshot_assets: list[dict] = []
	for asset in assets:
		snapshot_assets.append(
			{
				"asset": asset.get("name"),
				"asset_name": asset.get("asset_name"),
				"status": asset.get("status"),
				"location": asset.get("location"),
				"asset_category": asset.get("asset_category"),
			}
		)

	return snapshot_assets


@frappe.whitelist()
def refresh_assets_from_location(warehouse_stock_name: str) -> str:
	"""
	Rebuild the assets child table rows by querying Asset doctype filtered by warehouse location.
	"""
	doc0 = frappe.get_doc("Warehouse Stock", warehouse_stock_name)
	if not doc0.warehouse:
		frappe.throw("Please set Warehouse before refreshing assets.")

	warehouse = doc0.warehouse
	snapshot_assets = _get_assets_for_warehouse(warehouse)

	# Save with retry logic
	for attempt in range(2):
		doc = frappe.get_doc("Warehouse Stock", warehouse_stock_name)
		doc.set("assets", [])
		for asset in snapshot_assets:
			doc.append("assets", asset)
		try:
			doc.save(ignore_permissions=True)
			return doc.name
		except frappe.TimestampMismatchError:
			if attempt == 0:
				continue
			raise

	return warehouse_stock_name


def _get_warehouses_with_assets() -> list[str]:
	"""
	Return warehouses that have assets at their mapped location.
	This includes field warehouses that may only have assets (no stock items).
	"""
	warehouses_with_assets = []
	
	# Get all non-group warehouses (field warehouses are ledger warehouses)
	all_warehouses = frappe.get_all(
		"Warehouse",
		fields=["name"],
		filters={"is_group": 0},
		limit=1000
	)
	
	# Check each warehouse for assets
	for wh in all_warehouses:
		warehouse_name = wh.get("name")
		if not warehouse_name:
			continue
		
		try:
			# Get location for this warehouse
			location_result = get_location_for_warehouse(warehouse_name)
			location = location_result.get("location") if location_result else None
			
			if not location:
				continue
			
			# Check if there are any assets at this location (draft + submitted; ignore_permissions for inventory view)
			assets = frappe.get_all(
				"Asset",
				fields=["name"],
				filters=[["location", "=", location], ["docstatus", "in", ASSET_DOCSTATUS_NOT_CANCELLED]],
				limit=1,
				ignore_permissions=True,
			)
			
			if assets:
				warehouses_with_assets.append(warehouse_name)
		except Exception:
			# Skip warehouses where location lookup fails
			continue
	
	return warehouses_with_assets


def _get_warehouses_with_positive_stock() -> list[str]:
	"""
	Return warehouses that have *any* item with net qty > 0, computed from Stock Ledger Entry,
	OR warehouses that have assets at their mapped location.
	
	This ensures field warehouses with only assets (no stock items) are also included.
	"""
	# Get warehouses with stock items
	rows = frappe.db.sql(
		"""
		select distinct t.warehouse
		from (
			select
				sle.warehouse as warehouse
			from `tabStock Ledger Entry` sle
			inner join `tabWarehouse` wh on wh.name = sle.warehouse
			where
				sle.is_cancelled = 0
				and sle.warehouse is not null
				and sle.item_code is not null
				and sle.company = wh.company
			group by sle.warehouse, sle.item_code
			having sum(sle.actual_qty) > 0.0001
		) t
		""",
		as_dict=True,
	)
	warehouses_with_stock = [r["warehouse"] for r in rows if r.get("warehouse")]
	
	# Also get warehouses with assets (includes field warehouses)
	warehouses_with_assets = _get_warehouses_with_assets()
	
	# Combine and deduplicate
	all_warehouses = set(warehouses_with_stock + warehouses_with_assets)
	return list(all_warehouses)


@frappe.whitelist()
def sync_warehouse_stock(refresh_existing: int = 0) -> dict:
	"""
	Auto-create Warehouse Stock docs for warehouses that currently have stock available.
	Optionally refresh existing ones too.
	"""
	refresh_existing = int(refresh_existing or 0)

	warehouses = _get_warehouses_with_positive_stock()
	created = 0
	refreshed = 0
	skipped = 0

	# Migrate old WS-#### naming (if any exist) to warehouse-based names
	for ws in frappe.get_all("Warehouse Stock", fields=["name", "warehouse"]):
		if not ws.get("warehouse"):
			continue
		if ws["name"] == ws["warehouse"]:
			continue
		if frappe.db.exists("Warehouse Stock", ws["warehouse"]):
			continue
		try:
			frappe.rename_doc("Warehouse Stock", ws["name"], ws["warehouse"], ignore_permissions=True)
		except Exception:
			# don't block sync if rename fails
			pass

	for wh in warehouses:
		existing_name = _get_ws_docname_for_warehouse(wh)
		if existing_name:
			if refresh_existing:
				try:
					refresh_from_ledger(existing_name)
					refreshed += 1
				except Exception:
					skipped += 1
			continue

		try:
			doc = frappe.get_doc({"doctype": "Warehouse Stock", "warehouse": wh})
			doc.insert(ignore_permissions=True)
			refresh_from_ledger(doc.name)
			created += 1
		except Exception:
			skipped += 1

	return {"created": created, "refreshed": refreshed, "skipped": skipped, "warehouses": len(warehouses)}


def _create_missing_warehouse_stock_docs(warehouses: list[str]) -> dict:
	"""
	Create missing Warehouse Stock docs (named by warehouse), without populating child rows.
	This is fast and safe to run in a request context.
	"""
	created = 0
	existed = 0
	skipped = 0

	for wh in warehouses:
		existing_name = _get_ws_docname_for_warehouse(wh)
		if existing_name:
			existed += 1
			continue

		try:
			doc = frappe.get_doc({"doctype": "Warehouse Stock", "warehouse": wh})
			doc.insert(ignore_permissions=True)
			created += 1
		except Exception:
			skipped += 1

	return {"created": created, "existed": existed, "skipped": skipped}


def _populate_warehouse_stock_items(*, warehouses: list[str], refresh_existing: int = 0) -> dict:
	"""
	Populate items for the given warehouse-doc-names list (doc names == warehouse names).
	Intended to run in background.
	"""
	refresh_existing = int(refresh_existing or 0)
	populated = 0
	skipped = 0

	for wh in warehouses:
		existing_name = _get_ws_docname_for_warehouse(wh)
		if not existing_name:
			continue
		if not refresh_existing:
			# only populate if never refreshed before (best-effort)
			last_refreshed = frappe.db.get_value("Warehouse Stock", existing_name, "last_refreshed_on")
			if last_refreshed:
				continue

		try:
			refresh_from_ledger(existing_name)
			populated += 1
		except Exception:
			skipped += 1

	return {"populated": populated, "skipped": skipped, "warehouses": len(warehouses)}


@frappe.whitelist()
def sync_warehouse_stock_async(refresh_existing: int = 0) -> dict:
	"""
	One-click sync for list view:
	- Quickly ensures Warehouse Stock docs exist for all warehouses with stock
	- Enqueues background job to populate item rows (avoids request timeouts)
	"""
	refresh_existing = int(refresh_existing or 0)

	warehouses = _get_warehouses_with_positive_stock()

	# Migrate old WS-#### naming (if any exist) to warehouse-based names (best effort)
	for ws in frappe.get_all("Warehouse Stock", fields=["name", "warehouse"]):
		if not ws.get("warehouse"):
			continue
		if ws["name"] == ws["warehouse"]:
			continue
		if frappe.db.exists("Warehouse Stock", ws["warehouse"]):
			continue
		try:
			frappe.rename_doc("Warehouse Stock", ws["name"], ws["warehouse"], ignore_permissions=True)
		except Exception:
			pass

	create_stats = _create_missing_warehouse_stock_docs(warehouses)
	frappe.db.commit()

	frappe.enqueue(
		"f2c.inventory.doctype.warehouse_stock.warehouse_stock._populate_warehouse_stock_items",
		queue="long",
		refresh_existing=refresh_existing,
		warehouses=warehouses,
		job_name="warehouse_stock_populate_items",
	)

	return {
		"warehouses": len(warehouses),
		"created": create_stats["created"],
		"existed": create_stats["existed"],
		"skipped": create_stats["skipped"],
		"enqueued": 1,
	}


@frappe.whitelist()
def get_flat_stock_rows(search: str = "", limit: int = 1000, offset: int = 0) -> list[dict]:
	"""
	Return flattened snapshot rows (one row per Warehouse Stock Item) without recalculating from ledger.
	Intended for frontend Flat view.
	"""
	limit = int(limit or 1000)
	offset = int(offset or 0)
	search = (search or "").strip()

	conditions = ""
	params = {"limit": limit, "offset": offset}
	if search:
		conditions = """
			and (
				ws.warehouse like %(q)s
				or wsi.item_code like %(q)s
				or wsi.item_name like %(q)s
				or wsi.category like %(q)s
			)
		"""
		params["q"] = f"%{search}%"

	return frappe.db.sql(
		f"""
		select
			ws.warehouse as warehouse,
			ws.company as company,
			ws.last_refreshed_on as last_refreshed_on,
			wsi.item_code as item_code,
			wsi.item_name as item_name,
			wsi.category as category,
			wsi.qty as qty,
			wsi.stock_uom as stock_uom
		from `tabWarehouse Stock Item` wsi
		inner join `tabWarehouse Stock` ws on ws.name = wsi.parent
		where ws.docstatus < 2
		{conditions}
		order by ws.modified desc, wsi.idx asc
		limit %(limit)s offset %(offset)s
		""",
		params,
		as_dict=True,
	)




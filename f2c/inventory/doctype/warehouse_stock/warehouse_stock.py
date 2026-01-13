import frappe
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import flt, now_datetime


class WarehouseStock(Document):
	def validate(self):
		if not self.warehouse:
			return

		company = frappe.db.get_value("Warehouse", self.warehouse, "company")
		if company:
			self.company = company


@frappe.whitelist()
def refresh_from_ledger(warehouse_stock_name: str) -> str:
	"""
	Rebuild the child table rows by computing net qty per item from Stock Ledger Entry.
	"""
	doc = frappe.get_doc("Warehouse Stock", warehouse_stock_name)
	if not doc.warehouse:
		frappe.throw("Please set Warehouse before refreshing.")

	company = frappe.db.get_value("Warehouse", doc.warehouse, "company")
	if not company:
		frappe.throw("Warehouse has no Company set; cannot refresh.")

	doc.company = company

	sle = frappe.qb.DocType("Stock Ledger Entry")
	rows = (
		frappe.qb.from_(sle)
		.select(sle.item_code, Sum(sle.actual_qty).as_("qty"))
		.where(
			(sle.warehouse == doc.warehouse)
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

	# Replace snapshot rows
	doc.set("items", [])
	for r in rows:
		item_code = r.get("item_code")
		qty = flt(r.get("qty") or 0, 3)
		if not item_code or abs(qty) < 0.0001:
			continue
		meta = item_meta.get(item_code) or {}
		doc.append(
			"items",
			{
				"item_code": item_code,
				"item_name": meta.get("item_name"),
				"category": meta.get("item_group"),
				"qty": qty,
				"stock_uom": meta.get("stock_uom"),
			},
		)

	doc.last_refreshed_on = now_datetime()
	doc.save(ignore_permissions=True)
	return doc.name


def _get_warehouses_with_positive_stock() -> list[str]:
	"""
	Return warehouses that have *any* item with net qty > 0, computed from Stock Ledger Entry.

	We treat sum(actual_qty) per (warehouse, item_code) as the current balance for that item.
	"""
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
	return [r["warehouse"] for r in rows if r.get("warehouse")]


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
		if frappe.db.exists("Warehouse Stock", wh):
			if refresh_existing:
				try:
					refresh_from_ledger(wh)
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
		if frappe.db.exists("Warehouse Stock", wh):
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
		if not frappe.db.exists("Warehouse Stock", wh):
			continue
		if not refresh_existing:
			# only populate if never refreshed before (best-effort)
			last_refreshed = frappe.db.get_value("Warehouse Stock", wh, "last_refreshed_on")
			if last_refreshed:
				continue

		try:
			refresh_from_ledger(wh)
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




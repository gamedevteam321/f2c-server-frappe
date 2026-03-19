from __future__ import annotations

import frappe


def _first_value(doctype: str, field: str = "name") -> str:
	row = frappe.get_all(doctype, fields=[field], limit=1)
	if not row:
		raise RuntimeError(f"No records found for {doctype}")
	return row[0][field]


def _find_item_with_asset_series() -> str:
	row = frappe.get_all(
		"Item",
		filters={"asset_naming_series": ["!=", ""]},
		fields=["name", "asset_naming_series"],
		limit=1,
	)
	if not row:
		raise RuntimeError("No Item found with asset_naming_series set; cannot test auto Asset creation.")
	return row[0]["name"]


def run() -> dict:
	"""
	Smoke test for auto-create Asset hooks.
	Creates+deletes one record per equipment DocType, verifying an Asset is created and linked.
	"""
	item_code = _find_item_with_asset_series()
	company = _first_value("Company")
	location = _first_value("Location")

	created = []
	results: dict[str, dict] = {}

	def _create_and_assert(doctype: str, payload: dict):
		doc = frappe.get_doc({"doctype": doctype, **payload})
		doc.insert(ignore_permissions=True)
		if not doc.get("asset"):
			raise RuntimeError(f"{doctype} created but no asset link set.")
		if not frappe.db.exists("Asset", doc.get("asset")):
			raise RuntimeError(f"{doctype} asset {doc.get('asset')} does not exist.")
		created.append((doctype, doc.name, doc.get("asset")))
		return doc

	try:
		m = _create_and_assert(
			"Machinery",
			{
				"item_code": item_code,
				"company": company,
				"location": location,
				"purchase_date": frappe.utils.today(),
				"is_existing_asset": 1,
				"available_for_use_date": frappe.utils.today(),
				"machinery_type": "Tractor",
				"machinery_name": "Smoke Test Tractor",
				"brand": "TestBrand",
				"model": "T-1",
			},
		)
		results["Machinery"] = {"name": m.name, "asset": m.asset}

		i = _create_and_assert(
			"Implement",
			{
				"item_code": item_code,
				"company": company,
				"location": location,
				"purchase_date": frappe.utils.today(),
				"is_existing_asset": 1,
				"available_for_use_date": frappe.utils.today(),
				"implement_type": "Plow",
				"implement_name": "Smoke Test Plow",
				"brand": "TestBrand",
				"model": "P-1",
			},
		)
		results["Implement"] = {"name": i.name, "asset": i.asset}

		ht = _create_and_assert(
			"Hand Tool",
			{
				"item_code": item_code,
				"company": company,
				"location": location,
				"purchase_date": frappe.utils.today(),
				"is_existing_asset": 1,
				"available_for_use_date": frappe.utils.today(),
				"hand_tool_type": "Shovel",
				"tool_name": "Smoke Test Shovel",
				"brand": "TestBrand",
				"model": "S-1",
			},
		)
		results["Hand Tool"] = {"name": ht.name, "asset": ht.asset}

		ot = _create_and_assert(
			"Other Tool",
			{
				"item_code": item_code,
				"company": company,
				"location": location,
				"purchase_date": frappe.utils.today(),
				"is_existing_asset": 1,
				"available_for_use_date": frappe.utils.today(),
				"other_tool_type": "SmokeType",
				"tool_name": "Smoke Test Other Tool",
				"brand": "TestBrand",
				"model": "O-1",
			},
		)
		results["Other Tool"] = {"name": ot.name, "asset": ot.asset}

		return {"ok": True, "results": results}
	finally:
		# Cleanup in reverse order
		for doctype, name, asset in reversed(created):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=1)
			# Delete auto-created Asset if still present
			if asset and frappe.db.exists("Asset", asset):
				frappe.delete_doc("Asset", asset, ignore_permissions=True, force=1)



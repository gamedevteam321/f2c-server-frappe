import frappe


def _get_linked_warehouses_for_area(area_name: str) -> set[str]:
	"""
	Return all Warehouse names linked to a Geo Fencing Area.

	Links exist via:
	- Warehouse.custom_geo_fence_area (custom field)
	- Geo Fencing Area -> child table "warehouses" (doctype: Geo Fencing Area Warehouse)
	"""
	linked: set[str] = set()

	# 1) By custom link field on Warehouse (most reliable for delete)
	try:
		for wh in frappe.get_all(
			"Warehouse",
			filters={"custom_geo_fence_area": area_name},
			pluck="name",
		) or []:
			if wh:
				linked.add(str(wh))
	except Exception:
		pass

	# 2) By child table rows
	try:
		for wh in frappe.get_all(
			"Geo Fencing Area Warehouse",
			filters={"parent": area_name, "parenttype": "Geo Fencing Area"},
			pluck="warehouse",
		) or []:
			if wh:
				linked.add(str(wh))
	except Exception:
		pass

	return linked


def _collect_warehouse_subtree_names(root_wh: str) -> list[str]:
	"""Collect warehouse subtree (including root) children-first."""
	info = frappe.db.get_value("Warehouse", root_wh, ["lft", "rgt"], as_dict=True)
	if not info or info.lft is None or info.rgt is None:
		return [root_wh]
	rows = frappe.get_all(
		"Warehouse",
		filters={"lft": (">=", info.lft), "rgt": ("<=", info.rgt)},
		fields=["name", "lft"],
		order_by="lft desc",
	)
	return [r["name"] for r in rows if r.get("name")] or [root_wh]


@frappe.whitelist()
def delete_geo_fencing_area_cascade(area_name: str, disable_if_cannot_delete: int = 1):
	"""
	Delete a Geo Fencing Area and automatically delete its linked Warehouses first.

	This fixes the common issue where deleting a Geo Fencing Area fails with:
	"Cannot delete ... because it is linked to Warehouse ..."

	Notes:
	- If a Warehouse cannot be deleted due to stock ledger/bins/child warehouses, we will raise an error
	  unless `disable_if_cannot_delete` is truthy. In that case we will:
	  - disable the Warehouse(es)
	  - unlink `custom_geo_fence_area`
	  and proceed to delete the Geo Fencing Area.
	"""
	area_name = (area_name or "").strip()
	if not area_name:
		frappe.throw("area_name is required")

	if not frappe.db.exists("Geo Fencing Area", area_name):
		frappe.throw(f"Geo Fencing Area not found: {area_name}")

	# Ensure the current user is allowed to delete the Geo Fencing Area.
	if not frappe.has_permission("Geo Fencing Area", "delete", area_name):
		frappe.throw("Not permitted to delete this Geo Fencing Area.")

	linked_wh = _get_linked_warehouses_for_area(area_name)
	to_delete: list[str] = []

	# Expand to include full subtrees (so group warehouses delete their stock child warehouses too).
	seen: set[str] = set()
	for wh in linked_wh:
		if not wh or wh in seen or not frappe.db.exists("Warehouse", wh):
			continue
		for name in _collect_warehouse_subtree_names(wh):
			if name and name not in seen:
				seen.add(name)
				to_delete.append(name)

	# Delete warehouses (children-first order is already ensured by subtree collection).
	# If multiple subtrees overlap, `seen` dedupes.
	deleted: list[str] = []
	disabled: list[str] = []
	failed: list[tuple[str, str]] = []
	for wh_name in to_delete:
		try:
			# Ignore permissions for warehouses: this is a cascade from deleting the geo area.
			frappe.delete_doc("Warehouse", wh_name, ignore_permissions=True)
			deleted.append(wh_name)
		except Exception as e:
			# Optionally fall back to disabling + unlinking instead of failing hard.
			if disable_if_cannot_delete:
				try:
					# Unlink from Geo Fencing Area to avoid LinkExistsError.
					# Also disable instead of deleting (ERPNext recommended for warehouses with stock).
					frappe.db.set_value("Warehouse", wh_name, "disabled", 1, update_modified=False)
					# custom field might not exist in some deployments; guard it.
					if frappe.db.has_column("Warehouse", "custom_geo_fence_area"):
						frappe.db.set_value("Warehouse", wh_name, "custom_geo_fence_area", None, update_modified=False)
					# Remove child table rows pointing at this warehouse (best-effort).
					try:
						frappe.db.delete(
							"Geo Fencing Area Warehouse",
							{"parent": area_name, "parenttype": "Geo Fencing Area", "warehouse": wh_name},
						)
					except Exception:
						pass
					disabled.append(wh_name)
				except Exception as e2:
					failed.append((wh_name, f"{e} | disable/unlink failed: {e2}"))
			else:
				failed.append((wh_name, str(e)))

	if failed:
		# Keep message small-ish but useful
		lines = []
		for name, msg in failed[:5]:
			lines.append(f"- {name}: {msg}")
		more = ""
		if len(failed) > 5:
			more = f"\n(and {len(failed) - 5} more)"
		frappe.throw(
			"Could not delete the linked Warehouse(s) automatically.\n"
			"This usually happens if stock entries / quantities exist in the warehouse.\n\n"
			+ "\n".join(lines)
			+ more
		)

	# Delete the Geo Fencing Area (after warehouses are removed).
	# Use normal delete to keep integrity checks for other links.
	frappe.delete_doc("Geo Fencing Area", area_name, ignore_permissions=False)

	return {"ok": True, "warehouses_deleted": deleted, "warehouses_disabled": disabled}


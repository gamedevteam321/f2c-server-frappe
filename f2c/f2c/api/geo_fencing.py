import frappe
from frappe.model.rename_doc import get_link_fields

SAFE_DELETE_LINKED_DOCTYPES = {"Weather Report"}
DIRECT_DETACH_TYPES = {"Farm", "Cluster"}


def _title_for_link_row(doctype: str, row: dict) -> str:
	if not isinstance(row, dict):
		return str(row)
	for key in (
		"area_name",
		"field_name",
		"project_name",
		"subject",
		"title",
		"report_number",
		"name",
	):
		val = row.get(key)
		if val:
			return str(val)
	return str(row.get("name") or "")


def _delete_or_cancel_then_delete(doctype: str, name: str) -> None:
	doc = frappe.get_doc(doctype, name)
	if doc.meta.is_submittable and doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	frappe.delete_doc(doctype, name, ignore_permissions=True)


def _clear_direct_link_field(doctype: str, name: str, fieldname: str) -> None:
	meta = frappe.get_meta(doctype)
	updates = {fieldname: None}
	for df in meta.fields or []:
		fetch_from = (getattr(df, "fetch_from", None) or "").strip()
		if fetch_from.startswith(f"{fieldname}."):
			updates[df.fieldname] = None
	frappe.db.set_value(doctype, name, updates, update_modified=False)


def _get_geo_area_type(area_name: str) -> str:
	return (frappe.db.get_value("Geo Fencing Area", area_name, "geo_fencing_type") or "").strip()


def _detach_child_geo_areas(area_name: str) -> None:
	"""Remove the parent relationship from child geo areas instead of deleting them."""
	errors: list[str] = []
	for child_name in frappe.get_all(
		"Geo Fencing Area",
		filters={"parent_area": area_name},
		pluck="name",
		order_by="name asc",
	):
		try:
			child_doc = frappe.get_doc("Geo Fencing Area", child_name)
			child_doc.parent_area = None
			child_doc.save(ignore_permissions=True)
		except Exception as e:
			errors.append(f"Geo Fencing Area {child_name}: {str(e)}")

	if errors:
		frappe.throw(
			"Could not detach all child geo areas before delete:\n"
			+ "\n".join(errors[:12])
			+ (f"\n(and {len(errors) - 12} more)" if len(errors) > 12 else "")
		)


def _remove_inbound_references_to_geo_area(area_name: str, force_clear_direct_links: bool = False) -> None:
	"""
	Delete or unlink only the minimum data that blocks delete.

	Skips:
	- parent_area on Geo Fencing Area (children are detached before this runs)
	- Warehouse (handled earlier in cascade)
	"""
	errors: list[str] = []
	blockers: list[str] = []

	for lf in get_link_fields("Geo Fencing Area"):
		link_dt = lf.get("parent")
		fieldname = lf.get("fieldname")
		if not link_dt or not fieldname:
			continue
		if link_dt == "Geo Fencing Area" and fieldname == "parent_area":
			continue
		if link_dt == "Warehouse":
			continue
		if link_dt == "Lease Plot" and fieldname == "linked_geo_fencing_area":
			continue
		if not frappe.db.exists("DocType", link_dt):
			continue

		meta = frappe.get_meta(link_dt)

		if meta.istable:
			rows = frappe.get_all(
				link_dt,
				filters={fieldname: area_name},
				fields=["name", "parent", "parenttype"],
			)
			for row in rows:
				parent = row.get("parent")
				parenttype = row.get("parenttype")
				if not parent or not parenttype:
					continue
				try:
					parent_meta = frappe.get_meta(parenttype)
					table_field = None
					for df in parent_meta.get_table_fields():
						if df.options == link_dt:
							table_field = df.fieldname
							break
					if not table_field:
						frappe.db.delete(link_dt, {"name": row.get("name")})
						continue
					doc = frappe.get_doc(parenttype, parent)
					to_remove = [
						c
						for c in (doc.get(table_field) or [])
						if getattr(c, fieldname, None) == area_name or c.get(fieldname) == area_name
					]
					for c in to_remove:
						doc.remove(c)
					doc.save(ignore_permissions=True)
				except Exception as e:
					errors.append(f"{link_dt} row {row.get('name')}: {str(e)}")
		else:
			names = frappe.get_all(link_dt, filters={fieldname: area_name}, pluck="name")
			field_meta = meta.get_field(fieldname)
			for nm in names:
				if not nm:
					continue
				try:
					if link_dt in SAFE_DELETE_LINKED_DOCTYPES:
						_delete_or_cancel_then_delete(link_dt, nm)
					elif force_clear_direct_links or (field_meta and not field_meta.reqd):
						_clear_direct_link_field(link_dt, nm, fieldname)
					else:
						blockers.append(f"{link_dt} {nm}")
				except Exception as e:
					errors.append(f"{link_dt} {nm}: {str(e)}")

	if blockers:
		frappe.throw(
			"Cannot delete this Geo Fencing Area because these documents still directly depend on it:\n"
			+ "\n".join(f"- {item}" for item in blockers[:12])
			+ (f"\n(and {len(blockers) - 12} more)" if len(blockers) > 12 else "")
		)

	if errors:
		frappe.throw(
			"Could not remove the blocking references for this Geo Fencing Area:\n"
			+ "\n".join(errors[:12])
			+ (f"\n(and {len(errors) - 12} more)" if len(errors) > 12 else "")
		)


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
def get_geo_fencing_area_linked_documents(area_name: str):
	"""Return documents that reference this Geo Fencing Area (for delete confirmation UI)."""
	from frappe.desk.form.linked_with import get_linked_docs, get_linked_doctypes

	area_name = (area_name or "").strip()
	if not area_name:
		frappe.throw("area_name is required")
	if not frappe.db.exists("Geo Fencing Area", area_name):
		frappe.throw(f"Geo Fencing Area not found: {area_name}")
	frappe.has_permission("Geo Fencing Area", "read", area_name, throw=True)

	linkinfo = get_linked_doctypes("Geo Fencing Area")
	linked = get_linked_docs("Geo Fencing Area", area_name, linkinfo)
	items = []
	for dt, rows in (linked or {}).items():
		for row in rows or []:
			if not isinstance(row, dict):
				continue
			nm = row.get("name")
			items.append(
				{
					"doctype": dt,
					"name": nm,
					"title": _title_for_link_row(dt, row),
				}
			)
	return {"items": items}


@frappe.whitelist()
def delete_geo_fencing_area_cascade(
	area_name: str, disable_if_cannot_delete: int = 1, delete_related_links: int = 1
):
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

	- Child Geo Fencing Areas are detached from this parent instead of being deleted.
	- Only minimal blocking data is removed automatically:
	  - Weather Reports are deleted
	  - child-table rows that reference this area are removed
	  - optional direct links are cleared
	- Business documents that still directly depend on this area (for example Crop Plan field/block links)
	  are preserved and will block delete.
	"""
	area_name = (area_name or "").strip()
	if not area_name:
		frappe.throw("area_name is required")

	if not frappe.db.exists("Geo Fencing Area", area_name):
		frappe.throw(f"Geo Fencing Area not found: {area_name}")

	# Ensure the current user is allowed to delete the Geo Fencing Area.
	if not frappe.has_permission("Geo Fencing Area", "delete", area_name):
		frappe.throw("Not permitted to delete this Geo Fencing Area.")

	geo_area_type = _get_geo_area_type(area_name)
	force_clear_direct_links = geo_area_type in DIRECT_DETACH_TYPES

	_detach_child_geo_areas(area_name)

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

	# If this geo area originated from Plot Management field creation,
	# release those plots before deleting the geo area, otherwise
	# Frappe's link validation will block the delete.
	try:
		from f2c.lease_module.api.lease_plot import clear_field_mapping_for_geo_area

		clear_field_mapping_for_geo_area(area_name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Failed to clear plot field mapping for {area_name}")
		frappe.throw(
			f"Could not remove plot links for Geo Fencing Area {area_name}. "
			"Please try again or contact support if the issue persists."
		)

	_remove_inbound_references_to_geo_area(area_name, force_clear_direct_links=force_clear_direct_links)

	# Delete the Geo Fencing Area (after warehouses are removed and plot links are cleared).
	# Use normal delete to keep integrity checks for other links.
	frappe.delete_doc("Geo Fencing Area", area_name, ignore_permissions=False)

	return {"ok": True, "warehouses_deleted": deleted, "warehouses_disabled": disabled}


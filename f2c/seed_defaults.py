import frappe

from f2c.access.constants import (
	CLUSTER_SUPERVISOR_ROLE,
	DRIVER_ROLE,
	EMPLOYEE_ALLOWED_GEO_FIELDNAME,
	FARM_MANAGER_ROLE,
	FIELD_SUPERVISOR_ROLE,
	PROJECT_MANAGER_ROLE,
	USER_ASSIGNED_FIELD_FIELDNAME,
)
from f2c.access.migrate_user_scope_to_employee import run_migrate_user_scope_to_employee
from f2c.farm_to_crop.doctype.water_source.seed_data import seed_water_source


def ensure_irrigation_types() -> None:
	"""Create default Irrigation Type master records if they don't exist.

	Idempotent: safe to run multiple times.
	"""
	# Only bootstrap defaults when the master is empty.
	# This avoids re-creating records that an admin intentionally removed.
	if frappe.db.count("Irrigation Type") > 0:
		return

	defaults = ["Drip", "Sprinkler", "Flood", "Other"]

	for name in defaults:
		frappe.get_doc(
			{
				"doctype": "Irrigation Type",
				"irrigation_type_name": name,
			}
		).insert(ignore_permissions=True)


def ensure_item_group_default_gst_hsn_code_field() -> None:
	"""Add Custom Field on Item Group for default GST HSN Code (India Compliance). Idempotent."""
	if not frappe.db.table_exists("GST HSN Code"):
		return
	if frappe.db.exists("Custom Field", {"dt": "Item Group", "fieldname": "default_gst_hsn_code"}):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Item Group",
				"fieldname": "default_gst_hsn_code",
				"label": "Default GST HSN Code",
				"fieldtype": "Link",
				"options": "GST HSN Code",
				"insert_after": "parent_item_group",
				"description": "Default HSN/SAC code for Items in this group (used when creating fixed-asset Items).",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_item_group_default_gst_hsn_code_field failed")


def ensure_default_gst_hsn_code_00000000() -> None:
	"""Create default GST HSN Code '00000000' when India Compliance is installed. Idempotent."""
	if not frappe.db.table_exists("GST HSN Code"):
		return
	if frappe.db.exists("GST HSN Code", "00000000"):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "GST HSN Code",
				"hsn_code": "00000000",
				"description": "Default / Not specified",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_default_gst_hsn_code_00000000 failed")


def ensure_equipment_parts_item_groups() -> None:
	"""Create Item Groups for Equipment Report parts list (per equipment type). Idempotent."""
	from f2c.scripts.populate_equipment_parts import EQUIPMENT_PARTS_GROUPS
	for group_name in EQUIPMENT_PARTS_GROUPS:
		if frappe.db.exists("Item Group", group_name):
			continue
		try:
			frappe.get_doc(
				{"doctype": "Item Group", "item_group_name": group_name}
			).insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"f2c ensure_equipment_parts_item_groups ({group_name}) failed",
			)


def ensure_equipment_spec_options() -> None:
	"""Seed Equipment Spec Option rows to match React static dropdowns (idempotent).

	Source of truth for labels:
	- `equipmentSpecFieldLibrary.ts` → BASE_FIELDS (market, power source, mounting, criticality, spec status)
	- Category defs → `fuel_type_select` options (union + EngineSpecsSection / equipmentSpecOptions fallback)

	Skips any (option_type, title) that already exists.

	Runs automatically on `bench migrate` (after_migrate). To run once on a live site without migrate::

		bench --site <site> execute f2c.seed_defaults.ensure_equipment_spec_options
	"""
	if not frappe.db.exists("DocType", "Equipment Spec Option"):
		return

	# (option_type, title, sort_order) — titles must match values stored in specs_json / Machinery.fuel_type
	defaults: list[tuple[str, str, int]] = [
		# Market — BASE_FIELDS market_select
		("Market", "India", 10),
		# Power Source — BASE_FIELDS power_source_select
		("Power Source", "Diesel", 10),
		("Power Source", "Electric", 20),
		("Power Source", "Petrol", 30),
		("Power Source", "Hybrid", 40),
		("Power Source", "Manual", 50),
		# Mounting Type — BASE_FIELDS mounting_type_select
		("Mounting Type", "None", 10),
		("Mounting Type", "Mounted", 20),
		("Mounting Type", "Non-mounted", 30),
		("Mounting Type", "Trailed", 40),
		("Mounting Type", "Self-propelled", 50),
		("Mounting Type", "Handheld", 60),
		("Mounting Type", "Skid", 70),
		# Spec Status — BASE_FIELDS status_select (lowercase = stored value)
		("Spec Status", "shortlisted", 10),
		("Spec Status", "approved", 20),
		("Spec Status", "rejected", 30),
		# Criticality — BASE_FIELDS criticality_select
		("Criticality", "Core", 10),
		("Criticality", "Support", 20),
		("Criticality", "Optional", 30),
		# Fuel Type — union of Engine fallback + Vehicle / Pump fuel_type_select in TS
		("Fuel Type", "Diesel", 10),
		("Fuel Type", "Petrol", 20),
		("Fuel Type", "Electric", 30),
		("Fuel Type", "Hybrid", 40),
		("Fuel Type", "Other", 50),
		("Fuel Type", "CNG", 60),
		("Fuel Type", "EV", 70),
		# Implement-specific selects in equipmentSpecFieldLibrary.ts
		("PTO Speed Required", "540", 10),
		("PTO Speed Required", "1000", 20),
		("Hitch Category", "Cat 1", 10),
		("Hitch Category", "Cat 2", 20),
		("Hitch Category", "Cat 3", 30),
		("Safety Mechanism", "Shear bolt", 10),
		("Safety Mechanism", "Spring", 20),
		("Safety Mechanism", "None", 30),
	]

	for option_type, title, sort_order in defaults:
		if frappe.db.exists("Equipment Spec Option", {"option_type": option_type, "title": title}):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "Equipment Spec Option",
					"option_type": option_type,
					"title": title,
					"sort_order": sort_order,
					"disabled": 0,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"f2c ensure_equipment_spec_options ({option_type!r}, {title!r}) failed",
			)
	frappe.db.commit()


def ensure_f2c_settings() -> None:
	"""Create default F2C Settings single if missing (idempotent)."""
	if not frappe.db.exists("DocType", "F2C Settings"):
		return
	if frappe.db.exists("F2C Settings", "F2C Settings"):
		return
	try:
		doc = frappe.new_doc("F2C Settings")
		doc.enforce_logistics_location_check = 1
		doc.logistics_proximity_radius_meters = 1000
		doc.strict_geo_area_for_warehouse_lookup = 1
		doc.manual_transfer_equipment_page_size = 10
		doc.ltt_schedule_planned_times_enabled = 1
		doc.ltt_dropoff_buffer_minutes = 10
		doc.ltt_travel_avg_speed_kph = 35
		doc.ltt_travel_road_factor = 1.25
		doc.ltt_travel_min_minutes = 5
		doc.ltt_travel_max_minutes = 480
		doc.ltt_travel_fallback_minutes = 60
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_f2c_settings failed")


def ensure_field_supervisor_role() -> None:
	"""Create Field Supervisor role if missing (idempotent)."""
	if frappe.db.exists("Role", FIELD_SUPERVISOR_ROLE):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": FIELD_SUPERVISOR_ROLE,
				"desk_access": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_field_supervisor_role failed")


def ensure_cluster_supervisor_role() -> None:
	"""Create Cluster Supervisor role if missing (idempotent)."""
	if frappe.db.exists("Role", CLUSTER_SUPERVISOR_ROLE):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": CLUSTER_SUPERVISOR_ROLE,
				"desk_access": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_cluster_supervisor_role failed")


def ensure_driver_role() -> None:
	"""Create Driver role if missing (idempotent)."""
	if frappe.db.exists("Role", DRIVER_ROLE):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": DRIVER_ROLE,
				"desk_access": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_driver_role failed")


def ensure_farm_manager_role() -> None:
	"""Create Farm Manager role if missing (idempotent)."""
	if frappe.db.exists("Role", FARM_MANAGER_ROLE):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": FARM_MANAGER_ROLE,
				"desk_access": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_farm_manager_role failed")


def ensure_project_manager_role() -> None:
	"""Create Project Manager role if missing (idempotent)."""
	if frappe.db.exists("Role", PROJECT_MANAGER_ROLE):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": PROJECT_MANAGER_ROLE,
				"desk_access": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_project_manager_role failed")


def _hide_legacy_user_f2c_assigned_field() -> None:
	"""Keep DB column for optional fallback; hidden — scope is configured on Employee Allowed Geo Areas."""
	name = frappe.db.get_value("Custom Field", {"dt": "User", "fieldname": USER_ASSIGNED_FIELD_FIELDNAME}, "name")
	if not name:
		return
	try:
		cf = frappe.get_doc("Custom Field", name)
		if not cf.hidden:
			cf.hidden = 1
			cf.description = (
				"Legacy single Geo Fencing Area (read by server only if Employee Allowed Geo Areas is empty). "
				"Configure scope on the Employee record (Attendance & Leaves); this field is hidden."
			)
			cf.save(ignore_permissions=True)
			frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c _hide_legacy_user_f2c_assigned_field failed")


def ensure_user_f2c_assigned_field() -> None:
	"""Custom Field on User: legacy link (hidden); primary scope is Employee.allowed_geo_areas."""
	if frappe.db.exists("Custom Field", {"dt": "User", "fieldname": USER_ASSIGNED_FIELD_FIELDNAME}):
		_hide_legacy_user_f2c_assigned_field()
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "User",
				"fieldname": USER_ASSIGNED_FIELD_FIELDNAME,
				"label": "F2C Assigned Field",
				"fieldtype": "Link",
				"options": "Geo Fencing Area",
				"hidden": 1,
				"description": (
					"Legacy single Geo Fencing Area (read by server only if Employee Allowed Geo Areas is empty). "
					"Configure scope on the Employee record (Attendance & Leaves); this field is hidden."
				),
				"insert_after": "mobile_no",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c ensure_user_f2c_assigned_field failed")


def patch_employee_allowed_geo_areas_description() -> None:
	"""Align Employee Allowed Geo Areas help text with F2C role rules (idempotent)."""
	name = frappe.db.get_value("Custom Field", {"dt": "Employee", "fieldname": EMPLOYEE_ALLOWED_GEO_FIELDNAME}, "name")
	if not name:
		return
	desc = (
		"Field Supervisor: use Add Row to assign one or more Field-level Geo Fencing Areas (each row is a root). "
		"Cluster Supervisor / Driver: use Add Row to assign one or more Cluster-level Geo Fencing Areas (each row is a root). "
		"Farm Manager: assign Farm, Cluster, or Field level areas (each row is a root). "
		"Sub-areas inherit access. Only System Manager, Administrator, or Project Manager may edit this table for users with those scoped roles. "
		"For attendance punch-in on farm land, Field-level areas are used where applicable."
	)
	try:
		cf = frappe.get_doc("Custom Field", name)
		if (cf.description or "").strip() == desc.strip():
			return
		cf.description = desc
		cf.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c patch_employee_allowed_geo_areas_description failed")


def ensure_field_supervisor_doctype_permissions() -> None:
	"""Append DocPerm rows on key DocTypes so Field Supervisor can use the React app (idempotent)."""
	if not frappe.db.exists("Role", FIELD_SUPERVISOR_ROLE):
		return
	perms: list[tuple[str, dict]] = [
		(
			"Farm Task Execution",
			{"read": 1, "write": 1, "create": 0, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Logistics Transfer Ticket",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Worker Details",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Worker Attendance",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Crop Plan Schedule",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"On Demand Activity",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Crop Plan",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Geo Fencing Area",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Activity",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Activity Group Type",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Report Ticket",
			{"read": 1, "write": 1, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Location",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Warehouse",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Stock Entry",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Bin",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Asset",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Item",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"F2C Settings",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 0, "email": 0, "report": 0, "share": 0},
		),
	]
	for doctype, perm in perms:
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			dt = frappe.get_doc("DocType", doctype)
			if any(getattr(p, "role", None) == FIELD_SUPERVISOR_ROLE for p in (dt.permissions or [])):
				continue
			row = {"role": FIELD_SUPERVISOR_ROLE, **perm}
			dt.append("permissions", row)
			dt.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"f2c Field Supervisor DocPerm {doctype} failed")


def ensure_cluster_supervisor_doctype_permissions() -> None:
	"""DocPerm rows for Cluster Supervisor: like Field Supervisor but no On Demand Activity; Supplier Contact without delete."""
	if not frappe.db.exists("Role", CLUSTER_SUPERVISOR_ROLE):
		return
	perms: list[tuple[str, dict]] = [
		(
			"Farm Task Execution",
			{"read": 1, "write": 1, "create": 0, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Logistics Transfer Ticket",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Worker Details",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Worker Attendance",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Crop Plan Schedule",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Crop Plan",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Geo Fencing Area",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Activity",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Activity Group Type",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Farm Report Ticket",
			{"read": 1, "write": 1, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Location",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Warehouse",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Stock Entry",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Bin",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Asset",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Item",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"F2C Settings",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 0, "email": 0, "report": 0, "share": 0},
		),
		(
			"Supplier Contact",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
	]
	for doctype, perm in perms:
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			dt = frappe.get_doc("DocType", doctype)
			if any(getattr(p, "role", None) == CLUSTER_SUPERVISOR_ROLE for p in (dt.permissions or [])):
				continue
			row = {"role": CLUSTER_SUPERVISOR_ROLE, **perm}
			dt.append("permissions", row)
			dt.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"f2c Cluster Supervisor DocPerm {doctype} failed")


def ensure_driver_doctype_permissions() -> None:
	"""DocPerm rows for Driver: logistics (LTT) plus read-only masters for inventory / transfers."""
	if not frappe.db.exists("Role", DRIVER_ROLE):
		return
	perms: list[tuple[str, dict]] = [
		(
			"Farm Task Execution",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Logistics Transfer Ticket",
			{"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Geo Fencing Area",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Location",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Warehouse",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Stock Entry",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Bin",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Asset",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"Item",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0},
		),
		(
			"F2C Settings",
			{"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 0, "email": 0, "report": 0, "share": 0},
		),
	]
	for doctype, perm in perms:
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			dt = frappe.get_doc("DocType", doctype)
			if any(getattr(p, "role", None) == DRIVER_ROLE for p in (dt.permissions or [])):
				continue
			row = {"role": DRIVER_ROLE, **perm}
			dt.append("permissions", row)
			dt.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"f2c Driver DocPerm {doctype} failed")


def ensure_farm_manager_doctype_permissions() -> None:
	"""DocPerm rows for Farm Manager: geo-scoped full app (React audit useFrappeGetDocList + CS/FS parity, Apr 2026)."""
	if not frappe.db.exists("Role", FARM_MANAGER_ROLE):
		return
	_rw = {"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0}
	_rw_nc = {"read": 1, "write": 1, "create": 0, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0}
	_ro = {"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 1, "email": 0, "report": 1, "share": 0}
	_ro_f2c = {"read": 1, "write": 0, "create": 0, "delete": 0, "export": 0, "print": 0, "email": 0, "report": 0, "share": 0}
	_supplier = {"read": 1, "write": 1, "create": 1, "delete": 0, "export": 1, "print": 1, "email": 0, "report": 1, "share": 0}
	perms: list[tuple[str, dict]] = [
		("Farm Task Execution", _rw_nc),
		("Logistics Transfer Ticket", _rw),
		("Farm Worker Details", _rw),
		("Farm Worker Attendance", _rw),
		("Crop Plan Schedule", _rw_nc),
		("Crop Plan", _ro),
		("On Demand Activity", _rw_nc),
		("Geo Fencing Area", _ro),
		("Geo Fencing Type", _ro),
		("Farm Activity", _ro),
		("Activity Group Type", _ro),
		("Farm Report Ticket", _rw_nc),
		("Location", _ro),
		("Warehouse", _ro),
		("Stock Entry", _rw_nc),
		("Bin", _ro),
		("Asset", _ro),
		("Item", _ro),
		("F2C Settings", _ro_f2c),
		("Supplier Contact", _supplier),
		("Warehouse Stock", _rw_nc),
		("Material Request", _rw_nc),
		("Stock Transfer Ticket", _rw_nc),
		("Pick List", _rw_nc),
		("Farm Project", _ro),
		("POP", _rw_nc),
		("Land Preparation", _rw_nc),
		("Crop", _ro),
		("Crop Stage", _ro),
		("Farm Tasks", _rw_nc),
		("Spacing", _rw_nc),
		("POP-Activity List", _rw_nc),
		("Machinery", _rw_nc),
		("Implement", _rw_nc),
		("Hand Tool", _rw_nc),
		("Other Tool", _rw_nc),
		("Weather Report", _rw_nc),
		("Water Source", _ro),
		("Irrigation Type", _ro),
		("UOM", _ro),
		("Company", _ro),
		("User", _ro),
	]
	for doctype, perm in perms:
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			dt = frappe.get_doc("DocType", doctype)
			if any(getattr(p, "role", None) == FARM_MANAGER_ROLE for p in (dt.permissions or [])):
				continue
			row = {"role": FARM_MANAGER_ROLE, **perm}
			dt.append("permissions", row)
			dt.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"f2c Farm Manager DocPerm {doctype} failed")


def patch_crop_plan_docperm_for_f2c_scoped_roles() -> None:
	"""Ensure Crop Plan DocPerm rows match F2C intent for React scheduling.

	``ensure_*_doctype_permissions`` / PM clone skip when *any* DocPerm row already exists for
	that role+permlevel. A mistaken row (e.g. read unchecked) is never corrected without this patch.
	"""
	from frappe.utils import cint

	if not frappe.db.exists("DocType", "Crop Plan"):
		return
	# Same intent as Field Supervisor / Cluster Supervisor / Farm Manager entries for Crop Plan.
	target_ro = {
		"read": 1,
		"write": 0,
		"create": 0,
		"delete": 0,
		"export": 0,
		"print": 1,
		"email": 0,
		"report": 1,
		"share": 0,
	}
	# Align with Crop Plan / System Manager row in doctype JSON (PM is meant to mirror broad access).
	target_pm = {
		"read": 1,
		"write": 1,
		"create": 1,
		"delete": 1,
		"export": 1,
		"print": 1,
		"email": 1,
		"report": 1,
		"share": 1,
	}
	role_targets: list[tuple[str, dict]] = [
		(FIELD_SUPERVISOR_ROLE, target_ro),
		(CLUSTER_SUPERVISOR_ROLE, target_ro),
		(FARM_MANAGER_ROLE, target_ro),
	]
	if frappe.db.exists("Role", PROJECT_MANAGER_ROLE):
		role_targets.append((PROJECT_MANAGER_ROLE, target_pm))
	try:
		dt = frappe.get_doc("DocType", "Crop Plan")
		changed = False
		for role, target in role_targets:
			if not frappe.db.exists("Role", role):
				continue
			found = False
			for p in dt.permissions or []:
				if getattr(p, "role", None) != role:
					continue
				if cint(getattr(p, "permlevel", 0) or 0) != 0:
					continue
				found = True
				for k, v in target.items():
					if cint(getattr(p, k, 0) or 0) != cint(v):
						setattr(p, k, cint(v))
						changed = True
			if not found:
				dt.append("permissions", {"role": role, **target})
				changed = True
		if changed:
			dt.save(ignore_permissions=True)
			frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "f2c patch_crop_plan_docperm_for_f2c_scoped_roles failed")


def ensure_project_manager_doctype_permissions() -> None:
	"""Clone System Manager DocPerm rows onto Project Manager (idempotent).

	Matches spreadsheet intent "all modules / complete access"; grants very broad rights across the site.
	For React-only or reduced Desk power, replace this template with a narrower DocPerm set.
	"""
	from frappe.utils import cint

	if not frappe.db.exists("Role", PROJECT_MANAGER_ROLE):
		return
	if not frappe.db.exists("Role", "System Manager"):
		return
	skip_keys = frozenset(
		{
			"name",
			"creation",
			"modified",
			"modified_by",
			"owner",
			"docstatus",
			"idx",
			"parentfield",
			"parenttype",
			"parent",
			"role",
		}
	)
	sm_rows = frappe.get_all(
		"DocPerm",
		filters={"role": "System Manager"},
		fields="*",
		limit_page_length=0,
		ignore_permissions=True,
	)
	for row in sm_rows or []:
		parent = (row.get("parent") or "").strip()
		if not parent or not frappe.db.exists("DocType", parent):
			continue
		permlevel = cint(row.get("permlevel"))
		if frappe.db.exists(
			"DocPerm",
			{"parent": parent, "role": PROJECT_MANAGER_ROLE, "permlevel": permlevel},
		):
			continue
		perm: dict = {}
		for k, v in row.items():
			if k in skip_keys:
				continue
			perm[k] = v
		perm["role"] = PROJECT_MANAGER_ROLE
		try:
			dt = frappe.get_doc("DocType", parent)
			dt.append("permissions", perm)
			dt.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"f2c Project Manager DocPerm clone failed for {parent!r}",
			)


def after_migrate() -> None:
	"""Hook: run after `bench migrate`."""
	try:
		ensure_irrigation_types()
		seed_water_source()
		ensure_item_group_default_gst_hsn_code_field()
		ensure_default_gst_hsn_code_00000000()
		ensure_equipment_parts_item_groups()
		ensure_equipment_spec_options()
		ensure_f2c_settings()
		ensure_field_supervisor_role()
		ensure_cluster_supervisor_role()
		ensure_driver_role()
		ensure_farm_manager_role()
		ensure_project_manager_role()
		ensure_user_f2c_assigned_field()
		patch_employee_allowed_geo_areas_description()
		run_migrate_user_scope_to_employee()
		ensure_field_supervisor_doctype_permissions()
		ensure_cluster_supervisor_doctype_permissions()
		ensure_driver_doctype_permissions()
		ensure_farm_manager_doctype_permissions()
		ensure_project_manager_doctype_permissions()
		patch_crop_plan_docperm_for_f2c_scoped_roles()
	except Exception:
		# Never block migrations due to seed failures
		frappe.log_error(frappe.get_traceback(), "f2c.after_migrate seed_defaults failed")


def after_install() -> None:
	"""Hook: run after app installation."""
	after_migrate()



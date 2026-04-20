# -*- coding: utf-8 -*-
"""API endpoints for attaching and detaching implements to/from tractors at any location level.

When `_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD` is False (current default), attach/detach
and picker APIs do not block on Crop Plan Schedule / On Demand Activity (Scheduled/Reported).
Set the flag to True to restore CPS/ODA blocking.
"""

from __future__ import annotations

import frappe
from frappe import _

# Statuses that mean equipment is actively in use and cannot be re-linked (used only when guard is on)
_ACTIVE_STATUSES = ("Scheduled", "Reported")

# Temporary: allow attach/detach and modal pickers even when equipment is on CPS/ODA in Scheduled/Reported.
# Schedules may drift from actual field linkage; set True to block again.
_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD = False


def _is_equipment_in_active_activity(
	machinery_name: str | None = None,
	implement_name: str | None = None,
) -> dict:
	"""
	Returns {"in_use": bool, "reason": str | None}.
	When `_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD` is True, checks Crop Plan Schedule and
	On Demand Activity child tables; active = status in ("Scheduled", "Reported").
	"""
	if not _ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD:
		return {"in_use": False, "reason": None}

	asset = None

	if machinery_name:
		asset = frappe.db.get_value("Machinery", machinery_name, "asset")
		label = f"Tractor {machinery_name}"
		machinery_table = "Crop Plan Schedule Machinery"
		oda_table = "On Demand Activity Machinery"
	elif implement_name:
		asset = frappe.db.get_value("Implement", implement_name, "asset")
		label = f"Implement {implement_name}"
		machinery_table = "Crop Plan Schedule Implement"
		oda_table = "On Demand Activity Implement"
	else:
		return {"in_use": False, "reason": None}

	if not asset:
		return {"in_use": False, "reason": None}

	statuses = list(_ACTIVE_STATUSES)

	# Check Crop Plan Schedule
	cps_rows = frappe.db.sql(
		f"""
		SELECT cps.name, cps.status
		FROM `tab{machinery_table}` cst
		INNER JOIN `tabCrop Plan Schedule` cps ON cps.name = cst.parent
		WHERE cst.asset = %s AND cps.status IN %s
		LIMIT 1
		""",
		(asset, tuple(statuses)),
		as_dict=True,
	)
	if cps_rows:
		row = cps_rows[0]
		return {
			"in_use": True,
			"reason": _(
				"{0} is assigned to active Crop Plan Schedule {1} (status: {2}). "
				"Cannot attach or detach while in use."
			).format(label, row["name"], row["status"]),
		}

	# Check On Demand Activity
	oda_rows = frappe.db.sql(
		f"""
		SELECT oda.name, oda.status
		FROM `tab{oda_table}` odt
		INNER JOIN `tabOn Demand Activity` oda ON oda.name = odt.parent
		WHERE odt.asset = %s AND oda.status IN %s
		LIMIT 1
		""",
		(asset, tuple(statuses)),
		as_dict=True,
	)
	if oda_rows:
		row = oda_rows[0]
		return {
			"in_use": True,
			"reason": _(
				"{0} is assigned to active On Demand Activity {1} (status: {2}). "
				"Cannot attach or detach while in use."
			).format(label, row["name"], row["status"]),
		}

	return {"in_use": False, "reason": None}


def _locations_equivalent(loc_a: str | None, loc_b: str | None) -> bool:
	"""Same Location docname or same location_name (case-insensitive)."""
	if not loc_a or not loc_b:
		return False
	if loc_a == loc_b:
		return True
	na = (frappe.db.get_value("Location", loc_a, "location_name") or "").strip().lower()
	nb = (frappe.db.get_value("Location", loc_b, "location_name") or "").strip().lower()
	return bool(na and na == nb)


def _machinery_implement_location_compatible(machinery_name: str, impl_row: dict) -> bool:
	"""Only implements at the same Location as the tractor (field vs field, cluster vs cluster, etc.)."""
	mach_loc = frappe.db.get_value("Machinery", machinery_name, "location")
	if not mach_loc:
		return True
	impl_loc = impl_row.get("location")
	if not impl_loc:
		return False
	return _locations_equivalent(mach_loc, impl_loc)


def _tractor_row_matches_implement_location(implement_name: str, tractor_row: dict) -> bool:
	"""Only tractors at the same Location as the implement when the implement has a Location set."""
	impl_loc = frappe.db.get_value("Implement", implement_name, "location")
	if not impl_loc:
		return True
	mach_loc = tractor_row.get("location")
	if not mach_loc:
		return False
	return _locations_equivalent(mach_loc, impl_loc)


@frappe.whitelist()
def get_available_tractors(implement_name: str | None = None) -> list[dict]:
	"""
	Return Tractors available for attachment.
	A tractor is available when:
	  - machinery_type == "Tractor"
	  - Has no current_implement OR its current_implement == implement_name (already attached)
	  - When `_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD` is True: not on active CPS/ODA
	  - Same Location as the implement when the implement has a Location set
	"""
	if not implement_name:
		return []
	tractors = frappe.get_all(
		"Machinery",
		filters={"machinery_type": "Tractor"},
		fields=["name", "machinery_name", "current_implement", "asset", "location"],
		limit=500,
		ignore_permissions=True,
	)
	result = []
	for t in tractors:
		cur = t.get("current_implement") or None
		# Skip tractors that already have a different implement attached
		if cur and cur != implement_name:
			continue
		if not _tractor_row_matches_implement_location(implement_name, t):
			continue
		# Skip tractors in active activity
		check = _is_equipment_in_active_activity(machinery_name=t["name"])
		if check["in_use"]:
			continue
		# Resolve label for currently attached implement (if any)
		if cur:
			t["current_implement_label"] = (
				frappe.db.get_value("Implement", cur, "implement_name") or cur
			)
		else:
			t["current_implement_label"] = None
		result.append(t)
	return result


@frappe.whitelist()
def get_available_implements(machinery_name: str | None = None) -> list[dict]:
	"""
	Return Implements available for attachment to a tractor.
	An implement is available when:
	  - Has no attached_to_machinery OR its attached_to_machinery == machinery_name
	  - When `_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD` is True: not on active CPS/ODA
	  - Same Location as the tractor (when the tractor has a Location set)
	"""
	if not machinery_name:
		return []
	implements = frappe.get_all(
		"Implement",
		fields=["name", "implement_name", "implement_type", "attached_to_machinery", "asset", "location"],
		limit=500,
		ignore_permissions=True,
	)
	result = []
	for impl in implements:
		cur_tractor = impl.get("attached_to_machinery") or None
		# Skip implements already attached to a different tractor
		if cur_tractor and cur_tractor != machinery_name:
			continue
		if not _machinery_implement_location_compatible(machinery_name, impl):
			continue
		# Skip implements in active activity
		check = _is_equipment_in_active_activity(implement_name=impl["name"])
		if check["in_use"]:
			continue
		if cur_tractor:
			impl["attached_tractor_label"] = (
				frappe.db.get_value("Machinery", cur_tractor, "machinery_name") or cur_tractor
			)
		else:
			impl["attached_tractor_label"] = None
		result.append(impl)
	return result


@frappe.whitelist()
def attach_implement(implement_name: str, machinery_name: str) -> dict:
	"""
	Attach implement_name to machinery_name.
	Validates machinery is a Tractor, optional CPS/ODA guard when
	`_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD` is True, and tractor has no other implement.
	The existing _sync_tractor_attachment on_update hook keeps both sides in sync automatically.
	"""
	# Validate tractor type
	mtype = frappe.db.get_value("Machinery", machinery_name, "machinery_type")
	if (mtype or "").strip() != "Tractor":
		frappe.throw(_("Only Tractor type machinery can have implements attached."))

	# Validate implement exists
	if not frappe.db.exists("Implement", implement_name):
		frappe.throw(_("Implement {0} does not exist.").format(implement_name))

	impl_loc_row = {"location": frappe.db.get_value("Implement", implement_name, "location")}
	if not _machinery_implement_location_compatible(machinery_name, impl_loc_row):
		frappe.throw(
			_("Implement and tractor must be at the same location (warehouse) to attach.")
		)

	# In-use checks
	for check_kwargs in [
		{"implement_name": implement_name},
		{"machinery_name": machinery_name},
	]:
		check = _is_equipment_in_active_activity(**check_kwargs)
		if check["in_use"]:
			frappe.throw(check["reason"])

	# Check tractor doesn't already have a different implement
	current = frappe.db.get_value("Machinery", machinery_name, "current_implement") or None
	if current and current != implement_name:
		current_label = frappe.db.get_value("Implement", current, "implement_name") or current
		frappe.throw(
			_("Tractor already has implement {0} attached. Detach it first.").format(current_label)
		)

	# Perform attach via Machinery doc save — _sync_tractor_attachment hook syncs Implement side
	frappe.flags["skip_cluster_attachment_validation"] = True
	doc = frappe.get_doc("Machinery", machinery_name)
	doc.current_implement = implement_name
	doc.save(ignore_permissions=True)

	impl_label = frappe.db.get_value("Implement", implement_name, "implement_name") or implement_name
	mach_label = frappe.db.get_value("Machinery", machinery_name, "machinery_name") or machinery_name
	return {"ok": True, "message": _("Attached {0} to {1}.").format(impl_label, mach_label)}


@frappe.whitelist()
def detach_implement(implement_name: str | None = None, machinery_name: str | None = None) -> dict:
	"""
	Detach an implement from its tractor. Either side can be passed.
	Optional CPS/ODA guard when `_ENFORCE_IMPLEMENT_ATTACH_ACTIVE_ACTIVITY_GUARD` is True.
	The existing _sync_tractor_attachment on_update hook clears the Implement side automatically.
	"""
	# Resolve the full pair
	if implement_name and not machinery_name:
		machinery_name = frappe.db.get_value("Implement", implement_name, "attached_to_machinery") or None
		if not machinery_name:
			frappe.throw(_("Implement {0} is not currently attached to any tractor.").format(implement_name))
	elif machinery_name and not implement_name:
		implement_name = frappe.db.get_value("Machinery", machinery_name, "current_implement") or None
		if not implement_name:
			frappe.throw(_("Tractor {0} has no implement attached.").format(machinery_name))
	elif not implement_name and not machinery_name:
		frappe.throw(_("Either implement_name or machinery_name is required."))

	# In-use checks for both sides
	for check_kwargs in [
		{"implement_name": implement_name},
		{"machinery_name": machinery_name},
	]:
		check = _is_equipment_in_active_activity(**check_kwargs)
		if check["in_use"]:
			frappe.throw(check["reason"])

	# Clear via Machinery doc save — hook syncs Implement.attached_to_machinery = None
	frappe.flags["skip_cluster_attachment_validation"] = True
	doc = frappe.get_doc("Machinery", machinery_name)
	doc.current_implement = None
	doc.save(ignore_permissions=True)

	impl_label = frappe.db.get_value("Implement", implement_name, "implement_name") or implement_name
	mach_label = frappe.db.get_value("Machinery", machinery_name, "machinery_name") or machinery_name
	return {"ok": True, "message": _("Detached {0} from {1}.").format(impl_label, mach_label)}

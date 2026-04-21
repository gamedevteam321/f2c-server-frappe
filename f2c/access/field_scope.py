# -*- coding: utf-8 -*-
"""Field Supervisor: Geo Fencing scope (farms / clusters / fields) and derived warehouse access."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import frappe

from f2c.access.constants import (
	FIELD_SUPERVISOR_ROLE,
	FULL_ACCESS_USERS,
	USER_ASSIGNED_FIELD_FIELDNAME,
	USER_SCOPE_AREAS_FIELDNAME,
	USER_SCOPE_CHILD_DOCTYPE,
)
from f2c.inventory.warehouse_utils import get_ledger_warehouse, get_ledger_warehouses_for_areas


def user_bypasses_field_supervisor_restrictions(user: str | None = None) -> bool:
	"""Administrator (and similar) keeps full access; no FS scoping or review read-only."""
	u = (user or frappe.session.user or "").strip()
	return u in FULL_ACCESS_USERS


def user_has_field_supervisor_role(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	return FIELD_SUPERVISOR_ROLE in frappe.get_roles(user)


def field_supervisor_data_scope_active(user: str | None = None) -> bool:
	"""True when FS role applies for scoping (not full-access users)."""
	return user_has_field_supervisor_role(user) and not user_bypasses_field_supervisor_restrictions(user)


def _legacy_assigned_field_link(user: str) -> str | None:
	if not frappe.db.has_column("User", USER_ASSIGNED_FIELD_FIELDNAME):
		return None
	val = frappe.db.get_value("User", user, USER_ASSIGNED_FIELD_FIELDNAME)
	return (val or "").strip() or None


def _scope_area_rows_from_db(user: str) -> list[str]:
	if not frappe.db.exists("DocType", USER_SCOPE_CHILD_DOCTYPE):
		return []
	try:
		rows = frappe.get_all(
			USER_SCOPE_CHILD_DOCTYPE,
			filters={"parent": user, "parenttype": "User"},
			fields=["area"],
			limit_page_length=0,
			ignore_permissions=True,
		)
	except Exception:
		return []
	out: list[str] = []
	seen: set[str] = set()
	for r in rows or []:
		a = (r.get("area") or "").strip()
		if a and a not in seen:
			seen.add(a)
			out.append(a)
	return out


def get_user_scope_area_roots(user: str | None = None) -> tuple[str, ...]:
	"""Geo Fencing Area roots from User.f2c_scope_areas plus optional legacy User.f2c_assigned_field."""
	user = (user or frappe.session.user or "").strip()
	if not user or user == "Guest":
		return ()
	ordered: list[str] = []
	seen: set[str] = set()
	for a in _scope_area_rows_from_db(user):
		if a not in seen:
			seen.add(a)
			ordered.append(a)
	legacy = _legacy_assigned_field_link(user)
	if legacy and legacy not in seen:
		seen.add(legacy)
		ordered.append(legacy)
	return tuple(ordered)


def get_assigned_field(user: str | None = None) -> str | None:
	"""First scope root (backward compatible with single-link callers)."""
	roots = get_user_scope_area_roots(user)
	return roots[0] if roots else None


def expand_geo_fencing_descendants(roots: Sequence[str]) -> frozenset[str]:
	"""Each root plus every Geo Fencing Area whose parent chain reaches a root (via parent_area)."""
	names = [str(r).strip() for r in roots if r and str(r).strip()]
	if not names:
		return frozenset()
	seen: set[str] = set(names)
	frontier = list(names)
	# Guard against pathological cycles in master data
	for _ in range(500):
		if not frontier:
			break
		children = frappe.get_all(
			"Geo Fencing Area",
			filters={"parent_area": ["in", frontier]},
			pluck="name",
			limit_page_length=0,
			ignore_permissions=True,
		)
		next_frontier: list[str] = []
		for c in children or []:
			if not c or c in seen:
				continue
			seen.add(c)
			next_frontier.append(c)
		frontier = next_frontier
	return frozenset(seen)


def get_user_scope_expanded_area_names(user: str | None = None) -> frozenset[str]:
	return expand_geo_fencing_descendants(get_user_scope_area_roots(user))


def allowed_warehouse_names_for_area_names(area_names: Sequence[str] | frozenset[str] | set[str] | None) -> frozenset[str]:
	"""Warehouses (raw + ledger) linked to any of the given Geo Fencing Areas."""
	if not area_names:
		return frozenset()
	uniq = [str(a).strip() for a in area_names if a and str(a).strip()]
	if not uniq:
		return frozenset()
	names: set[str] = set()
	try:
		for w in get_ledger_warehouses_for_areas(uniq):
			if w:
				names.add(w)
	except Exception:
		pass
	try:
		for row in frappe.get_all(
			"Geo Fencing Area Warehouse",
			fields=["warehouse"],
			filters={"parent": ["in", uniq]},
			limit_page_length=0,
			ignore_permissions=True,
		):
			wh = (row.get("warehouse") or "").strip()
			if not wh:
				continue
			names.add(wh)
			lw = get_ledger_warehouse(wh)
			if lw:
				names.add(lw)
	except Exception:
		pass
	return frozenset(names)


def allowed_warehouse_names_for_assigned_field(field_name: str | None) -> frozenset[str]:
	"""Backward-compatible single-area helper."""
	if not field_name:
		return frozenset()
	return allowed_warehouse_names_for_area_names([field_name])


def _execution_field(execution_name: str | None) -> str | None:
	if not execution_name:
		return None
	return frappe.db.get_value("Farm Task Execution", execution_name, "field")


def _execution_field_in_scope(doc: Any, allowed_areas: frozenset[str]) -> bool:
	ex = getattr(doc, "farm_task_execution", None) or ""
	if not ex:
		return False
	ef = _execution_field(ex)
	return bool(ef and ef in allowed_areas)


def ltt_external_pickup_action_allowed(doc: Any, allowed_areas: frozenset[str], allowed_wh: frozenset[str]) -> bool:
	"""External pickup: drop-off (destination) must tie to assigned scope (RBAC matrix)."""
	if _execution_field_in_scope(doc, allowed_areas):
		return True
	to_wh = (getattr(doc, "to_warehouse", None) or "").strip()
	return bool(to_wh and to_wh in allowed_wh)


def ltt_external_dropoff_action_allowed(doc: Any, allowed_areas: frozenset[str], allowed_wh: frozenset[str]) -> bool:
	"""External drop-off: pick-up (source) must tie to assigned scope (RBAC matrix)."""
	if _execution_field_in_scope(doc, allowed_areas):
		return True
	from_wh = (getattr(doc, "from_warehouse", None) or "").strip()
	return bool(from_wh and from_wh in allowed_wh)


def ltt_external_any_leg_for_supervisor(doc: Any, allowed_areas: frozenset[str], allowed_wh: frozenset[str]) -> bool:
	"""Whole-ticket actions: either external pickup leg or external drop-off leg matches scope."""
	return ltt_external_pickup_action_allowed(doc, allowed_areas, allowed_wh) or ltt_external_dropoff_action_allowed(
		doc, allowed_areas, allowed_wh
	)


def _fs_scope_areas_and_warehouses() -> tuple[frozenset[str], frozenset[str]]:
	roots = get_user_scope_area_roots()
	if not roots:
		return (frozenset(), frozenset())
	areas = get_user_scope_expanded_area_names()
	wh = allowed_warehouse_names_for_area_names(areas)
	return (areas, wh)


def assert_field_supervisor_ltt_pickup(doc: Any) -> None:
	if not field_supervisor_data_scope_active():
		return
	areas, wh = _fs_scope_areas_and_warehouses()
	if not get_user_scope_area_roots():
		frappe.throw(
			"Field Supervisor must have at least one scope area "
			f"(User.{USER_SCOPE_AREAS_FIELDNAME} or legacy {USER_ASSIGNED_FIELD_FIELDNAME!r})."
		)
	if getattr(doc, "transfer_type", None) != "External":
		frappe.throw("Field Supervisor can only act on External logistics tickets.")
	if not ltt_external_pickup_action_allowed(doc, areas, wh):
		frappe.throw("You are not allowed to perform pickup actions for this ticket.")


def assert_field_supervisor_ltt_dropoff(doc: Any) -> None:
	if not field_supervisor_data_scope_active():
		return
	areas, wh = _fs_scope_areas_and_warehouses()
	if not get_user_scope_area_roots():
		frappe.throw(
			"Field Supervisor must have at least one scope area "
			f"(User.{USER_SCOPE_AREAS_FIELDNAME} or legacy {USER_ASSIGNED_FIELD_FIELDNAME!r})."
		)
	if getattr(doc, "transfer_type", None) != "External":
		frappe.throw("Field Supervisor can only act on External logistics tickets.")
	if not ltt_external_dropoff_action_allowed(doc, areas, wh):
		frappe.throw("You are not allowed to perform drop-off actions for this ticket.")


def assert_field_supervisor_ltt_read(doc: Any) -> None:
	"""For mutating APIs that apply to whole ticket (cancel, report, resolve): require any scoped leg."""
	if not field_supervisor_data_scope_active():
		return
	areas, wh = _fs_scope_areas_and_warehouses()
	if not get_user_scope_area_roots():
		frappe.throw(
			"Field Supervisor must have at least one scope area "
			f"(User.{USER_SCOPE_AREAS_FIELDNAME} or legacy {USER_ASSIGNED_FIELD_FIELDNAME!r})."
		)
	if getattr(doc, "transfer_type", None) != "External":
		frappe.throw("Field Supervisor can only act on External logistics tickets.")
	if not ltt_external_any_leg_for_supervisor(doc, areas, wh):
		frappe.throw("You are not allowed to modify this logistics ticket.")


def raise_if_field_supervisor_blocked_in_review(execution_doc: Any) -> None:
	"""Block review-stage mutations (approve, return types, return tickets) for Field Supervisor."""
	if not field_supervisor_data_scope_active():
		return
	st = (getattr(execution_doc, "status", None) or "").strip()
	if st == "In Review":
		frappe.throw("Field Supervisor has read-only access while the execution is In Review.")


def assert_execution_in_field_scope(execution_name: str) -> None:
	if not field_supervisor_data_scope_active():
		return
	if not get_user_scope_area_roots():
		frappe.throw(
			"Field Supervisor must have at least one scope area "
			f"(User.{USER_SCOPE_AREAS_FIELDNAME} or legacy {USER_ASSIGNED_FIELD_FIELDNAME!r})."
		)
	allowed = get_user_scope_expanded_area_names()
	field = frappe.db.get_value("Farm Task Execution", execution_name, "field")
	if field not in allowed:
		frappe.throw("This execution is outside your assigned scope.")

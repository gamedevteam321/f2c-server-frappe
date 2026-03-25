# Copyright (c) 2025, Orgatek and contributors
# List users whose Role Profile is Driver or Security Guard (Farm Employee page). Uses ignore_permissions so any logged-in user can load the list.

import frappe
from frappe.utils import cint

FARM_EMPLOYEE_ROLE_PROFILES = ("Driver", "Security Guard")


def _role_profile_matches(role_profile):
	if not role_profile:
		return False
	r = (role_profile or "").strip().lower()
	allowed = [p.lower() for p in FARM_EMPLOYEE_ROLE_PROFILES]
	return r in allowed or any(a in r for a in allowed)


def _normalize_list(val):
	"""Accept list/tuple/JSON string/None and return list[str]."""
	if val is None:
		return None
	if isinstance(val, (list, tuple)):
		return [str(x).strip() for x in val if str(x).strip()]
	if isinstance(val, str):
		s = val.strip()
		if not s:
			return []
		# Try JSON list
		try:
			import json
			parsed = json.loads(s)
			if isinstance(parsed, list):
				return [str(x).strip() for x in parsed if str(x).strip()]
		except Exception:
			pass
		# Fallback: comma-separated
		return [p.strip() for p in s.split(",") if p.strip()]
	return [str(val).strip()] if str(val).strip() else []


def _matches_selected(role_profile, selected_profiles):
	if not role_profile:
		return False
	rp = (role_profile or "").strip().lower()
	selected = [(p or "").strip().lower() for p in (selected_profiles or []) if (p or "").strip()]
	return rp in selected


@frappe.whitelist()
def get_farm_employee_role_profiles():
	"""
	Return Role Profile names to choose from.
	Prefer the `Role Profile` doctype (all configured role profiles),
	fallback to distinct values used by User.role_profile_name.
	Used by Farm Employee page filter dropdown.
	"""
	# Preferred: fetch all Role Profiles (even if currently not used by any user)
	try:
		if frappe.db.exists("DocType", "Role Profile"):
			role_profiles = frappe.get_all(
				"Role Profile",
				fields=["name"],
				limit=5000,
				ignore_permissions=True,
				order_by="name asc",
			) or []
			out = [r.get("name") for r in role_profiles if (r.get("name") or "").strip()]
			if out:
				return out
	except Exception:
		# Fall back to user-derived list below
		pass

	rows = frappe.get_all(
		"User",
		fields=["role_profile_name"],
		limit=5000,
		ignore_permissions=True,
	)
	values = []
	for r in (rows or []):
		v = (r.get("role_profile_name") or "").strip()
		if v:
			values.append(v)
	# De-dupe (case-insensitive, preserve original casing of first occurrence)
	seen = set()
	out = []
	for v in values:
		k = v.lower()
		if k in seen:
			continue
		seen.add(k)
		out.append(v)
	out.sort(key=lambda x: x.lower())
	return out


@frappe.whitelist()
def get_farm_employees(role_profiles=None, include_no_role_profile: int = 0):
	"""
	Fetch users from User doctype filtered by Role Profile (User.role_profile_name).

	- If role_profiles is NOT provided: defaults to Driver + Security Guard (backward compatible)
	- If role_profiles is provided as []: return all users with ANY role_profile_name (unless include_no_role_profile=1)
	- If role_profiles is provided as list: return only those role profiles

	Returns list of { name, full_name, gender, birth_date, location, role_profile, roles }.
	"""
	role_profiles_list = _normalize_list(role_profiles)
	include_no_role_profile = bool(cint(include_no_role_profile))

	all_users = frappe.get_all(
		"User",
		fields=["name", "full_name", "gender", "birth_date", "location", "role_profile_name"],
		limit=2000,
		ignore_permissions=True,
	)

	if role_profiles_list is None:
		# Backward compatible behavior
		users = [u for u in all_users if _role_profile_matches(u.get("role_profile_name"))]
	elif len(role_profiles_list) == 0:
		# "All": any role_profile_name (or include missing when requested)
		if include_no_role_profile:
			users = list(all_users or [])
		else:
			users = [u for u in (all_users or []) if (u.get("role_profile_name") or "").strip()]
	else:
		users = [u for u in (all_users or []) if _matches_selected(u.get("role_profile_name"), role_profiles_list)]

	if not users:
		return []

	user_ids = [u["name"] for u in users]
	role_rows = frappe.get_all(
		"User Role",
		filters={"parent": ["in", user_ids]},
		fields=["parent", "role"],
		limit=10000,
		ignore_permissions=True,
	)
	roles_by_user = {}
	for r in role_rows:
		roles_by_user.setdefault(r["parent"], []).append(r["role"])
	for k in roles_by_user:
		roles_by_user[k] = list(dict.fromkeys(roles_by_user[k]))

	users.sort(key=lambda u: (u.get("full_name") or u.get("name") or "").lower())
	result = []
	for u in users:
		result.append({
			"name": u.get("name"),
			"full_name": u.get("full_name") or u.get("name"),
			"gender": u.get("gender"),
			"birth_date": u.get("birth_date"),
			"location": u.get("location"),
			"role_profile": u.get("role_profile_name"),
			"roles": roles_by_user.get(u["name"], []),
		})
	return result

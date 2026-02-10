# Copyright (c) 2025, Orgatek and contributors
# List users whose Role Profile is Driver or Security Guard (Farm Employee page). Uses ignore_permissions so any logged-in user can load the list.

import frappe

FARM_EMPLOYEE_ROLE_PROFILES = ("Driver", "Security Guard")


def _role_profile_matches(role_profile):
	if not role_profile:
		return False
	r = (role_profile or "").strip().lower()
	allowed = [p.lower() for p in FARM_EMPLOYEE_ROLE_PROFILES]
	return r in allowed or any(a in r for a in allowed)


@frappe.whitelist()
def get_farm_employees():
	"""
	Fetch users from User doctype where role_profile_name is Driver or Security Guard only.
	Returns list of { name, full_name, gender, birth_date, location, role_profile, roles }.
	"""
	all_users = frappe.get_all(
		"User",
		fields=["name", "full_name", "gender", "birth_date", "location", "role_profile_name"],
		limit=2000,
		ignore_permissions=True,
	)
	# Only users whose Role Profile is Driver or Security Guard
	users = [u for u in all_users if _role_profile_matches(u.get("role_profile_name"))]
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

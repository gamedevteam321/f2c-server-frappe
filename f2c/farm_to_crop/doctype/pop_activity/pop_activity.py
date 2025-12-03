# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class POPActivity(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_pop_activity_list_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show descriptive name in POP-Activity List dropdown, filtered by Crop."""
	
	# Basic search (by name, crop stage, and activity name)
	where_conditions = [
		f"(pal.{searchfield} LIKE %(txt)s OR pal.crop_stage_name LIKE %(txt)s OR pal.activity_name LIKE %(txt)s)"
	]
	
	params = {
		"txt": f"%{txt}%",
		"start": start,
		"page_len": page_len,
	}
	
	# Filter by Crop if crop name is provided in filters
	if filters and filters.get("crop_name"):
		where_conditions.append("pal.crop = %(crop)s")
		params["crop"] = filters.get("crop_name")
	
	where_clause = " AND ".join(where_conditions)
	
	return frappe.db.sql(
		f"""
		SELECT 
			pal.name,
			CONCAT(
				COALESCE(pal.crop_stage_name, ''),
				CASE 
					WHEN pal.crop_stage_name IS NOT NULL AND pal.activity_name IS NOT NULL THEN ' - '
					ELSE ''
				END,
				COALESCE(pal.activity_name, ''),
				' (', pal.name, ')'
			) as description
		FROM `tabPOP-Activity List` pal
		WHERE {where_clause}
		ORDER BY pal.crop_stage_name, pal.activity_name
		LIMIT %(start)s, %(page_len)s
		""",
		params,
	)


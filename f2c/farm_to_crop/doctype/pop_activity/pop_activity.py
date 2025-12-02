# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class POPActivity(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_pop_activity_list_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show descriptive name in POP-Activity List dropdown, filtered by crop type"""
	
	# Build the WHERE clause with crop type filter if provided
	where_conditions = [
		f"({searchfield} LIKE %(txt)s OR activity_type_name LIKE %(txt)s OR trigger_event_name LIKE %(txt)s)"
	]
	
	params = {
		'txt': f'%{txt}%',
		'start': start,
		'page_len': page_len
	}
	
	# Filter by crop type if crop name is provided in filters
	if filters and filters.get('crop_name'):
		# Get crop type from crop
		crop_type = frappe.db.get_value('Crop', filters.get('crop_name'), 'crop_type')
		if crop_type:
			# Filter by crop_type field in POP-Activity List
			where_conditions.append("pal.crop_type = %(crop_type)s")
			params['crop_type'] = crop_type
	
	where_clause = " AND ".join(where_conditions)
	
	return frappe.db.sql(f"""
		SELECT 
			pal.name,
			CONCAT(
				COALESCE(pal.activity_type_name, ''),
				CASE 
					WHEN pal.activity_type_name IS NOT NULL AND pal.trigger_event_name IS NOT NULL THEN ' - '
					ELSE ''
				END,
				COALESCE(pal.trigger_event_name, ''),
				' (', pal.name, ')'
			) as description
		FROM `tabPOP-Activity List` pal
		WHERE {where_clause}
		ORDER BY pal.activity_type_name, pal.trigger_event_name
		LIMIT %(start)s, %(page_len)s
	""", params)


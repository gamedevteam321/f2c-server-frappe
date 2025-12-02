# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class POPActivityList(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_crop_stage_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show stage name in Crop Stage dropdown"""
	return frappe.db.sql("""
		SELECT 
			name,
			CONCAT(stage, ' (', name, ')') as description
		FROM `tabCrop Stage`
		WHERE 
			{key} LIKE %(txt)s
			OR stage LIKE %(txt)s
		ORDER BY stage
		LIMIT %(start)s, %(page_len)s
	""".format(key=searchfield), {
		'txt': f'%{txt}%',
		'start': start,
		'page_len': page_len
	})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_trigger_event_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show event name in Trigger Event dropdown"""
	return frappe.db.sql("""
		SELECT 
			name,
			CONCAT(event_name, ' (', name, ')') as description
		FROM `tabTrigger Event`
		WHERE 
			{key} LIKE %(txt)s
			OR event_name LIKE %(txt)s
		ORDER BY event_name
		LIMIT %(start)s, %(page_len)s
	""".format(key=searchfield), {
		'txt': f'%{txt}%',
		'start': start,
		'page_len': page_len
	})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_activity_type_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show activity name in Activity Type dropdown"""
	return frappe.db.sql("""
		SELECT 
			name,
			CONCAT(activity_name, ' (', name, ')') as description
		FROM `tabFarm Activity`
		WHERE 
			{key} LIKE %(txt)s
			OR activity_name LIKE %(txt)s
		ORDER BY activity_name
		LIMIT %(start)s, %(page_len)s
	""".format(key=searchfield), {
		'txt': f'%{txt}%',
		'start': start,
		'page_len': page_len
	})


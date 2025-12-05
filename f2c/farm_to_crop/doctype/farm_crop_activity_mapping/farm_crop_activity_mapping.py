# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FarmCropActivityMapping(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_crop_stage_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show stage name in Crop Stage dropdown"""
	return frappe.db.sql(
		"""
		SELECT 
			name,
			CONCAT(stage, ' (', name, ')') as description
		FROM `tabCrop Stage`
		WHERE 
			{key} LIKE %(txt)s
			OR stage LIKE %(txt)s
		ORDER BY stage
		LIMIT %(start)s, %(page_len)s
		""".format(
			key=searchfield
		),
		{
			"txt": f"%{txt}%",
			"start": start,
			"page_len": page_len,
		},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_activity_query(doctype, txt, searchfield, start, page_len, filters):
	"""Custom query to show activity_name in Activity dropdown, filtered by Activity Group Type."""

	where_conditions = [
		f"(fa.{searchfield} LIKE %(txt)s OR fa.activity_name LIKE %(txt)s)"
	]

	params = {
		"txt": f"%{txt}%",
		"start": start,
		"page_len": page_len,
	}

	if filters and filters.get("activity_group_type"):
		where_conditions.append("fa.activity_group_type = %(activity_group_type)s")
		params["activity_group_type"] = filters.get("activity_group_type")

	where_clause = " AND ".join(where_conditions)

	return frappe.db.sql(
		f"""
		SELECT 
			fa.name,
			CONCAT(fa.activity_name, ' (', fa.name, ')') as description
		FROM `tabFarm Activity` fa
		WHERE {where_clause}
		ORDER BY fa.activity_name
		LIMIT %(start)s, %(page_len)s
		""",
		params,
	)


@frappe.whitelist()
def get_activity_tasks_and_items(activity: str) -> list[dict]:
    """Return flattened list of all Farm Tasks and their items for a given Farm Activity.

    Each row in the result corresponds to one item within one task, including:
    - farm_activity_task (child row name from Farm Activity Task)
    - farm_task (Farm Tasks document name)
    - task_name
    - item, item_name
    - quantity, unit
    """
    if not activity:
        return []

    result: list[dict] = []

    try:
        activity_doc = frappe.get_doc("Farm Activity", activity)
    except frappe.DoesNotExistError:
        return []

    # activity_doc.farm_tasks is the child table of type Farm Activity Task
    for activity_task in activity_doc.get("farm_tasks", []):
        farm_task_name = activity_task.get("farm_task")
        if not farm_task_name:
            continue

        try:
            farm_task_doc = frappe.get_doc("Farm Tasks", farm_task_name)
        except frappe.DoesNotExistError:
            continue

        task_name = farm_task_doc.get("task_name")

        # farm_task_doc.items is the child table of type Farm Task Item
        for item_row in farm_task_doc.get("items", []):
            result.append(
                {
                    "farm_activity_task": activity_task.name,
                    "farm_task": farm_task_name,
                    "task_name": task_name,
                    "item": item_row.get("item"),
                    "item_name": item_row.get("item_name"),
                    "quantity": item_row.get("quantity"),
                    "unit": item_row.get("unit"),
                }
            )

    return result


@frappe.whitelist()
def get_available_tasks(activity: str) -> list[dict]:
    """Return list of available tasks for a given Farm Activity.
    
    Each row includes:
    - farm_activity_task (child row name from Farm Activity Task)
    - farm_task (Farm Tasks document name)
    - task_name
    - item_count (number of items in the task)
    """
    if not activity:
        return []
    
    result: list[dict] = []
    
    try:
        activity_doc = frappe.get_doc("Farm Activity", activity)
    except frappe.DoesNotExistError:
        return []
    
    # activity_doc.farm_tasks is the child table of type Farm Activity Task
    for activity_task in activity_doc.get("farm_tasks", []):
        farm_task_name = activity_task.get("farm_task")
        if not farm_task_name:
            continue
        
        try:
            farm_task_doc = frappe.get_doc("Farm Tasks", farm_task_name)
        except frappe.DoesNotExistError:
            continue
        
        task_name = farm_task_doc.get("task_name")
        item_count = len(farm_task_doc.get("items", []))
        
        result.append({
            "farm_activity_task": activity_task.name,
            "farm_task": farm_task_name,
            "task_name": task_name,
            "item_count": item_count
        })
    
    return result


@frappe.whitelist()
def get_task_items(farm_activity_task: str, farm_task: str) -> list[dict]:
    """Return all items for a specific Farm Task.
    
    Each row includes:
    - farm_activity_task
    - farm_task
    - task_name
    - item, item_name
    - quantity, unit
    """
    if not farm_task:
        return []
    
    result: list[dict] = []
    
    try:
        farm_task_doc = frappe.get_doc("Farm Tasks", farm_task)
    except frappe.DoesNotExistError:
        return []
    
    task_name = farm_task_doc.get("task_name")
    
    # farm_task_doc.items is the child table of type Farm Task Item
    for item_row in farm_task_doc.get("items", []):
        result.append({
            "farm_activity_task": farm_activity_task,
            "farm_task": farm_task,
            "task_name": task_name,
            "item": item_row.get("item"),
            "item_name": item_row.get("item_name"),
            "quantity": item_row.get("quantity"),
            "unit": item_row.get("unit"),
        })

    return result


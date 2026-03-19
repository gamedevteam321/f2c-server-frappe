# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Crop(Document):
	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_crop_stage_query(doctype, txt, searchfield, start, page_len, filters):
	"""
	Custom query for Crop Stage Link field to show descriptive stage name.
	"""
	return frappe.db.sql(
		"""
		SELECT
			cs.name,
			cs.stage
		FROM `tabCrop Stage` cs
		WHERE
			(cs.name LIKE %(txt)s OR cs.stage LIKE %(txt)s)
		ORDER BY cs.stage
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"txt": f"%{txt}%",
			"start": start,
			"page_len": page_len
		}
	)


@frappe.whitelist()
def get_crop_stages_by_sequence(crop: str) -> list[dict]:
	"""
	Fetch crop stages for a given crop ordered by sequence.
	
	Args:
		crop: Name of the crop
		
	Returns:
		List of crop stages with their sequence numbers
	"""
	if not crop:
		return []
	
	stages = frappe.get_all(
		"Crop Stage Mapping",
		filters={"parent": crop},
		fields=["crop_stage", "sequence"],
		order_by="sequence asc"
	)
	
	result = []
	for stage in stages:
		try:
			stage_doc = frappe.get_doc("Crop Stage", stage.crop_stage)
			result.append({
				"name": stage.crop_stage,
				"stage": stage_doc.stage,
				"sequence": stage.sequence
			})
		except Exception as e:
			frappe.log_error(f"Error fetching crop stage {stage.crop_stage}: {str(e)}")
			continue
	
	return result


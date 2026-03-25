// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Crop", {
	refresh(frm) {
		// Set up the query for crop_stage in child table to show descriptive names
		frm.fields_dict.crop_stages.grid.get_field("crop_stage").get_query = function() {
			return {
				query: "f2c.farm_to_crop.doctype.crop.crop.get_crop_stage_query"
			};
		};
	}
});

// Child table events for Crop Stage Mapping
frappe.ui.form.on("Crop Stage Mapping", {
	crop_stages_add(frm, cdt, cdn) {
		// Auto-increment sequence when a new row is added
		const row = locals[cdt][cdn];
		const existing_rows = frm.doc.crop_stages || [];
		
		// Find the maximum sequence value
		let max_sequence = 0;
		existing_rows.forEach(r => {
			if (r.sequence && r.sequence > max_sequence) {
				max_sequence = r.sequence;
			}
		});
		
		// Set the new row's sequence to max + 1
		frappe.model.set_value(cdt, cdn, 'sequence', max_sequence + 1);
	}
});


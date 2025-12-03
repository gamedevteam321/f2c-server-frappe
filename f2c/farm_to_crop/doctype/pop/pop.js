// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on('POP', {
	refresh: function(frm) {
		// Customize POP-Activity List Link field in child table to show descriptive name
		setup_pop_activity_list_query(frm);
	},
	
	crop: function(frm) {
		// Refresh the child table when crop changes to update available options
		setup_pop_activity_list_query(frm);
		if (frm.fields_dict.activities) {
			frm.fields_dict.activities.refresh();
		}
	}
});

function setup_pop_activity_list_query(frm) {
	if (frm.fields_dict.activities) {
		frm.fields_dict.activities.grid.get_field('pop_activity_list').get_query = function(doc, cdt, cdn) {
			let filters = {};
			
			// Filter by crop type if crop is selected
			// We'll pass the crop name and let the server-side query get the crop_type
			if (frm.doc.crop) {
				filters['crop_name'] = frm.doc.crop;
			}
			
			return {
				filters: filters,
				query: "f2c.farm_to_crop.doctype.pop_activity.pop_activity.get_pop_activity_list_query"
			};
		};
	}
}

// Child table: keep Activity Name in sync when POP-Activity List is selected
frappe.ui.form.on('POP Activity', {
	pop_activity_list: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.pop_activity_list) {
			frappe.model.set_value(cdt, cdn, 'activity_name', null);
			return;
		}

		// First try to read cached activity_name from POP-Activity List.
		// If it's missing (older records), fall back to Farm Activity.activity_name.
		frappe.db.get_value('POP-Activity List', row.pop_activity_list, ['activity', 'activity_name'])
			.then(r => {
				const data = r && r.message ? r.message : {};
				if (data.activity_name) {
					frappe.model.set_value(cdt, cdn, 'activity_name', data.activity_name);
				} else if (data.activity) {
					frappe.db.get_value('Farm Activity', data.activity, 'activity_name')
						.then(r2 => {
							const name = r2 && r2.message ? r2.message.activity_name : '';
							frappe.model.set_value(cdt, cdn, 'activity_name', name);
						});
				} else {
					frappe.model.set_value(cdt, cdn, 'activity_name', '');
				}
			});
	}
});


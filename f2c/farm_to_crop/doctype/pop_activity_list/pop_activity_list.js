// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on('POP-Activity List', {
	refresh: function(frm) {
		// Customize Crop Stage Link field to show stage name
		if (frm.fields_dict.crop_stage) {
			frm.fields_dict.crop_stage.get_query = function(doc) {
				return {
					filters: {},
					query: "f2c.farm_to_crop.doctype.pop_activity_list.pop_activity_list.get_crop_stage_query"
				};
			};
		}

		// Customize Trigger Event Link field to show event name
		if (frm.fields_dict.trigger_event) {
			frm.fields_dict.trigger_event.get_query = function(doc) {
				return {
					filters: {},
					query: "f2c.farm_to_crop.doctype.pop_activity_list.pop_activity_list.get_trigger_event_query"
				};
			};
		}

		// Customize Activity Type Link field to show activity name
		if (frm.fields_dict.activity_type) {
			frm.fields_dict.activity_type.get_query = function(doc) {
				return {
					filters: {},
					query: "f2c.farm_to_crop.doctype.pop_activity_list.pop_activity_list.get_activity_type_query"
				};
			};
		}
	}
});

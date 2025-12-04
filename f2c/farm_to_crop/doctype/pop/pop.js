// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on('POP', {
	refresh: function(frm) {
		// Customize Farm Crop Activity Mapping Link field in child table to show descriptive name
		setup_pop_activity_list_query(frm);
		
		// Add custom button to add activities via modal
		setup_custom_activity_button(frm);
		
		// Render activities in custom table format
		render_activities_table(frm);
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

function setup_custom_activity_button(frm) {
	// Hide the default grid add button and add custom button
	if (frm.fields_dict.activities && frm.fields_dict.activities.grid) {
		// Add custom "Add Activity" button before the activities section
		if (!frm.custom_activity_button_added) {
			frm.fields_dict.activities.grid.wrapper.find('.grid-add-row').hide();
			
			// Add custom button
			let $btn_wrapper = frm.fields_dict.activities.wrapper.find('.form-section-heading');
			if ($btn_wrapper.length && !$btn_wrapper.find('.custom-add-activity-btn').length) {
				$btn_wrapper.append(`
					<button class="btn btn-sm btn-primary custom-add-activity-btn" style="margin-left: 10px;">
						<svg class="icon icon-sm" style=""><use href="#icon-add"></use></svg>
						Add Activity
					</button>
				`);
				
				$btn_wrapper.find('.custom-add-activity-btn').on('click', function() {
					open_activity_modal(frm);
				});
			}
			
			frm.custom_activity_button_added = true;
		}
	}
}

function render_activities_table(frm) {
	// Custom rendering of activities in a more user-friendly table format
	if (!frm.fields_dict.activities) return;
	
	let activities = frm.doc.activities || [];
	
	// Build custom HTML table
	let html = `
		<div class="custom-activities-table" style="margin-top: 15px;">
			<table class="table table-bordered" style="margin-bottom: 0;">
				<thead>
					<tr>
						<th style="width: 10%;">Sequence</th>
						<th style="width: 25%;">Stage</th>
						<th style="width: 30%;">Activity</th>
						<th style="width: 15%;">Duration</th>
						<th style="width: 10%;">DAT</th>
						<th style="width: 10%;">Actions</th>
					</tr>
				</thead>
				<tbody>
	`;
	
	if (activities.length === 0) {
		html += `
			<tr>
				<td colspan="6" style="text-align: center; padding: 20px; color: #888;">
					No activities added yet. Click "Add Activity" to start.
				</td>
			</tr>
		`;
	} else {
		activities.forEach((activity, idx) => {
			// Fetch activity details
			frappe.db.get_value('Farm Crop Activity Mapping', activity.pop_activity_list, 
				['crop_stage_name', 'duration_after_stage', 'is_dat', 'dat'])
				.then(r => {
					let data = r && r.message ? r.message : {};
					let row_html = `
						<tr data-idx="${idx}">
							<td>${activity.sequence || idx + 1}</td>
							<td>${data.crop_stage_name || '—'}</td>
							<td>${activity.activity_name || '—'}</td>
							<td>${data.duration_after_stage || '—'}</td>
							<td>${data.is_dat ? (data.dat || '—') : '—'}</td>
							<td>
								<button class="btn btn-xs btn-default edit-activity-btn" data-idx="${idx}">
									Edit
								</button>
								<button class="btn btn-xs btn-danger remove-activity-btn" data-idx="${idx}" style="margin-left: 5px;">
									Remove
								</button>
							</td>
						</tr>
					`;
					
					// Update or append row
					let $table = frm.fields_dict.activities.wrapper.find('.custom-activities-table tbody');
					let $existing_row = $table.find(`tr[data-idx="${idx}"]`);
					if ($existing_row.length) {
						$existing_row.replaceWith(row_html);
					} else {
						$table.append(row_html);
					}
					
					// Bind events
					bind_activity_row_events(frm);
				});
		});
	}
	
	html += `
				</tbody>
			</table>
		</div>
	`;
	
	// Insert or update the custom table
	let $wrapper = frm.fields_dict.activities.wrapper;
	let $existing_table = $wrapper.find('.custom-activities-table');
	if ($existing_table.length) {
		$existing_table.replaceWith(html);
	} else {
		$wrapper.append(html);
	}
	
	// Hide the default grid
	frm.fields_dict.activities.grid.wrapper.hide();
}

function bind_activity_row_events(frm) {
	// Bind edit and remove buttons
	frm.fields_dict.activities.wrapper.find('.edit-activity-btn').off('click').on('click', function() {
		let idx = $(this).data('idx');
		edit_activity_modal(frm, idx);
	});
	
	frm.fields_dict.activities.wrapper.find('.remove-activity-btn').off('click').on('click', function() {
		let idx = $(this).data('idx');
		frappe.confirm('Are you sure you want to remove this activity?', () => {
			frm.doc.activities.splice(idx, 1);
			frm.refresh_field('activities');
			render_activities_table(frm);
		});
	});
}

function open_activity_modal(frm, edit_idx = null) {
	if (!frm.doc.crop) {
		frappe.msgprint('Please select a Crop first');
		return;
	}
	
	let activity_data = {};
	if (edit_idx !== null && frm.doc.activities[edit_idx]) {
		let activity = frm.doc.activities[edit_idx];
		// Fetch full details
		frappe.db.get_value('POP-Activity List', activity.pop_activity_list,
			['crop_stage', 'duration_after_stage', 'is_dat', 'dat', 'activity_group_type', 'activity', 'remarks'])
			.then(r => {
				activity_data = r && r.message ? r.message : {};
				show_activity_dialog(frm, activity_data, edit_idx);
			});
	} else {
		show_activity_dialog(frm, activity_data, edit_idx);
	}
}

function edit_activity_modal(frm, idx) {
	open_activity_modal(frm, idx);
}

function show_activity_dialog(frm, activity_data, edit_idx) {
	let d = new frappe.ui.Dialog({
		title: edit_idx !== null ? 'Edit Activity' : 'Add Activity',
		fields: [
			{
				fieldname: 'section_crop',
				fieldtype: 'Section Break',
				label: 'Crop Information'
			},
			{
				fieldname: 'crop_stage',
				fieldtype: 'Link',
				label: 'Crop Stage',
				options: 'Crop Stage',
				reqd: 1,
				default: activity_data.crop_stage || ''
			},
			{
				fieldname: 'duration_after_stage',
				fieldtype: 'Int',
				label: 'Duration After Stage (days)',
				default: activity_data.duration_after_stage || 0
			},
			{
				fieldname: 'column_break_1',
				fieldtype: 'Column Break'
			},
			{
				fieldname: 'is_dat',
				fieldtype: 'Check',
				label: 'Is DAT',
				default: activity_data.is_dat || 0
			},
			{
				fieldname: 'dat',
				fieldtype: 'Int',
				label: 'DAT',
				depends_on: 'eval:doc.is_dat==1',
				default: activity_data.dat || 0
			},
			{
				fieldname: 'section_activity',
				fieldtype: 'Section Break',
				label: 'Activity Details'
			},
			{
				fieldname: 'activity_group_type',
				fieldtype: 'Link',
				label: 'Activity Group Type',
				options: 'Activity Group Type',
				reqd: 1,
				default: activity_data.activity_group_type || ''
			},
			{
				fieldname: 'column_break_2',
				fieldtype: 'Column Break'
			},
			{
				fieldname: 'activity',
				fieldtype: 'Link',
				label: 'Activity',
				options: 'Farm Activity',
				reqd: 1,
				default: activity_data.activity || '',
				get_query: function() {
					let group = d.get_value('activity_group_type');
					if (group) {
						return {
							filters: {
								'activity_group_type': group
							}
						};
					}
					return {};
				}
			},
			{
				fieldname: 'category',
				fieldtype: 'Select',
				label: 'Category',
				options: 'Compulsory\nOn Demand',
				reqd: 1,
				default: activity_data.category || 'Compulsory'
			},
			{
				fieldname: 'section_remarks',
				fieldtype: 'Section Break',
				label: 'Additional Information'
			},
			{
				fieldname: 'remarks',
				fieldtype: 'Text Editor',
				label: 'Remarks',
				default: activity_data.remarks || ''
			}
		],
		primary_action_label: edit_idx !== null ? 'Update' : 'Add',
		primary_action: function(values) {
			// Create or update Farm Crop Activity Mapping
			frappe.call({
				method: 'frappe.client.insert',
				args: {
					doc: {
						doctype: 'Farm Crop Activity Mapping',
						crop: frm.doc.crop,
						crop_stage: values.crop_stage,
						duration_after_stage: values.duration_after_stage,
						is_dat: values.is_dat ? 1 : 0,
						dat: values.dat,
						activity_group_type: values.activity_group_type,
						activity: values.activity,
						category: values.category,
						remarks: values.remarks
					}
				},
				callback: function(r) {
					if (r.message) {
						let pop_activity_list = r.message.name;
						let activity_name = r.message.activity_name;
						
						if (edit_idx !== null) {
							// Update existing
							frm.doc.activities[edit_idx].pop_activity_list = pop_activity_list;
							frm.doc.activities[edit_idx].activity_name = activity_name;
						} else {
							// Add new
							let new_row = frm.add_child('activities');
							new_row.pop_activity_list = pop_activity_list;
							new_row.activity_name = activity_name;
							new_row.sequence = frm.doc.activities.length;
						}
						
						frm.refresh_field('activities');
						render_activities_table(frm);
						d.hide();
					}
				}
			});
		}
	});
	
	// Update activity options when activity group changes
	d.fields_dict.activity_group_type.$input.on('change', function() {
		d.set_value('activity', '');
	});
	
	d.show();
}

// Child table: keep Activity Name in sync when Farm Crop Activity Mapping is selected
frappe.ui.form.on('POP Activity', {
	pop_activity_list: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.pop_activity_list) {
			frappe.model.set_value(cdt, cdn, 'activity_name', null);
			return;
		}

		// First try to read cached activity_name from Farm Crop Activity Mapping.
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


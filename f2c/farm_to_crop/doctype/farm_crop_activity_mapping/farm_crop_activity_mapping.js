// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Farm Crop Activity Mapping", {
	refresh(frm) {
		// Customize Crop Stage Link field to show stage name via custom query
		if (frm.fields_dict.crop_stage) {
			frm.fields_dict.crop_stage.get_query = function () {
				return {
					filters: {},
					query:
						"f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_crop_stage_query",
				};
			};
		}

		// Filter Activity by selected Activity Group Type & show descriptive labels
		frm.set_query("activity", function (doc) {
			const filters = {};
			if (doc.activity_group_type) {
				filters.activity_group_type = doc.activity_group_type;
			}
				return {
				filters,
				query:
					"f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_activity_query",
				};
		});

		// Initial render of grouped approved input mixes view
		render_tasks_items_view(frm);
	},

	activity_group_type(frm) {
		// When Activity Group changes, clear Activity and approved input mixes table
		if (frm.doc.activity) {
			frm.set_value("activity", null);
		}
		frm.clear_table("tasks_items");
		frm.refresh_field("tasks_items");
	},

	activity(frm) {
		// When Activity changes, clear the approved input mixes table
		// User will manually add approved input mixes using the "Add Approved Input Mix" button
		if (!frm.doc.activity) {
			frm.clear_table("tasks_items");
			frm.refresh_field("tasks_items");
			render_tasks_items_view(frm);
		}
		
		// Refresh to show/hide the "Add Approved Input Mix" button
		frm.trigger('refresh');
	},

	is_dat(frm) {
		// When Is DAT is unchecked, clear DAT value
		if (!frm.doc.is_dat) {
			frm.set_value("dat", null);
		}
	},
});

/**
 * Render approved input mixes grouped by mix name into the HTML field
 * so that the user sees:
 *   Approved Input Mix Name
 *     Approved Input | Quantity | Unit
 */
function render_tasks_items_view(frm) {
	const wrapper = frm.fields_dict.tasks_items_view
		? frm.fields_dict.tasks_items_view.$wrapper
		: null;

	if (!wrapper) return;

	const rows = frm.doc.tasks_items || [];
	
	// If no approved input mixes added yet, check if activity has mixes available
	if (!rows.length) {
		if (!frm.doc.activity) {
			wrapper.html("<p class=\"text-muted\">Please select an activity first.</p>");
			return;
		}
		
		// Check if activity has approved input mixes
		frappe.call({
			method: 'f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_available_tasks',
			args: {
				activity: frm.doc.activity
			},
			callback: function(r) {
				if (!r.message || r.message.length === 0) {
					wrapper.html("<p class=\"text-muted\">No approved input mixes mapped with this activity.</p>");
				} else {
					wrapper.html(`
						<p class="text-muted">No approved input mixes added yet.</p>
						<button class="btn btn-sm btn-primary add-task-inline-btn">
							<i class="fa fa-plus"></i> Add Approved Input Mix
						</button>
					`);
					
					// Add click handler for inline button
					wrapper.find('.add-task-inline-btn').on('click', function() {
						show_add_task_dialog(frm);
					});
				}
			}
		});
		return;
	}

	// Group rows by task_name (approved input mix name)
	const groups = {};
	rows.forEach((row) => {
		const key = row.task_name || row.farm_task || __("Untitled Mix");
		if (!groups[key]) {
			groups[key] = [];
		}
		groups[key].push(row);
	});

	let html = "";
	Object.keys(groups).forEach((mix_name) => {
		const items = groups[mix_name];
		const farm_task = items[0].farm_task; // Get farm_task from first item

		html += `<div class="mb-3 task-group" style="border: 1px solid #d1d8dd; border-radius: 4px; padding: 10px;">`;
		html += `<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">`;
		html += `<h5 style="margin: 0;">${frappe.utils.escape_html(mix_name)}</h5>`;
		html += `<button class="btn btn-xs btn-danger remove-task-btn" data-farm-task="${farm_task}" data-task-name="${frappe.utils.escape_html(mix_name)}">
			<i class="fa fa-trash"></i> ${__("Remove Mix")}
		</button>`;
		html += `</div>`;
		html += `<table class="table table-bordered table-sm mb-0">
			<thead>
				<tr>
					<th style="width: 60%">${__("Approved Input")}</th>
					<th style="width: 20%">${__("Quantity")}</th>
					<th style="width: 20%">${__("Unit")}</th>
				</tr>
			</thead>
			<tbody>`;

		items.forEach((row) => {
			html += `<tr>
				<td>${frappe.utils.escape_html(row.item_name || row.item || "")}</td>
				<td>${row.quantity || ""}</td>
				<td>${frappe.utils.escape_html(row.unit || "")}</td>
			</tr>`;
		});

		html += `</tbody></table></div>`;
	});

	// Add "Add Approved Input Mix" button at the bottom if activity has more mixes
	html += `<div class="mt-3">
		<button class="btn btn-sm btn-primary add-task-inline-btn">
			<i class="fa fa-plus"></i> Add Approved Input Mix
		</button>
	</div>`;

	wrapper.html(html);

	// Add click handlers for remove buttons
	wrapper.find('.remove-task-btn').on('click', function() {
		const farm_task = $(this).data('farm-task');
		const mix_name = $(this).data('task-name');
		remove_task_from_table(frm, farm_task, mix_name);
	});
	
	// Add click handler for inline add approved input mix button
	wrapper.find('.add-task-inline-btn').on('click', function() {
		show_add_task_dialog(frm);
	});
}

/**
 * Remove an approved input mix and all its inputs from the child table
 */
function remove_task_from_table(frm, farm_task, mix_name) {
	frappe.confirm(
		__('Are you sure you want to remove the approved input mix "{0}" and all its inputs?', [mix_name]),
		function() {
			// Remove all rows with this farm_task
			const rows_to_remove = [];
			(frm.doc.tasks_items || []).forEach((row, idx) => {
				if (row.farm_task === farm_task) {
					rows_to_remove.push(idx);
				}
			});

			// Remove in reverse order to maintain correct indices
			rows_to_remove.reverse().forEach(idx => {
				frm.doc.tasks_items.splice(idx, 1);
			});

			frm.refresh_field('tasks_items');
			render_tasks_items_view(frm);
			
			frappe.show_alert({
				message: __('Approved input mix removed successfully'),
				indicator: 'green'
			});
		}
	);
}

/**
 * Show dialog to select an approved input mix from the current activity
 */
function show_add_task_dialog(frm) {
	if (!frm.doc.activity) {
		frappe.msgprint(__('Please select an activity first.'));
		return;
	}

	// Fetch available approved input mixes from the selected activity
	frappe.call({
		method: 'f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_available_tasks',
		args: {
			activity: frm.doc.activity
		},
		callback: function(r) {
			if (!r.message || r.message.length === 0) {
				frappe.msgprint(__('No approved input mixes available for this activity.'));
				return;
			}

			const available_mixes = r.message;
			
			// Get already added mix names to prevent duplicates
			const added_mixes = (frm.doc.tasks_items || []).map(row => row.farm_task);
			const unique_added_mixes = [...new Set(added_mixes)];

			// Filter out already added mixes
			const mixes_to_show = available_mixes.filter(mix => 
				!unique_added_mixes.includes(mix.farm_task)
			);

			if (mixes_to_show.length === 0) {
				frappe.msgprint(__('All approved input mixes from this activity have already been added.'));
				return;
			}

			// Create dialog
			const d = new frappe.ui.Dialog({
				title: __('Add Approved Input Mix'),
				fields: [
					{
						fieldname: 'task',
						fieldtype: 'Select',
						label: __('Select Approved Input Mix'),
						options: mixes_to_show.map(t => t.farm_task),
						reqd: 1,
						onchange: function() {
							const selected_mix = this.get_value();
							const mix_info = mixes_to_show.find(t => t.farm_task === selected_mix);
							if (mix_info) {
								d.set_df_property('task_info', 'options', 
									`<p><strong>Mix Name:</strong> ${mix_info.task_name}</p>
									<p><strong>Number of Approved Inputs:</strong> ${mix_info.item_count}</p>`
								);
							}
						}
					},
					{
						fieldname: 'task_info',
						fieldtype: 'HTML',
						label: __('Mix Information')
					}
				],
				primary_action_label: __('Add'),
				primary_action: function(values) {
					const selected_mix = values.task;
					const mix_data = mixes_to_show.find(t => t.farm_task === selected_mix);
					
					if (!mix_data) {
						frappe.msgprint(__('Invalid mix selection.'));
						return;
					}

					// Fetch approved input items and add to table
					frappe.call({
						method: 'f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_task_items',
						args: {
							farm_activity_task: mix_data.farm_activity_task,
							farm_task: mix_data.farm_task
						},
						callback: function(r) {
							if (r.message && r.message.length > 0) {
								r.message.forEach(item => {
									const child = frm.add_child('tasks_items');
									child.farm_activity_task = item.farm_activity_task;
									child.farm_task = item.farm_task;
									child.task_name = item.task_name;
									child.item = item.item;
									child.item_name = item.item_name;
									child.quantity = item.quantity;
									child.unit = item.unit;
								});
								
								frm.refresh_field('tasks_items');
								render_tasks_items_view(frm);
								frappe.show_alert({
									message: __('Approved input mix added successfully'),
									indicator: 'green'
								});
							}
							d.hide();
						}
					});
				}
			});

			d.show();
			
			// Trigger initial mix info display
			if (mixes_to_show.length > 0) {
				d.set_value('task', mixes_to_show[0].farm_task);
			}
		}
	});
}



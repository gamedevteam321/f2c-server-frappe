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

		// Initial render of grouped tasks/items view
		render_tasks_items_view(frm);
	},

	activity_group_type(frm) {
		// When Activity Group changes, clear Activity and tasks/items table
		if (frm.doc.activity) {
			frm.set_value("activity", null);
		}
		frm.clear_table("tasks_items");
		frm.refresh_field("tasks_items");
	},

	activity(frm) {
		// When Activity changes, clear the tasks/items table
		// User will manually add tasks using the "Add Task" button
		if (!frm.doc.activity) {
			frm.clear_table("tasks_items");
			frm.refresh_field("tasks_items");
			render_tasks_items_view(frm);
		}
		
		// Refresh to show/hide the "Add Task" button
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
 * Render tasks and items grouped by task name into the HTML field
 * so that the user sees:
 *   Task Name
 *     Item | Quantity | Unit
 */
function render_tasks_items_view(frm) {
	const wrapper = frm.fields_dict.tasks_items_view
		? frm.fields_dict.tasks_items_view.$wrapper
		: null;

	if (!wrapper) return;

	const rows = frm.doc.tasks_items || [];
	
	// If no tasks added yet, check if activity has tasks available
	if (!rows.length) {
		if (!frm.doc.activity) {
			wrapper.html("<p class=\"text-muted\">Please select an activity first.</p>");
			return;
		}
		
		// Check if activity has tasks
		frappe.call({
			method: 'f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_available_tasks',
			args: {
				activity: frm.doc.activity
			},
			callback: function(r) {
				if (!r.message || r.message.length === 0) {
					wrapper.html("<p class=\"text-muted\">No tasks mapped with this activity.</p>");
				} else {
					wrapper.html(`
						<p class="text-muted">No tasks added yet.</p>
						<button class="btn btn-sm btn-primary add-task-inline-btn">
							<i class="fa fa-plus"></i> Add Task
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

	// Group rows by task_name
	const groups = {};
	rows.forEach((row) => {
		const key = row.task_name || row.farm_task || __("Untitled Task");
		if (!groups[key]) {
			groups[key] = [];
		}
		groups[key].push(row);
	});

	let html = "";
	Object.keys(groups).forEach((task_name) => {
		const items = groups[task_name];
		const farm_task = items[0].farm_task; // Get farm_task from first item

		html += `<div class="mb-3 task-group" style="border: 1px solid #d1d8dd; border-radius: 4px; padding: 10px;">`;
		html += `<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">`;
		html += `<h5 style="margin: 0;">${frappe.utils.escape_html(task_name)}</h5>`;
		html += `<button class="btn btn-xs btn-danger remove-task-btn" data-farm-task="${farm_task}" data-task-name="${frappe.utils.escape_html(task_name)}">
			<i class="fa fa-trash"></i> ${__("Remove Task")}
		</button>`;
		html += `</div>`;
		html += `<table class="table table-bordered table-sm mb-0">
			<thead>
				<tr>
					<th style="width: 60%">${__("Item")}</th>
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

	// Add "Add Task" button at the bottom if activity has more tasks
	html += `<div class="mt-3">
		<button class="btn btn-sm btn-primary add-task-inline-btn">
			<i class="fa fa-plus"></i> Add Task
		</button>
	</div>`;

	wrapper.html(html);

	// Add click handlers for remove buttons
	wrapper.find('.remove-task-btn').on('click', function() {
		const farm_task = $(this).data('farm-task');
		const task_name = $(this).data('task-name');
		remove_task_from_table(frm, farm_task, task_name);
	});
	
	// Add click handler for inline add task button
	wrapper.find('.add-task-inline-btn').on('click', function() {
		show_add_task_dialog(frm);
	});
}

/**
 * Remove a task and all its items from the child table
 */
function remove_task_from_table(frm, farm_task, task_name) {
	frappe.confirm(
		__('Are you sure you want to remove the task "{0}" and all its items?', [task_name]),
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
				message: __('Task removed successfully'),
				indicator: 'green'
			});
		}
	);
}

/**
 * Show dialog to select a task from the current activity
 */
function show_add_task_dialog(frm) {
	if (!frm.doc.activity) {
		frappe.msgprint(__('Please select an activity first.'));
		return;
	}

	// Fetch available tasks from the selected activity
	frappe.call({
		method: 'f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_available_tasks',
		args: {
			activity: frm.doc.activity
		},
		callback: function(r) {
			if (!r.message || r.message.length === 0) {
				frappe.msgprint(__('No tasks available for this activity.'));
				return;
			}

			const available_tasks = r.message;
			
			// Get already added task names to prevent duplicates
			const added_tasks = (frm.doc.tasks_items || []).map(row => row.farm_task);
			const unique_added_tasks = [...new Set(added_tasks)];

			// Filter out already added tasks
			const tasks_to_show = available_tasks.filter(task => 
				!unique_added_tasks.includes(task.farm_task)
			);

			if (tasks_to_show.length === 0) {
				frappe.msgprint(__('All tasks from this activity have already been added.'));
				return;
			}

			// Create dialog
			const d = new frappe.ui.Dialog({
				title: __('Add Task'),
				fields: [
					{
						fieldname: 'task',
						fieldtype: 'Select',
						label: __('Select Task'),
						options: tasks_to_show.map(t => t.farm_task),
						reqd: 1,
						onchange: function() {
							const selected_task = this.get_value();
							const task_info = tasks_to_show.find(t => t.farm_task === selected_task);
							if (task_info) {
								d.set_df_property('task_info', 'options', 
									`<p><strong>Task Name:</strong> ${task_info.task_name}</p>
									<p><strong>Number of Items:</strong> ${task_info.item_count}</p>`
								);
							}
						}
					},
					{
						fieldname: 'task_info',
						fieldtype: 'HTML',
						label: __('Task Information')
					}
				],
				primary_action_label: __('Add'),
				primary_action: function(values) {
					const selected_task = values.task;
					const task_data = tasks_to_show.find(t => t.farm_task === selected_task);
					
					if (!task_data) {
						frappe.msgprint(__('Invalid task selection.'));
						return;
					}

					// Fetch task items and add to table
					frappe.call({
						method: 'f2c.farm_to_crop.doctype.farm_crop_activity_mapping.farm_crop_activity_mapping.get_task_items',
						args: {
							farm_activity_task: task_data.farm_activity_task,
							farm_task: task_data.farm_task
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
									message: __('Task added successfully'),
									indicator: 'green'
								});
							}
							d.hide();
						}
					});
				}
			});

			d.show();
			
			// Trigger initial task info display
			if (tasks_to_show.length > 0) {
				d.set_value('task', tasks_to_show[0].farm_task);
			}
		}
	});
}



// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("POP-Activity List", {
	refresh(frm) {
		// Customize Crop Stage Link field to show stage name via custom query
		if (frm.fields_dict.crop_stage) {
			frm.fields_dict.crop_stage.get_query = function () {
				return {
					filters: {},
					query:
						"f2c.farm_to_crop.doctype.pop_activity_list.pop_activity_list.get_crop_stage_query",
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
					"f2c.farm_to_crop.doctype.pop_activity_list.pop_activity_list.get_activity_query",
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
		// When Activity changes, fetch all tasks and items for that activity
		if (!frm.doc.activity) {
			frm.clear_table("tasks_items");
			frm.refresh_field("tasks_items");
			return;
		}

		frappe.call({
			method:
				"f2c.farm_to_crop.doctype.pop_activity_list.pop_activity_list.get_activity_tasks_and_items",
			args: {
				activity: frm.doc.activity,
			},
			callback(r) {
				frm.clear_table("tasks_items");

				(r.message || []).forEach((row) => {
					const child = frm.add_child("tasks_items");
					child.farm_activity_task = row.farm_activity_task;
					child.farm_task = row.farm_task;
					child.task_name = row.task_name;
					child.item = row.item;
					child.item_name = row.item_name;
					child.quantity = row.quantity;
					child.unit = row.unit;
				});

				frm.refresh_field("tasks_items");
				render_tasks_items_view(frm);
			},
		});
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
	if (!rows.length) {
		wrapper.html("<p class=\"text-muted\">No tasks/items for this activity.</p>");
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

		html += `<div class="mb-3">`;
		html += `<h5>${frappe.utils.escape_html(task_name)}</h5>`;
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

	wrapper.html(html);
}




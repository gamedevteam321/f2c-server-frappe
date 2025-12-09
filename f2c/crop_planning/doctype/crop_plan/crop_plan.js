// Copyright (c) 2025, F2C and contributors
// For license information, please see license.txt

frappe.ui.form.on('Crop Plan', {
	refresh: function(frm) {
		// Set up custom rendering for blocks table
		setup_blocks_table(frm);
		
		// Add button to auto-populate blocks
		if (frm.doc.field && !frm.is_new()) {
			frm.add_custom_button(__('Auto Populate Blocks'), function() {
				auto_populate_blocks(frm);
			}, __('Actions'));
		}
	},
	
	field: function(frm) {
		// Auto-fetch field area when field is selected (converted to acres)
		if (frm.doc.field) {
			frappe.db.get_value('Geo Fencing Area', frm.doc.field, 'area', (r) => {
				if (r && r.area) {
					// Convert square meters to acres (1 acre = 4046.86 sq meters)
					let area_acres = r.area * 0.000247105;
					frm.set_value('field_area_acres', area_acres);
				}
			});
			
			// Prompt to auto-populate blocks
			if (!frm.is_new() && (!frm.doc.blocks || frm.doc.blocks.length === 0)) {
				frappe.confirm(
					__('Do you want to auto-populate all blocks from this field?'),
					function() {
						auto_populate_blocks(frm);
					}
				);
			}
		}
		
		// Refresh blocks table to update filters
		frm.refresh_field('blocks');
	},
	
	blocks_on_form_rendered: function(frm) {
		setup_blocks_table(frm);
	}
});

frappe.ui.form.on('Crop Plan Block', {
	block: function(frm, cdt, cdn) {
		// Auto-fetch block name and area when block is selected (converted to acres)
		let row = locals[cdt][cdn];
		if (row.block) {
			frappe.db.get_value('Geo Fencing Area', row.block, ['area_name', 'area'], (r) => {
				if (r) {
					frappe.model.set_value(cdt, cdn, 'block_name', r.area_name);
					// Convert square meters to acres
					if (r.area) {
						let area_acres = r.area * 0.000247105;
						frappe.model.set_value(cdt, cdn, 'block_area', area_acres);
					}
				}
			});
		}
	},
	
	crop: function(frm, cdt, cdn) {
		// Refresh POP field filter when crop changes
		let row = locals[cdt][cdn];
		if (!row.crop) {
			frappe.model.set_value(cdt, cdn, 'pop', '');
			frappe.model.set_value(cdt, cdn, 'pop_name', '');
		}
	},
	
	pop: function(frm, cdt, cdn) {
		// Auto-fetch POP name when POP is selected
		let row = locals[cdt][cdn];
		if (row.pop) {
			frappe.db.get_value('POP', row.pop, 'pop_name', (r) => {
				if (r && r.pop_name) {
					frappe.model.set_value(cdt, cdn, 'pop_name', r.pop_name);
				}
			});
		}
	},
	
	blocks_add: function(frm, cdt, cdn) {
		// Update total blocks count
		frm.set_value('total_blocks', frm.doc.blocks ? frm.doc.blocks.length : 0);
	},
	
	blocks_remove: function(frm, cdt, cdn) {
		// Update total blocks count
		frm.set_value('total_blocks', frm.doc.blocks ? frm.doc.blocks.length : 0);
	}
});

// Set up query filters
frappe.ui.form.on('Crop Plan', {
	setup: function(frm) {
		// Filter field to show only Geo Fencing Areas of type "Field"
		frm.set_query('field', function() {
			return {
				filters: {
					'geo_fencing_type': 'Field'
				}
			};
		});
	}
});

frappe.ui.form.on('Crop Plan Block', {
	setup: function(frm) {
		// Filter block to show only Blocks that belong to the selected field
		frm.set_query('block', 'blocks', function(doc, cdt, cdn) {
			if (!doc.field) {
				frappe.msgprint(__('Please select a Field first'));
				return {
					filters: {
						'name': ['=', '']  // Return no results
					}
				};
			}
			return {
				filters: {
					'geo_fencing_type': 'Block',
					'parent_area': doc.field
				}
			};
		});
		
		// Filter POP to show only POPs for the selected crop
		frm.set_query('pop', 'blocks', function(doc, cdt, cdn) {
			let row = locals[cdt][cdn];
			if (!row.crop) {
				return {
					filters: {
						'name': ['=', '']  // Return no results
					}
				};
			}
			return {
				filters: {
					'crop': row.crop
				}
			};
		});
	}
});

// Set up blocks table with custom buttons and expandable activities
function setup_blocks_table(frm) {
	if (!frm.doc.blocks || frm.doc.blocks.length === 0) {
		return;
	}
	
	// Add custom buttons to each block row
	frm.fields_dict.blocks.grid.wrapper.find('.grid-body .grid-row').each(function(i) {
		let $row = $(this);
		let row_index = i + 1;  // 1-based index
		let block_data = frm.doc.blocks[i];
		
		// Remove existing custom buttons to avoid duplicates
		$row.find('.activities-btn-wrapper').remove();
		
		if (block_data && block_data.pop) {
			// Create button wrapper
			let $btn_wrapper = $('<div class="activities-btn-wrapper" style="padding: 5px;"></div>');
			
			// Load Activities button
			let $load_btn = $('<button class="btn btn-xs btn-default" style="margin-right: 5px;">')
				.text(__('Load Activities'))
				.on('click', function(e) {
					e.preventDefault();
					e.stopPropagation();
					load_activities_from_pop(frm, row_index, block_data.pop);
				});
			
			// View/Edit Activities button
			let $view_btn = $('<button class="btn btn-xs btn-primary">')
				.text(__('View/Edit Activities'))
				.on('click', function(e) {
					e.preventDefault();
					e.stopPropagation();
					toggle_activities_view(frm, row_index, $row);
				});
			
			$btn_wrapper.append($load_btn).append($view_btn);
			$row.find('.grid-static-col').first().append($btn_wrapper);
		}
	});
}

// Load activities from POP
function load_activities_from_pop(frm, block_idx, pop_name) {
	frappe.confirm(
		__('This will replace existing activities for this block. Continue?'),
		function() {
			frappe.call({
				method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.load_pop_activities',
				args: {
					crop_plan_name: frm.doc.name,
					block_idx: block_idx,
					pop_name: pop_name
				},
				callback: function(r) {
					if (r.message) {
						frappe.show_alert({
							message: __('Loaded {0} activities from POP', [r.message.length]),
							indicator: 'green'
						});
						frm.reload_doc();
					}
				}
			});
		}
	);
}

// Toggle activities view for a block
function toggle_activities_view(frm, block_idx, $row) {
	let $activities_section = $row.next('.activities-section');
	
	if ($activities_section.length > 0) {
		// Already expanded, collapse it
		$activities_section.remove();
	} else {
		// Expand and show activities
		show_activities_section(frm, block_idx, $row);
	}
}

// Show activities section below the block row
function show_activities_section(frm, block_idx, $row) {
	// Get activities for this block
	let block_activities = frm.doc.activities ? frm.doc.activities.filter(a => a.block_reference == block_idx) : [];
	
	// Create expandable section
	let $section = $('<tr class="activities-section"><td colspan="100%"></td></tr>');
	let $content = $('<div class="activities-content" style="padding: 15px; background: #f9f9f9; border: 1px solid #ddd;"></div>');
	
	// Add header
	$content.append('<h5>' + __('Activities for Block {0}', [block_idx]) + '</h5>');
	
	if (block_activities.length === 0) {
		$content.append('<p class="text-muted">' + __('No activities loaded. Click "Load Activities" to import from POP.') + '</p>');
	} else {
		// Create activities table
		let $table = $('<table class="table table-bordered table-sm"><thead><tr>' +
			'<th>' + __('Seq') + '</th>' +
			'<th>' + __('Activity') + '</th>' +
			'<th>' + __('Crop Stage') + '</th>' +
			'<th>' + __('Duration') + '</th>' +
			'<th>' + __('DAT') + '</th>' +
			'<th>' + __('Inputs') + '</th>' +
			'<th>' + __('Actions') + '</th>' +
			'</tr></thead><tbody></tbody></table>');
		
		let $tbody = $table.find('tbody');
		
		// Sort activities by sequence
		block_activities.sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
		
		block_activities.forEach(function(activity, idx) {
			let inputs_count = activity.approved_inputs ? activity.approved_inputs.length : 0;
			let $tr = $('<tr></tr>');
			
			$tr.append('<td>' + (activity.sequence || 0) + '</td>');
			$tr.append('<td>' + (activity.activity_name || activity.activity || '-') + '</td>');
			$tr.append('<td>' + (activity.crop_stage || '-') + '</td>');
			$tr.append('<td>' + (activity.duration_after_stage || '-') + '</td>');
			$tr.append('<td>' + (activity.is_dat ? (activity.dat || '-') : '-') + '</td>');
			$tr.append('<td>' + inputs_count + ' input(s)</td>');
			
			let $actions = $('<td></td>');
			let $edit_btn = $('<button class="btn btn-xs btn-default">' + __('Edit') + '</button>')
				.on('click', function() {
					edit_activity(frm, activity, block_idx);
				});
			$actions.append($edit_btn);
			$tr.append($actions);
			
			$tbody.append($tr);
		});
		
		$content.append($table);
	}
	
	// Add button to add new activity
	let $add_btn = $('<button class="btn btn-sm btn-primary">' + __('Add Activity') + '</button>')
		.on('click', function() {
			add_new_activity(frm, block_idx);
		});
	$content.append($add_btn);
	
	// Add close button
	let $close_btn = $('<button class="btn btn-sm btn-default" style="margin-left: 10px;">' + __('Close') + '</button>')
		.on('click', function() {
			$section.remove();
		});
	$content.append($close_btn);
	
	$section.find('td').append($content);
	$row.after($section);
}

// Edit activity dialog
function edit_activity(frm, activity, block_idx) {
	// Find the actual activity row in the document
	let activity_row = null;
	let activity_idx = -1;
	
	for (let i = 0; i < frm.doc.activities.length; i++) {
		if (frm.doc.activities[i].name === activity.name) {
			activity_row = frm.doc.activities[i];
			activity_idx = i;
			break;
		}
	}
	
	if (!activity_row) {
		frappe.msgprint(__('Activity not found'));
		return;
	}
	
	// Open the activities child table in edit mode
	frm.fields_dict.activities.grid.grid_rows[activity_idx].toggle_view(true);
	frm.scroll_to_field('activities');
}

// Add new activity
function add_new_activity(frm, block_idx) {
	let new_row = frm.add_child('activities', {
		block_reference: String(block_idx),
		sequence: 0
	});
	frm.refresh_field('activities');
	
	// Find the newly added row and open it for editing
	let new_idx = frm.doc.activities.length - 1;
	frm.fields_dict.activities.grid.grid_rows[new_idx].toggle_view(true);
	frm.scroll_to_field('activities');
}

// Auto populate blocks from field
function auto_populate_blocks(frm) {
	if (!frm.doc.field) {
		frappe.msgprint(__('Please select a Field first'));
		return;
	}
	
	frappe.call({
		method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.auto_populate_blocks',
		args: {
			crop_plan_name: frm.doc.name
		},
		callback: function(r) {
			if (r.message) {
				frappe.show_alert({
					message: __('Added {0} blocks from field', [r.message]),
					indicator: 'green'
				});
				frm.reload_doc();
			}
		}
	});
}


// Copyright (c) 2025, F2C and contributors
// For license information, please see license.txt

frappe.ui.form.on('Crop Plan', {
	refresh: function(frm) {
		// Clean up any existing buttons first
		if (frm.fields_dict.blocks && frm.fields_dict.blocks.grid && frm.fields_dict.blocks.grid.wrapper) {
			frm.fields_dict.blocks.grid.wrapper.find('.activities-btn-wrapper').remove();
		}
		
		// Auto-load Land Preparation activities for blocks that have land_preparation but no LP activities yet (one block per refresh to avoid loop)
		if (!frm.is_new() && frm.doc.blocks && frm.doc.blocks.length > 0 && frm.doc.activities) {
			let has_lp = {};
			frm.doc.activities.forEach(function(a) {
				if (a.activity_source === 'Land Preparation') has_lp[a.block_reference] = true;
			});
			for (let idx = 0; idx < frm.doc.blocks.length; idx++) {
				let block = frm.doc.blocks[idx];
				let block_ref = String(idx + 1);
				if (block.land_preparation && !has_lp[block_ref]) {
					frappe.call({
						method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.load_land_preparation_activities',
						args: {
							crop_plan_name: frm.doc.name,
							block_idx: parseInt(block_ref, 10),
							land_preparation_name: block.land_preparation
						},
						callback: function(r) {
							if (r.message && r.message.length > 0) {
								frappe.show_alert({ message: __('Loaded {0} Land Preparation activities', [r.message.length]), indicator: 'green' });
								frm.reload_doc();
							}
						}
					});
					break;
				}
			}
		}
		
		// Set up custom rendering for blocks table (with delay to ensure grid is ready)
		setTimeout(function() {
			setup_blocks_table(frm);
		}, 300);
		
		// Add button to auto-populate blocks
		if (frm.doc.field && !frm.is_new()) {
			frm.add_custom_button(__('Auto Populate Blocks'), function() {
				auto_populate_blocks(frm);
			}, __('Actions'));
		}

		// Populate "Items (Qty · Unit)" summary for Approved Input Mixes so item values are visible in the grid
		refresh_approved_input_mixes_summary(frm);

		// Button to open child table data in an editable dialog (nested child tables don't save via standard form save)
		if (!frm.is_new() && frm.doc.approved_input_mixes && frm.doc.approved_input_mixes.length > 0) {
			frm.add_custom_button(__('Edit Approved Input Items'), function() {
				show_approved_input_items_dialog(frm);
			}, __('Approved Input Mixes'));
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
		// Clean up any existing buttons first
		if (frm.fields_dict.blocks && frm.fields_dict.blocks.grid && frm.fields_dict.blocks.grid.wrapper) {
			frm.fields_dict.blocks.grid.wrapper.find('.activities-btn-wrapper').remove();
		}
		
		// Delay to ensure grid is fully rendered
		setTimeout(function() {
			setup_blocks_table(frm);
		}, 200);
	},
	approved_input_mixes_on_form_rendered: function(frm) {
		refresh_approved_input_mixes_summary(frm);
	}
});

frappe.ui.form.on('Crop Plan Block', {
	block: function(frm, cdt, cdn) {
		// Auto-fetch block name, area, and field when block is selected (converted to acres)
		let row = locals[cdt][cdn];
		if (row.block) {
			frappe.db.get_value('Geo Fencing Area', row.block, ['area_name', 'area', 'parent_area'], (r) => {
				if (r) {
					frappe.model.set_value(cdt, cdn, 'block_name', r.area_name);
					// Convert square meters to acres
					if (r.area) {
						let area_acres = r.area * 0.000247105;
						frappe.model.set_value(cdt, cdn, 'block_area', area_acres);
					}
					// Set field from block's parent_area
					if (r.parent_area) {
						frappe.model.set_value(cdt, cdn, 'field', r.parent_area);
						// Fetch field name
						frappe.db.get_value('Geo Fencing Area', r.parent_area, 'area_name', (field_r) => {
							if (field_r && field_r.area_name) {
								frappe.model.set_value(cdt, cdn, 'field_name', field_r.area_name);
							}
						});
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
		// Update total blocks count - count unique blocks, not total rows
		if (frm.doc.blocks) {
			let unique_blocks = new Set();
			frm.doc.blocks.forEach(function(block) {
				if (block.block) {
					unique_blocks.add(block.block);
				}
			});
			frm.set_value('total_blocks', unique_blocks.size);
		} else {
			frm.set_value('total_blocks', 0);
		}
	},
	
	blocks_remove: function(frm, cdt, cdn) {
		// Update total blocks count - count unique blocks, not total rows
		if (frm.doc.blocks) {
			let unique_blocks = new Set();
			frm.doc.blocks.forEach(function(block) {
				if (block.block) {
					unique_blocks.add(block.block);
				}
			});
			frm.set_value('total_blocks', unique_blocks.size);
		} else {
			frm.set_value('total_blocks', 0);
		}
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
		
		// Filter field to show only Geo Fencing Areas of type "Field"
		frm.set_query('field', 'blocks', function(doc, cdt, cdn) {
			return {
				filters: {
					'geo_fencing_type': 'Field'
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

// Build a one-line summary of approved_inputs for display in the Approved Input Mixes grid
function build_items_summary(approved_inputs) {
	if (!approved_inputs || !approved_inputs.length) return '';
	return approved_inputs.map(function(inp) {
		let name = inp.item_name || inp.item || __('Item');
		let qty = inp.quantity != null ? inp.quantity : '';
		let u = inp.unit || '';
		return name + ': ' + qty + (u ? ' ' + u : '');
	}).join(' · ');
}

// Show Approved Inputs (child table) in an EDITABLE dialog.
// Fetches from server so nested approved_inputs are loaded, then renders
// editable quantity/unit fields that save directly via the backend API.
function show_approved_input_items_dialog(frm) {
	var mixes = frm.doc.approved_input_mixes || [];
	if (!mixes.length) {
		frappe.msgprint(__('No Approved Input Mixes in this Crop Plan.'));
		return;
	}
	frappe.call({
		method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.get_approved_input_mixes_with_inputs',
		args: { crop_plan_name: frm.doc.name },
		callback: function(r) {
			var loaded_mixes = r.message || [];
			if (!loaded_mixes.length) {
				frappe.msgprint(__('No Approved Input Mixes found.'));
				return;
			}
			render_approved_input_items_dialog(frm, loaded_mixes);
		},
		error: function() {
			// Fallback to the older read-only approach
			frappe.call({
				method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.get_crop_plan_with_activities',
				args: { crop_plan_name: frm.doc.name },
				callback: function(r) {
					var loaded_mixes = (r.message && r.message.approved_input_mixes) || [];
					render_approved_input_items_dialog(frm, loaded_mixes.length ? loaded_mixes : mixes);
				}
			});
		}
	});
}

function render_approved_input_items_dialog(frm, mixes) {
	var unit_options = ['kg', 'g', 'ml', 'L', 'ml/L', 'g/L', 'Bags/Acre', 'Per Manufacturer'];
	var body = [];
	// Track all editable inputs for saving
	var editable_inputs = [];

	mixes.forEach(function(mix, mix_idx) {
		var title = (mix.activity_name || __('Activity')) + ' → ' + (mix.task_name || mix.farm_task || __('Mix'));
		body.push('<div class="mix-section" style="margin-bottom: 16px;">');
		body.push('<strong style="font-size: 13px;">' + (mix_idx + 1) + '. ' + frappe.utils.escape_html(title) + '</strong>');
		var inputs = mix.approved_inputs || [];
		if (!inputs.length) {
			body.push('<p class="text-muted" style="margin: 6px 0 0 0;">' + __('No items') + '</p>');
		} else {
			body.push('<table class="table table-bordered table-condensed" style="margin-top: 6px; font-size: 12px;">');
			body.push('<thead><tr>');
			body.push('<th>' + __('Approved Input') + '</th>');
			body.push('<th style="width: 120px;">' + __('Quantity') + '</th>');
			body.push('<th style="width: 130px;">' + __('Unit') + '</th>');
			body.push('</tr></thead><tbody>');
			inputs.forEach(function(inp, inp_idx) {
				var inp_name = inp.item_name || inp.item || '—';
				var qty = inp.quantity != null ? inp.quantity : 0;
				var unit = inp.unit || 'ml/L';
				var input_id = 'inp_' + mix_idx + '_' + inp_idx;

				// Build unit <select> options
				var unit_select = '<select class="form-control input-xs" data-input-id="' + input_id + '" data-field="unit" style="font-size: 12px; height: 28px;">';
				unit_options.forEach(function(u) {
					unit_select += '<option value="' + u + '"' + (u === unit ? ' selected' : '') + '>' + u + '</option>';
				});
				unit_select += '</select>';

				body.push('<tr>');
				body.push('<td>' + frappe.utils.escape_html(inp_name) + '</td>');
				body.push('<td><input type="number" step="0.001" class="form-control input-xs" data-input-id="' + input_id + '" data-field="quantity" value="' + qty + '" style="font-size: 12px; height: 28px;" /></td>');
				body.push('<td>' + unit_select + '</td>');
				body.push('</tr>');

				editable_inputs.push({
					id: input_id,
					mix_name: mix.name,
					approved_input_name: inp.name,
					original_qty: qty,
					original_unit: unit
				});
			});
			body.push('</tbody></table>');
		}
		body.push('</div>');
	});

	var d = new frappe.ui.Dialog({
		title: __('Edit Approved Input Items'),
		size: 'large',
		fields: [{ fieldtype: 'HTML', fieldname: 'items_html', options: body.join('') }],
		primary_action_label: __('Save Changes'),
		primary_action: function() {
			// Collect changed inputs
			var changes = [];
			editable_inputs.forEach(function(inp_info) {
				var $qty = d.$wrapper.find('[data-input-id="' + inp_info.id + '"][data-field="quantity"]');
				var $unit = d.$wrapper.find('[data-input-id="' + inp_info.id + '"][data-field="unit"]');
				var new_qty = parseFloat($qty.val()) || 0;
				var new_unit = $unit.val() || inp_info.original_unit;

				if (new_qty !== inp_info.original_qty || new_unit !== inp_info.original_unit) {
					changes.push({
						mix_name: inp_info.mix_name,
						approved_input_name: inp_info.approved_input_name,
						quantity: new_qty,
						unit: new_unit
					});
				}
			});

			if (!changes.length) {
				frappe.show_alert({ message: __('No changes to save.'), indicator: 'blue' });
				d.hide();
				return;
			}

			// Save each change via the backend API
			var saved = 0;
			var errors = 0;
			var total = changes.length;

			d.disable_primary_action();
			frappe.show_alert({ message: __('Saving {0} change(s)...', [total]), indicator: 'blue' });

			changes.forEach(function(change) {
				frappe.call({
					method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.update_approved_input_qty',
					args: {
						crop_plan_name: frm.doc.name,
						mix_name: change.mix_name,
						approved_input_name: change.approved_input_name,
						quantity: change.quantity,
						unit: change.unit
					},
					async: true,
					callback: function(r) {
						saved++;
						check_done();
					},
					error: function() {
						errors++;
						check_done();
					}
				});
			});

			function check_done() {
				if (saved + errors >= total) {
					d.enable_primary_action();
					if (errors > 0) {
						frappe.show_alert({ message: __('Saved {0} changes, {1} failed.', [saved, errors]), indicator: 'orange' });
					} else {
						frappe.show_alert({ message: __('All {0} changes saved successfully.', [saved]), indicator: 'green' });
						d.hide();
						frm.reload_doc();
					}
				}
			}
		}
	});
	d.show();
}

// Populate items_summary for each Approved Input Mix row so item values are visible in the doctype form
function refresh_approved_input_mixes_summary(frm) {
	if (!frm.doc.approved_input_mixes || !frm.doc.approved_input_mixes.length) return;
	let modified = false;
	frm.doc.approved_input_mixes.forEach(function(mix) {
		let summary = build_items_summary(mix.approved_inputs);
		if (mix.items_summary !== summary) {
			frappe.model.set_value(mix.doctype, mix.name, 'items_summary', summary);
			modified = true;
		}
	});
	if (modified && frm.fields_dict.approved_input_mixes && frm.fields_dict.approved_input_mixes.grid) {
		frm.refresh_field('approved_input_mixes');
	}
}

// Set up blocks table with custom buttons and expandable activities
// Use debounce to prevent multiple simultaneous executions
let setup_blocks_table_timeout = null;

function setup_blocks_table(frm) {
	if (!frm.doc.blocks || frm.doc.blocks.length === 0) {
		return;
	}
	
	// Clear any pending timeout
	if (setup_blocks_table_timeout) {
		clearTimeout(setup_blocks_table_timeout);
	}
	
	// Wait for grid to be fully rendered (debounced)
	setup_blocks_table_timeout = setTimeout(function() {
		setup_blocks_table_timeout = null;
		
		if (!frm.fields_dict.blocks || !frm.fields_dict.blocks.grid || !frm.fields_dict.blocks.grid.wrapper) {
			return;
		}
		
		// Remove ALL existing button wrappers first to prevent duplicates (globally)
		// Use multiple selectors to ensure we get all instances
		frm.fields_dict.blocks.grid.wrapper.find('.activities-btn-wrapper').remove();
		frm.fields_dict.blocks.grid.wrapper.find('[data-block-id]').filter('.activities-btn-wrapper').remove();
		// Also remove any orphaned buttons
		frm.fields_dict.blocks.grid.wrapper.find('button').filter(function() {
			return $(this).text().trim() === __('Load Activities') || $(this).text().trim() === __('View/Edit Activities');
		}).closest('.activities-btn-wrapper').remove();
		
		// Group blocks by block reference for better visual organization
		let block_groups = {};
		frm.doc.blocks.forEach(function(block, idx) {
			if (block.block) {
				if (!block_groups[block.block]) {
					block_groups[block.block] = [];
				}
				block_groups[block.block].push({block: block, index: idx});
			}
		});
		
		// Track which blocks we've already added buttons to (by block reference)
		let blocks_with_buttons = new Set();
		
		// Add visual grouping and custom buttons to each block row
		frm.fields_dict.blocks.grid.wrapper.find('.grid-body .grid-row').each(function(i) {
			let $row = $(this);
			let row_index = i + 1;  // 1-based index
			let block_data = frm.doc.blocks[i];
			
			// Double check - remove any existing buttons in this row
			$row.find('.activities-btn-wrapper').remove();
			
			// Add visual indicator for grouped blocks (same block reference)
			if (block_data && block_data.block) {
				let group = block_groups[block_data.block];
				let is_first_in_group = group && group[0].index === i;
				
				if (group && group.length > 1) {
					// This block has multiple crops - add visual indicator
					if (is_first_in_group) {
						$row.css('border-top', '2px solid #2ecc71');
					} else {
						$row.css('background-color', '#f9fafb');
						$row.css('border-left', '3px solid #2ecc71');
					}
				}
				
				// Only add buttons once per unique block (first row of each block group, or single row)
				let should_add_buttons = false;
				if (group && group.length > 1) {
					// Multiple crops - only add to first row
					should_add_buttons = is_first_in_group && !blocks_with_buttons.has(block_data.block);
				} else {
					// Single crop - add buttons if not already added
					should_add_buttons = !blocks_with_buttons.has(block_data.block);
				}
				
				if (should_add_buttons && (block_data.pop || block_data.land_preparation)) {
					blocks_with_buttons.add(block_data.block);
					
					// Check if buttons already exist in this row (extra safety check)
					if ($row.find('.activities-btn-wrapper[data-block-id="' + block_data.block + '"]').length > 0) {
						return; // Skip if buttons already exist
					}
					
					// Create button wrapper with data attribute for easy identification
					let $btn_wrapper = $('<div class="activities-btn-wrapper" data-block-id="' + block_data.block + '" style="padding: 5px;"></div>');
					
					if (block_data.pop) {
						// Load Activities (POP) button
						let $load_btn = $('<button class="btn btn-xs btn-default" style="margin-right: 5px;">')
							.text(__('Load Activities'))
							.on('click', function(e) {
								e.preventDefault();
								e.stopPropagation();
								load_activities_from_pop(frm, row_index, block_data.pop);
							});
						$btn_wrapper.append($load_btn);
					}
					if (block_data.land_preparation) {
						// Load Land Prep Activities button
						let $load_lp_btn = $('<button class="btn btn-xs btn-default" style="margin-right: 5px;">')
							.text(__('Load Land Prep Activities'))
							.on('click', function(e) {
								e.preventDefault();
								e.stopPropagation();
								load_activities_from_land_preparation(frm, row_index, block_data.land_preparation);
							});
						$btn_wrapper.append($load_lp_btn);
					}
					
					// View/Edit Activities button
					let $view_btn = $('<button class="btn btn-xs btn-primary">')
						.text(__('View/Edit Activities'))
						.on('click', function(e) {
							e.preventDefault();
							e.stopPropagation();
							toggle_activities_view(frm, row_index, $row);
						});
					
					$btn_wrapper.append($view_btn);
					
					// Find the first static column and append buttons there
					let $staticCol = $row.find('.grid-static-col').first();
					if ($staticCol.length === 0) {
						// Fallback: try to find any suitable container in the row
						$staticCol = $row.find('td').first();
					}
					$staticCol.append($btn_wrapper);
				}
			}
		});
	}, 300);
}

// Load activities from POP
function load_activities_from_pop(frm, block_idx, pop_name) {
	frappe.confirm(
		__('This will replace existing POP activities for this block. Continue?'),
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

// Load activities from Land Preparation into the Crop Plan activities table
function load_activities_from_land_preparation(frm, block_idx, land_preparation_name) {
	if (!land_preparation_name) return;
	frappe.call({
		method: 'f2c.crop_planning.doctype.crop_plan.crop_plan.load_land_preparation_activities',
		args: {
			crop_plan_name: frm.doc.name,
			block_idx: block_idx,
			land_preparation_name: land_preparation_name
		},
		callback: function(r) {
			if (r.message && r.message.length > 0) {
				frappe.show_alert({
					message: __('Loaded {0} Land Preparation activities', [r.message.length]),
					indicator: 'green'
				});
				frm.reload_doc();
			} else {
				frappe.msgprint(__('No activities found in this Land Preparation.'));
			}
		},
		error: function() {
			frappe.msgprint(__('Failed to load Land Preparation activities.'));
		}
	});
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
	// Get activities for this block (block_reference is 1-based)
	let block_ref = String(block_idx + 1);
	let block_activities = frm.doc.activities ? frm.doc.activities.filter(a => a.block_reference == block_ref) : [];
	
	// Create expandable section
	let $section = $('<tr class="activities-section"><td colspan="100%"></td></tr>');
	let $content = $('<div class="activities-content" style="padding: 15px; background: #f9f9f9; border: 1px solid #ddd;"></div>');
	
	// Add header
	$content.append('<h5>' + __('Activities for Block {0}', [block_idx]) + '</h5>');
	
	if (block_activities.length === 0) {
		$content.append('<p class="text-muted">' + __('No activities loaded. Click "Load Activities" to import from POP or "Load Land Prep Activities" to import from Land Preparation.') + '</p>');
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


// Copyright (c) 2025

frappe.ui.form.on('On Demand Activity', {
	setup(frm) {
		// Filter blocks to those that belong to the selected field
		frm.set_query('block', 'blocks', function(doc) {
			if (!doc.field) {
				return { filters: { name: ['=', ''] } };
			}
			return {
				filters: {
					parent_area: doc.field,
					geo_fencing_type: 'Block'
				}
			};
		});

		// Filter activities by selected activity_group_type
		frm.set_query('activity', function(doc) {
			if (!doc.activity_group_type) {
				return { filters: { name: ['=', ''] } };
			}
			return {
				filters: {
					activity_group_type: doc.activity_group_type
				}
			};
		});

		// Filter assets by category for each equipment section
		frm.set_query('asset', 'machinery', function() {
			return {
				filters: {
					asset_category: ['like', '%Machinery%']
				}
			};
		});

		frm.set_query('asset', 'implements', function() {
			return {
				filters: {
					asset_category: ['like', '%Implement%']
				}
			};
		});

		frm.set_query('asset', 'hand_tools', function() {
			return {
				filters: {
					asset_category: ['like', '%Hand Tool%']
				}
			};
		});

		frm.set_query('asset', 'other_tools', function() {
			return {
				filters: {
					asset_category: ['like', '%Other Tool%']
				}
			};
		});
	},

	refresh(frm) {
		// Show/hide implements section based on tractor selection
		frm.trigger('check_tractor_selection');
	},

	field(frm) {
		// Clear blocks table when field changes
		frm.clear_table('blocks');
		frm.refresh_field('blocks');
		frm.trigger('recompute_totals');
	},

	activity_group_type(frm) {
		// Clear activity when activity group changes
		frm.set_value('activity', '');
		frm.set_value('activity_name', '');
		frm.set_value('is_spray', 0);
		frm.set_value('approved_input_mix', '');
		frm.set_value('approved_input_mix_name', '');
		frm.clear_table('inputs');
		frm.refresh_field('inputs');
	},

	activity(frm) {
		if (!frm.doc.activity) return;

		// Fetch activity details
		frappe.call({
			method: 'f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity.get_activity_details',
			args: { activity: frm.doc.activity },
			callback(r) {
				const d = r.message || {};
				if (d.activity_name) frm.set_value('activity_name', d.activity_name);
				if (d.is_spray != null) frm.set_value('is_spray', d.is_spray ? 1 : 0);

				// If activity has farm_tasks, show approved_input_mix field (it's already in the form)
				// If not, clear approved_input_mix
				if (!d.has_farm_tasks) {
					frm.set_value('approved_input_mix', '');
					frm.set_value('approved_input_mix_name', '');
					frm.clear_table('inputs');
					frm.refresh_field('inputs');
				}

				frm.trigger('recompute_water');
				frm.trigger('recompute_input_totals');
			}
		});
	},

	approved_input_mix(frm) {
		if (!frm.doc.approved_input_mix) {
			frm.clear_table('inputs');
			frm.refresh_field('inputs');
			return;
		}

		frappe.call({
			method: 'f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity.get_farm_task_items',
			args: { farm_task: frm.doc.approved_input_mix },
			callback(r) {
				const items = r.message || [];
				frm.clear_table('inputs');
				items.forEach(it => {
					const row = frm.add_child('inputs', {
						item: it.item,
						item_name: it.item_name,
						rate_quantity: it.rate_quantity,
						unit: it.unit
					});
				});
				frm.refresh_field('inputs');
				frm.trigger('recompute_input_totals');
			}
		});
	},

	blocks_add(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.block) {
			frappe.call({
				method: 'f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity.get_block_details',
				args: { block: row.block, field: frm.doc.field || null },
				callback(r) {
					const d = r.message || {};
					frappe.model.set_value(cdt, cdn, 'block_name', d.block_name || '');
					frappe.model.set_value(cdt, cdn, 'block_area_acres', d.block_area_acres || 0);
					frappe.model.set_value(cdt, cdn, 'no_of_seedlings', d.no_of_seedlings || 0);
					frm.trigger('recompute_totals');
				}
			});
		}
	},

	blocks_remove(frm) {
		frm.trigger('recompute_totals');
	},

	'blocks.block'(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.block) {
			frappe.call({
				method: 'f2c.farm_scheduling.doctype.on_demand_activity.on_demand_activity.get_block_details',
				args: { block: row.block, field: frm.doc.field || null },
				callback(r) {
					const d = r.message || {};
					frappe.model.set_value(cdt, cdn, 'block_name', d.block_name || '');
					frappe.model.set_value(cdt, cdn, 'block_area_acres', d.block_area_acres || 0);
					frappe.model.set_value(cdt, cdn, 'no_of_seedlings', d.no_of_seedlings || 0);
					frm.trigger('recompute_totals');
				}
			});
		} else {
			frappe.model.set_value(cdt, cdn, 'block_name', '');
			frappe.model.set_value(cdt, cdn, 'block_area_acres', 0);
			frappe.model.set_value(cdt, cdn, 'no_of_seedlings', 0);
			frm.trigger('recompute_totals');
		}
	},

	planned_start(frm) {
		frm.trigger('validate_time_range');
	},

	planned_end(frm) {
		frm.trigger('validate_time_range');
	},

	water_requirement_basis(frm) {
		frm.trigger('recompute_water');
		frm.trigger('recompute_input_totals');
	},

	water_rate(frm) {
		frm.trigger('recompute_water');
		frm.trigger('recompute_input_totals');
	},

	is_spray(frm) {
		frm.trigger('recompute_water');
		frm.trigger('recompute_input_totals');
	},

	male_count(frm) {
		frm.trigger('recompute_labour_total');
	},

	female_count(frm) {
		frm.trigger('recompute_labour_total');
	},

	validate_time_range(frm) {
		if (frm.doc.planned_start && frm.doc.planned_end) {
			if (new Date(frm.doc.planned_end) <= new Date(frm.doc.planned_start)) {
				frappe.msgprint(__('Planned End must be after Planned Start'));
			}
		}
	},

	recompute_totals(frm) {
		let total_acres = 0;
		let total_seedlings = 0;
		(frm.doc.blocks || []).forEach(row => {
			total_acres += flt(row.block_area_acres || 0);
			total_seedlings += cint(row.no_of_seedlings || 0);
		});
		frm.set_value('total_acres', total_acres);
		frm.set_value('total_seedlings', total_seedlings);
		frm.trigger('recompute_water');
	},

	recompute_labour_total(frm) {
		frm.set_value('total_labour_count', cint(frm.doc.male_count || 0) + cint(frm.doc.female_count || 0));
	},

	recompute_water(frm) {
		let water = 0;
		if (frm.doc.water_requirement_basis === 'Per Acre') {
			water = flt(frm.doc.total_acres) * flt(frm.doc.water_rate || 0);
		} else if (frm.doc.water_requirement_basis === 'Per Plant') {
			water = cint(frm.doc.total_seedlings || 0) * flt(frm.doc.water_rate || 0);
		}
		frm.set_value('water_to_be_used_liters', water);
		frm.trigger('recompute_input_totals');
	},

	recompute_input_totals(frm) {
		// Only auto-calc for spray
		if (!frm.doc.is_spray) {
			(frm.doc.inputs || []).forEach(row => {
				frappe.model.set_value(row.doctype, row.name, 'total_quantity_to_use', 0);
				frappe.model.set_value(row.doctype, row.name, 'quantity_to_use_display', '');
			});
			return;
		}

		const water = flt(frm.doc.water_to_be_used_liters || 0);
		const acres = flt(frm.doc.total_acres || 0);
		(frm.doc.inputs || []).forEach(row => {
			const unit_raw = (row.unit || '');
			const unit = unit_raw.toLowerCase();
			let base_unit = unit_raw;
			if (unit === 'ml/l') base_unit = 'ml';
			else if (unit === 'g/l') base_unit = 'g';
			else if (unit === 'bags/acre') base_unit = 'Bags';

			let total = 0;
			if (unit === 'ml/l' || unit === 'g/l') {
				total = flt(row.rate_quantity || 0) * water;
			} else if (unit === 'bags/acre') {
				total = flt(row.rate_quantity || 0) * acres;
			}
			frappe.model.set_value(row.doctype, row.name, 'total_quantity_to_use', total);
			if (total && unit_raw) {
				frappe.model.set_value(
					row.doctype,
					row.name,
					'quantity_to_use_display',
					`${flt(total, 3)} ${base_unit} / ${flt(water, 3)} L`
				);
			} else {
				frappe.model.set_value(row.doctype, row.name, 'quantity_to_use_display', '');
			}
		});
	},

	check_tractor_selection(frm) {
		// Check if any machinery asset is a tractor
		let has_tractor = false;
		(frm.doc.machinery || []).forEach(row => {
			if (row.asset) {
				// Check if asset name contains "tractor" (case-insensitive)
				const asset_name = (row.asset_name || '').toLowerCase();
				if (asset_name.includes('tractor')) {
					has_tractor = true;
				}
			}
		});

		// Show/hide implements section
		if (has_tractor) {
			frm.set_df_property('section_break_implements', 'hidden', 0);
		} else {
			frm.set_df_property('section_break_implements', 'hidden', 1);
		}
		frm.refresh_field('implements');
	},

	'machinery.asset'(frm, cdt, cdn) {
		frm.trigger('check_tractor_selection');
	},

	'machinery.asset_name'(frm, cdt, cdn) {
		frm.trigger('check_tractor_selection');
	},

	machinery_remove(frm) {
		frm.trigger('check_tractor_selection');
	},
});


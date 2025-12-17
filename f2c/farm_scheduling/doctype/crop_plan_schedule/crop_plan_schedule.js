// Copyright (c) 2025

frappe.ui.form.on('Crop Plan Schedule', {
	setup(frm) {
		frm._activity_options_map = {};

		frm.set_query('block', function(doc) {
			// Filter blocks to those present in selected Crop Plan
			if (!doc.crop_plan) {
				return { filters: { name: ['=', ''] } };
			}
			return {
				query: 'f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.crop_plan_block_query',
				filters: {
					crop_plan: doc.crop_plan
				}
			};
		});

		frm.set_query('crop_plan_activity', function(doc) {
			if (!doc.crop_plan || !doc.block) {
				return { filters: { name: ['=', ''] } };
			}
			return {
				query: 'f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.crop_plan_activity_query',
				filters: {
					crop_plan: doc.crop_plan,
					block: doc.block
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
		frm.trigger('refresh_activity_options');

		if (!frm.is_new() && frm.doc.status !== 'Cancelled') {
			if (!frm.doc.execution_ref) {
				frm.add_custom_button(__('Create Execution'), function() {
					frappe.call({
						method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.create_from_schedule',
						args: { schedule_name: frm.doc.name },
						callback(r) {
							if (r.message) {
								frappe.set_route('Form', 'Farm Task Execution', r.message);
							}
						}
					});
				}, __('Actions'));
			}

			frm.add_custom_button(__('Reschedule'), function() {
				frappe.prompt(
					[
						{
							fieldname: 'reason',
							fieldtype: 'Small Text',
							label: __('Reschedule Reason')
						}
					],
					function(values) {
						frappe.call({
							method: 'f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.create_reschedule',
							args: {
								schedule_name: frm.doc.name,
								reschedule_reason: values.reason || ''
							},
							callback(r) {
								if (r.message) {
									frappe.set_route('Form', 'Crop Plan Schedule', r.message);
								}
							}
						});
					},
					__('Reschedule'),
					__('Create')
				);
			}, __('Actions'));
		}
	},

	crop_plan(frm) {
		// Clear dependent fields
		frm.set_value('block', '');
		frm.set_value('block_name', '');
		frm.set_value('block_area_acres', 0);
		frm.set_value('no_of_seedlings', 0);
		frm.set_value('crop_plan_activity', '');
		frm.set_value('farm_activity', '');
		frm.set_value('activity_name', '');
		frm.set_value('sequence', 0);
		frm.set_value('is_spray', 0);
		frm.set_value('approved_input_mix', '');
	},

	block(frm) {
		if (!frm.doc.crop_plan || !frm.doc.block) return;
		frappe.call({
			method: 'f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.get_block_details',
			args: { crop_plan: frm.doc.crop_plan, block: frm.doc.block },
			callback(r) {
				const d = r.message || {};
				frm.set_value('block_name', d.block_name || '');
				frm.set_value('block_area_acres', d.block_area_acres || 0);
				frm.set_value('no_of_seedlings', d.no_of_seedlings || 0);
				frm.trigger('recompute_totals');

				// block change invalidates selected activity
				frm.set_value('crop_plan_activity', '');
				frm.set_value('farm_activity', '');
				frm.set_value('activity_name', '');
				frm.set_value('sequence', 0);
				frm.set_value('is_spray', 0);
				frm.set_value('approved_input_mix', '');
			}
		});
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

	approved_input_mix(frm) {
		if (!frm.doc.approved_input_mix) {
			frm.clear_table('inputs');
			frm.refresh_field('inputs');
			return;
		}

		frappe.call({
			method: 'f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.get_farm_task_items',
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

	crop_plan_activity(frm) {
		if (!frm.doc.crop_plan || !frm.doc.crop_plan_activity) return;

		// Fetch activity details + approved mix from server (avoids frappe.client.get_value permission issues)
		frappe.call({
			method: 'f2c.farm_scheduling.doctype.crop_plan_schedule.crop_plan_schedule.get_schedule_defaults',
			args: {
				crop_plan: frm.doc.crop_plan,
				crop_plan_activity: frm.doc.crop_plan_activity
			},
			callback(r) {
				const d = r.message || {};
				if (d.sequence != null) frm.set_value('sequence', d.sequence);
				if (d.farm_activity) frm.set_value('farm_activity', d.farm_activity);
				if (d.activity_name != null) frm.set_value('activity_name', d.activity_name);
				if (d.is_spray != null) frm.set_value('is_spray', d.is_spray ? 1 : 0);
				if (d.approved_input_mix) frm.set_value('approved_input_mix', d.approved_input_mix);

				frm.trigger('recompute_water');
				frm.trigger('recompute_input_totals');
			}
		});
	},

	validate_time_range(frm) {
		if (frm.doc.planned_start && frm.doc.planned_end) {
			if (new Date(frm.doc.planned_end) < new Date(frm.doc.planned_start)) {
				frappe.msgprint(__('Planned End must be after Planned Start'));
			}
		}
	},

	recompute_totals(frm) {
		frm.set_value('total_acres', flt(frm.doc.block_area_acres));
		frm.set_value('total_seedlings', cint(frm.doc.no_of_seedlings));
		frm.trigger('recompute_water');
	},

	recompute_labour_total(frm) {
		frm.set_value('total_labour_count', cint(frm.doc.male_count) + cint(frm.doc.female_count));
	},

	recompute_water(frm) {
		let water = 0;
		if (frm.doc.water_requirement_basis === 'Per Acre') {
			water = flt(frm.doc.total_acres) * flt(frm.doc.water_rate);
		} else if (frm.doc.water_requirement_basis === 'Per Plant') {
			water = cint(frm.doc.total_seedlings) * flt(frm.doc.water_rate);
		}
		frm.set_value('water_to_be_used_liters', water);
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

		const water = flt(frm.doc.water_to_be_used_liters);
		const acres = flt(frm.doc.total_acres);
		(frm.doc.inputs || []).forEach(row => {
			const unit_raw = (row.unit || '');
			const unit = unit_raw.toLowerCase();
			let base_unit = unit_raw;
			if (unit === 'ml/l') base_unit = 'ml';
			else if (unit === 'g/l') base_unit = 'g';
			else if (unit === 'bags/acre') base_unit = 'Bags';

			let total = 0;
			if (unit === 'ml/l' || unit === 'g/l') {
				total = flt(row.rate_quantity) * water;
			} else if (unit === 'bags/acre') {
				total = flt(row.rate_quantity) * acres;
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

});


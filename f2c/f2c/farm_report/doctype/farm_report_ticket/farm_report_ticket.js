// Copyright (c) 2025, Orgatek and contributors

function set_report_module_from_refs(frm) {
	if (frm.doc.report_module) return;
	if (frm.doc.execution_ref) {
		frm.set_value('report_module', 'Execution');
	} else if (frm.doc.schedule_ref) {
		frm.set_value('report_module', 'Scheduling');
	} else if (frm.doc.on_demand_activity_ref) {
		frm.set_value('report_module', 'On Demand');
	}
}

frappe.ui.form.on('Farm Report Ticket', {
	setup(frm) {
		// Filter blocks to only show Block type geo fencing areas
		frm.set_query('block', function() {
			return {
				filters: {
					geo_fencing_type: 'Block'
				}
			};
		});
	},

	refresh(frm) {
		set_report_module_from_refs(frm);
		// Auto-fill asset_type when asset is selected in equipment table
		if (frm.doc.report_type === 'Inventory Failure' && frm.doc.equipment) {
			frm.doc.equipment.forEach(function(row) {
				if (row.asset && !row.asset_type) {
					frappe.db.get_value('Asset', row.asset, 'asset_category', function(r) {
						if (r && r.asset_category) {
							frappe.model.set_value(row.doctype, row.name, 'asset_type', r.asset_category);
						}
					});
				}
			});
		}
	},

	execution_ref(frm) { set_report_module_from_refs(frm); },
	schedule_ref(frm) { set_report_module_from_refs(frm); },
	on_demand_activity_ref(frm) { set_report_module_from_refs(frm); },

	report_type(frm) {
		// Clear conditional fields when report type changes
		if (frm.doc.report_type !== 'Inventory Failure') {
			frm.clear_table('equipment');
		}
		
		if (frm.doc.report_type !== 'Farm Worker') {
			frm.clear_table('list_of_labours');
		}
		
		frm.refresh();
	}
});

// Auto-fill asset_type when asset is selected in equipment child table
frappe.ui.form.on('Farm Report Equipment', {
	asset: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.asset) {
			frappe.db.get_value('Asset', row.asset, 'asset_category', function(r) {
				if (r && r.asset_category) {
					frappe.model.set_value(cdt, cdn, 'asset_type', r.asset_category);
				}
			});
		}
	}
});

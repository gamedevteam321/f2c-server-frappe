// Copyright (c) 2025, Orgatek and contributors

frappe.ui.form.on('Farm Report', {
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


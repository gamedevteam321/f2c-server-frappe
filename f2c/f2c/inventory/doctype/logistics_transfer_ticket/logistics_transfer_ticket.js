// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Logistics Transfer Ticket", {
	refresh: function(frm) {
		// Update balances when form is refreshed
		if (frm.doc.from_warehouse) {
			if (frm.doc.stock_items && frm.doc.stock_items.length > 0) {
				update_all_balances(frm);
			}
			if (frm.doc.asset_items && frm.doc.asset_items.length > 0) {
				update_all_asset_availability(frm);
			}
		}
		
		// Check for low stock and show warning
		check_low_stock_and_show_warning(frm);
		
		// When Assets table is empty, show message that equipment may already be at the field
		show_assets_section_message_when_empty(frm);
	},
	
	from_warehouse: function(frm) {
		// Update all balances when source warehouse changes
		if (frm.doc.from_warehouse) {
			if (frm.doc.stock_items && frm.doc.stock_items.length > 0) {
				update_all_balances(frm);
			}
			if (frm.doc.asset_items && frm.doc.asset_items.length > 0) {
				update_all_asset_availability(frm);
			}
		}
		
		// Check for low stock after balances are updated
		setTimeout(function() {
			check_low_stock_and_show_warning(frm);
		}, 500);
	}
});

function show_assets_section_message_when_empty(frm) {
	const selector = ".ltt-assets-empty-msg";
	const $aw = frm.fields_dict.asset_items && frm.fields_dict.asset_items.$wrapper;
	if (!$aw) return;

	$aw.prevAll(selector).remove();
	if (frm.doc.asset_items && frm.doc.asset_items.length > 0) return;

	const to_wh = (frm.doc.to_warehouse || "");
	const is_field = (/-f-\d+/.test(to_wh) || to_wh.toLowerCase().indexOf("field") !== -1);
	const msg = is_field
		? __("Equipment for this task is already at the field. This ticket is for inputs only.")
		: __("No equipment on this ticket. Equipment may already be at the destination, or this transfer is for inputs only.");

	const html = `<div class="ltt-assets-empty-msg" style="margin: 0 0 10px 0; padding: 10px 12px; background: #e7f3ff; border: 1px solid #b8daff; border-radius: 4px; color: #004085; font-size: 12px;">
		<span class="fa fa-info-circle"></span> ${msg}
	</div>`;
	$aw.before(html);
}

frappe.ui.form.on("Logistics Transfer Stock Item", {
	item_code: function(frm, cdt, cdn) {
		// Update balance when item is selected
		let row = locals[cdt][cdn];
		if (row.item_code && frm.doc.from_warehouse) {
			update_balance_for_row(frm, cdt, cdn);
		} else {
			frappe.model.set_value(cdt, cdn, "available_balance", null);
		}
	},
	
	stock_items_add: function(frm) {
		// Update all balances when a new row is added
		if (frm.doc.from_warehouse) {
			update_all_balances(frm);
		}
		// Check for low stock after balances are updated
		setTimeout(function() {
			check_low_stock_and_show_warning(frm);
		}, 500);
	},
	
	stock_items_remove: function(frm) {
		// No action needed on remove
	}
});

frappe.ui.form.on("Logistics Transfer Asset", {
	asset: function(frm, cdt, cdn) {
		// Update availability when asset is selected
		let row = locals[cdt][cdn];
		if (row.asset && frm.doc.from_warehouse) {
			update_asset_availability_for_row(frm, cdt, cdn);
		} else {
			frappe.model.set_value(cdt, cdn, "available_balance", null);
		}
	},
	
	asset_items_add: function(frm) {
		// Update all asset availability when a new row is added
		if (frm.doc.from_warehouse) {
			update_all_asset_availability(frm);
		}
	},
	
	asset_items_remove: function(frm) {
		// No action needed on remove
	}
});

function update_balance_for_row(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row.item_code || !frm.doc.from_warehouse) {
		return;
	}
	
	frappe.call({
		method: "f2c.inventory.logistics_transfer_ticket_api.get_available_balance",
		args: {
			item_code: row.item_code,
			warehouse: frm.doc.from_warehouse
		},
		callback: function(r) {
			if (r.message) {
				frappe.model.set_value(cdt, cdn, "available_balance", r.message);
			}
		}
	});
}

function update_all_balances(frm) {
	if (!frm.doc.from_warehouse || !frm.doc.stock_items) {
		return;
	}
	
	frm.doc.stock_items.forEach(function(row) {
		if (row.item_code) {
			frappe.call({
				method: "f2c.inventory.logistics_transfer_ticket_api.get_available_balance",
				args: {
					item_code: row.item_code,
					warehouse: frm.doc.from_warehouse
				},
				callback: function(r) {
					if (r.message) {
						frappe.model.set_value(row.doctype, row.name, "available_balance", r.message);
					}
				}
			});
		}
	});
}

function update_asset_availability_for_row(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row.asset || !frm.doc.from_warehouse) {
		return;
	}
	
	frappe.call({
		method: "f2c.inventory.logistics_transfer_ticket_api.get_asset_availability",
		args: {
			asset: row.asset,
			warehouse: frm.doc.from_warehouse
		},
		callback: function(r) {
			if (r.message) {
				frappe.model.set_value(cdt, cdn, "available_balance", r.message);
			}
		}
	});
}

function update_all_asset_availability(frm) {
	if (!frm.doc.from_warehouse || !frm.doc.asset_items) {
		return;
	}
	
	frm.doc.asset_items.forEach(function(row) {
		if (row.asset) {
			frappe.call({
				method: "f2c.inventory.logistics_transfer_ticket_api.get_asset_availability",
				args: {
					asset: row.asset,
					warehouse: frm.doc.from_warehouse
				},
				callback: function(r) {
					if (r.message) {
						frappe.model.set_value(row.doctype, row.name, "available_balance", r.message);
					}
				}
			});
		}
	});
}

function check_low_stock_and_show_warning(frm) {
	// Low-stock Material Request banner removed from logistics ticket UI; strip any legacy banner.
	if (frm.dashboard && frm.dashboard.wrapper) {
		frm.dashboard.wrapper.find(".low-stock-warning").remove();
	}
}

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
	if (!frm.doc.from_warehouse || !frm.doc.stock_items || frm.doc.stock_items.length === 0) {
		return;
	}
	
	// Check for items with low/zero balance
	let low_stock_items = [];
	frm.doc.stock_items.forEach(function(row) {
		if (!row.item_code) return;
		
		let available_balance = row.available_balance;
		if (available_balance) {
			// Try to parse as number
			let balance_float = parseFloat(available_balance);
			if (isNaN(balance_float) || balance_float <= 0) {
				// Check if it's a string like "Not Available"
				if (String(available_balance).toLowerCase() === "not available" || 
				    String(available_balance) === "0" || 
				    balance_float <= 0) {
					low_stock_items.push({
						item_code: row.item_code,
						item_name: row.item_name || row.item_code,
						qty: row.qty,
						available_balance: available_balance
					});
				}
			}
		} else {
			// No balance available
			low_stock_items.push({
				item_code: row.item_code,
				item_name: row.item_name || row.item_code,
				qty: row.qty,
				available_balance: "Unknown"
			});
		}
	});
	
	// Show warning if there are low stock items
	if (low_stock_items.length > 0) {
		let item_list = low_stock_items.map(function(item) {
			return item.item_name + " (Available: " + item.available_balance + ")";
		}).join(", ");
		
		let message = __("Warning: {0} item(s) have low or zero available balance: {1}. Consider creating a Material Request to request stock from cluster/farm warehouse.", 
			[low_stock_items.length, item_list]);
		
		// Remove existing warning if any
		if (frm.dashboard && frm.dashboard.wrapper) {
			frm.dashboard.wrapper.find(".low-stock-warning").remove();
		}
		
		// Add warning banner
		let warning_html = `
			<div class="low-stock-warning" style="margin: 10px 0; padding: 12px; background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px;">
				<div style="display: flex; justify-content: space-between; align-items: center;">
					<div style="flex: 1;">
						<strong style="color: #856404;">${message}</strong>
					</div>
					<button class="btn btn-sm btn-primary" onclick="create_material_request_from_ticket('${frm.doc.name || ''}')" style="margin-left: 10px;">
						${__("Create Material Request")}
					</button>
				</div>
			</div>
		`;
		
		// Insert warning at the top of the form
		if (frm.dashboard && frm.dashboard.wrapper) {
			frm.dashboard.wrapper.prepend(warning_html);
		} else {
			// Fallback: show as message
			frappe.show_alert({
				message: message,
				indicator: "orange"
			}, 10);
		}
	} else {
		// Remove warning if no low stock items
		if (frm.dashboard && frm.dashboard.wrapper) {
			frm.dashboard.wrapper.find(".low-stock-warning").remove();
		}
	}
}

// Global function to create Material Request from ticket
window.create_material_request_from_ticket = function(ticket_name) {
	if (!ticket_name) {
		frappe.msgprint(__("Please save the ticket first before creating Material Request"));
		return;
	}
	
	frappe.confirm(
		__("Create Material Request for items with low stock?"),
		function() {
			// Yes
			frappe.call({
				method: "f2c.inventory.material_request_api.create_material_request_from_ticket",
				args: {
					ticket_name: ticket_name,
					material_request_type: "Material Transfer"
				},
				callback: function(r) {
					if (r.message && r.message.material_request) {
						frappe.msgprint({
							message: __("Material Request {0} created successfully", [r.message.material_request]),
							indicator: "green"
						});
						// Open Material Request
						frappe.set_route("Form", "Material Request", r.message.material_request);
					} else {
						frappe.msgprint(__("Failed to create Material Request"));
					}
				},
				error: function(r) {
					frappe.msgprint(__("Error creating Material Request: {0}", [r.message || "Unknown error"]));
				}
			});
		},
		function() {
			// No
		}
	);
};

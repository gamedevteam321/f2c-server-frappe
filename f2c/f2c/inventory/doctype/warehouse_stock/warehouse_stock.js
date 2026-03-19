frappe.ui.form.on('Warehouse Stock', {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__('Refresh from Ledger'), function () {
			frappe.call({
				method: 'f2c.inventory.doctype.warehouse_stock.warehouse_stock.refresh_from_ledger',
				args: { warehouse_stock_name: frm.doc.name },
				callback(r) {
					if (r.message) frm.reload_doc();
				}
			});
		}, __('Actions'));

		frm.add_custom_button(__('Sync Warehouses (Auto Create)'), function () {
			frappe.call({
				method: 'f2c.inventory.doctype.warehouse_stock.warehouse_stock.sync_warehouse_stock',
				args: { refresh_existing: 0 },
				callback() {
					frm.reload_doc();
				}
			});
		}, __('Actions'));
	}
});



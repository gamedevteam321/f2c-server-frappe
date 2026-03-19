frappe.listview_settings['Warehouse Stock'] = {
	onload(listview) {
		listview.page.add_inner_button(__('Refresh Warehouses with Stock'), () => {
			frappe.call({
				method: 'f2c.inventory.doctype.warehouse_stock.warehouse_stock.sync_warehouse_stock_async',
				args: { refresh_existing: 1 },
				callback(r) {
					if (r.message?.enqueued) {
						frappe.show_alert({
							message: __('Sync started. Warehouses list updated; item rows will populate in background.'),
							indicator: 'green'
						});
					}
					listview.refresh();
				}
			});
		});
	}
};



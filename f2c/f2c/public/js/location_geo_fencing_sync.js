frappe.ui.form.on("Location", {
	refresh(frm) {
		// Keep this button available from any Location form (even new ones)
		frm.add_custom_button(__("Create Locations from Geo Warehouses"), async () => {
			const confirmed = await new Promise((resolve) => {
				frappe.confirm(
					__(
						"This will create ERPNext Locations for all Warehouses linked in Geo Fencing Areas. Existing Location names will be skipped. Continue?"
					),
					() => resolve(true),
					() => resolve(false)
				);
			});
			if (!confirmed) return;

			frappe.call({
				method: "f2c.farm_to_crop.location_sync.create_locations_from_geo_warehouses",
				args: { update_existing: 1 },
				freeze: true,
				freeze_message: __("Creating Locations…"),
				callback(r) {
					const msg = r?.message || {};
					const created = msg.created || 0;
					const skipped = msg.skipped_existing || 0;
					const updated = msg.updated_existing || 0;
					const errors = msg.errors || [];

					let html = `<div><b>Created:</b> ${created}</div><div><b>Updated existing (backfilled coords):</b> ${updated}</div><div><b>Skipped:</b> ${skipped}</div>`;
					if (errors.length) {
						html += `<hr/><div><b>Errors:</b></div><ul>${errors
							.map((e) => `<li>${frappe.utils.escape_html(e)}</li>`)
							.join("")}</ul>`;
					}

					frappe.msgprint({
						title: __("Geo Warehouse → Location Sync"),
						message: html,
						indicator: errors.length ? "orange" : "green",
					});
				},
			});
		});
	},
});



// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Farm Project", {
    refresh: function (frm) {
        // Set filter for geo_areas to show only root nodes (Farm type)
        frm.set_query("geo_fencing_area", "geo_areas", function () {
            return {
                filters: {
                    "level_sequence": 1
                }
            };
        });
    }
});

frappe.ui.form.on("Farm Project Geo Area", {
    geo_fencing_area: function (frm, cdt, cdn) {
        // Validate that selected area is a root node
        let row = locals[cdt][cdn];
        if (row.geo_fencing_area) {
            frappe.db.get_value("Geo Fencing Area", row.geo_fencing_area, ["level_sequence", "geo_fencing_type", "area_name"], (r) => {
                if (r && r.level_sequence !== 1) {
                    frappe.msgprint({
                        title: __("Invalid Selection"),
                        indicator: "red",
                        message: __(`Only root geo fencing areas (Farm type) can be added to a project. '${r.area_name}' is a ${r.geo_fencing_type} (Level ${r.level_sequence}).`)
                    });
                    frappe.model.set_value(cdt, cdn, "geo_fencing_area", "");
                }
            });
        }
    }
});


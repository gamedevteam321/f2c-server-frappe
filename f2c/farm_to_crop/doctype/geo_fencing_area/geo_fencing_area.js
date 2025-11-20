// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Geo Fencing Area", {
    refresh: function (frm) {
        // Add custom buttons or actions here if needed
    },

    geo_fencing_type: function (frm) {
        // Auto-set level sequence when type changes
        if (frm.doc.geo_fencing_type) {
            const level_map = {
                "Farm": 1,
                "Cluster": 2,
                "Field": 3,
                "Plot": 4,
                "Block": 5,
                "Row": 6
            };
            frm.set_value("level_sequence", level_map[frm.doc.geo_fencing_type] || 0);
        }
    },

    parent_area: function (frm) {
        // Validate parent selection
        if (frm.doc.parent_area && frm.doc.geo_fencing_type) {
            frappe.db.get_value("Geo Fencing Area", frm.doc.parent_area, ["geo_fencing_type", "level_sequence"], (r) => {
                if (r) {
                    const expected_level = (r.level_sequence || 0) + 1;
                    const current_level = frm.doc.level_sequence || 0;

                    if (current_level !== expected_level) {
                        frappe.msgprint({
                            title: __("Invalid Hierarchy"),
                            indicator: "red",
                            message: __(`${frm.doc.geo_fencing_type} (Level ${current_level}) cannot be a child of ${r.geo_fencing_type} (Level ${r.level_sequence}). Expected level ${expected_level}.`)
                        });
                        frm.set_value("parent_area", "");
                    }
                }
            });
        }
    }
});

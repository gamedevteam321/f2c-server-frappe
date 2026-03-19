// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Farm Activity", {
    refresh: function(frm) {
        // Customize parent_activity field to show activity_name in dropdown
        frm.set_query("parent_activity", function() {
            return {
                query: "f2c.farm_to_crop.doctype.farm_activity.farm_activity.get_parent_activities",
                filters: {
                    name: frm.doc.name || ""
                }
            };
        });
        
        // Format the parent_activity field to show activity_name after load
        if (frm.doc.parent_activity) {
            format_parent_activity_display(frm);
        }

		// Suggested equipment: show/hide Implements based on tractor suggestion in Machinery
		frm.trigger("check_tractor_suggestion");

		// Suggested equipment: filter assets by category per section
		frm.trigger("setup_suggested_equipment_queries");
    },
    
    parent_activity: function(frm) {
        // Update the display to show activity_name when parent is selected
        format_parent_activity_display(frm);
        
        // Auto-calculate sequence based on parent
        if (frm.doc.parent_activity) {
            frappe.db.get_value("Farm Activity", frm.doc.parent_activity, "sequence", (r) => {
                if (r && r.sequence) {
                    // Set sequence to parent's sequence + 1
                    frm.set_value("sequence", r.sequence + 1);
                }
            });
        } else {
            // If no parent, set sequence to 1
            if (!frm.doc.sequence || frm.doc.sequence > 1) {
                frm.set_value("sequence", 1);
            }
        }
    },
    
    sequence: function(frm) {
        // Validate sequence is positive
        if (frm.doc.sequence && frm.doc.sequence < 1) {
            frappe.msgprint("Sequence must be a positive integer");
            frm.set_value("sequence", 1);
        }
    },

	check_tractor_suggestion: function(frm) {
		let has_tractor = false;
		(frm.doc.suggested_machinery || []).forEach(row => {
			const name = (row.asset_name || "").toLowerCase();
			if (name.includes("tractor")) {
				has_tractor = true;
			}
		});

		// Hide both the section and the table field
		frm.set_df_property("section_break_suggested_implements", "hidden", has_tractor ? 0 : 1);
		frm.set_df_property("suggested_implements", "hidden", has_tractor ? 0 : 1);
		frm.refresh_field("suggested_implements");
	},

	setup_suggested_equipment_queries: function(frm) {
		// Machinery
		frm.set_query("asset", "suggested_machinery", function() {
			return {
				filters: {
					asset_category: ["like", "%Machinery%"]
				}
			};
		});

		// Implements
		frm.set_query("asset", "suggested_implements", function() {
			return {
				filters: {
					asset_category: ["like", "%Implement%"]
				}
			};
		});

		// Hand Tools
		frm.set_query("asset", "suggested_hand_tools", function() {
			return {
				filters: {
					asset_category: ["like", "%Hand Tool%"]
				}
			};
		});

		// Other Tools
		frm.set_query("asset", "suggested_other_tools", function() {
			return {
				filters: {
					asset_category: ["like", "%Other Tool%"]
				}
			};
		});
	},

	"suggested_machinery_add": function(frm) {
		frm.trigger("check_tractor_suggestion");
	},

	"suggested_machinery_remove": function(frm) {
		frm.trigger("check_tractor_suggestion");
	},
});

function format_parent_activity_display(frm) {
    if (frm.doc.parent_activity) {
        frappe.db.get_value("Farm Activity", frm.doc.parent_activity, "activity_name", (r) => {
            if (r && r.activity_name) {
                // Update the field description to show activity name
                let field = frm.get_field("parent_activity");
                if (field) {
                    // Set description to show activity name
                    $(field.disp_area).find(".link-display").text(r.activity_name);
                }
            }
        });
    }
}

// Child table field handler (Link -> Asset Category)
frappe.ui.form.on("Farm Activity Suggested Machinery", {
	asset: function(frm) {
		frm.trigger("check_tractor_suggestion");
	},
});


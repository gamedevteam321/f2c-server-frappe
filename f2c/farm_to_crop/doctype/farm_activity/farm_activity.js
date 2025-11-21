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
    }
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


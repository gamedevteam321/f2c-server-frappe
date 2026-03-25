// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Activity Implementation", {
    refresh: function(frm) {
        // Customize activity_name field to show activity_name in dropdown
        frm.set_query("activity_name", function() {
            return {
                query: "f2c.farm_to_crop.doctype.farm_activity.farm_activity.get_parent_activities"
            };
        });
        
        // Format the activity_name field to show activity_name after load
        if (frm.doc.activity_name) {
            format_activity_name_display(frm);
        }
    },
    
    activity_name: function(frm) {
        // Update the display to show activity_name when selected
        format_activity_name_display(frm);
    }
});

function format_activity_name_display(frm) {
    if (frm.doc.activity_name) {
        frappe.db.get_value("Farm Activity", frm.doc.activity_name, "activity_name", (r) => {
            if (r && r.activity_name) {
                // Update the field description to show activity name
                let field = frm.get_field("activity_name");
                if (field) {
                    // Set description to show activity name
                    $(field.disp_area).find(".link-display").text(r.activity_name);
                }
            }
        });
    }
}

// The activity_name_display field will automatically fetch and display the activity name
// No additional JavaScript needed as fetch_from handles it


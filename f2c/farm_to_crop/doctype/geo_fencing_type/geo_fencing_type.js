// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Geo Fencing Type", {
    refresh: function(frm) {
        // Set default colors based on geo fencing type
        if (frm.is_new() && frm.doc.geo_fencing_type_name && !frm.doc.color) {
            const defaultColors = {
                'Farm': '#3b82f6',      // Blue
                'Cluster': '#10b981',   // Green
                'Field': '#f59e0b',     // Orange
                'Plot': '#ef4444',      // Red
                'Block': '#8b5cf6',     // Purple
                'Row': '#ec4899'        // Pink
            };
            
            if (defaultColors[frm.doc.geo_fencing_type_name]) {
                frm.set_value('color', defaultColors[frm.doc.geo_fencing_type_name]);
            }
        }
    },
    
    geo_fencing_type_name: function(frm) {
        // Auto-set color when type is selected (only if color is not already set)
        if (frm.is_new() && frm.doc.geo_fencing_type_name && !frm.doc.color) {
            const defaultColors = {
                'Farm': '#3b82f6',      // Blue
                'Cluster': '#10b981',   // Green
                'Field': '#f59e0b',     // Orange
                'Plot': '#ef4444',      // Red
                'Block': '#8b5cf6',     // Purple
                'Row': '#ec4899'        // Pink
            };
            
            if (defaultColors[frm.doc.geo_fencing_type_name]) {
                frm.set_value('color', defaultColors[frm.doc.geo_fencing_type_name]);
            }
        }
    }
});

// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Geo Fencing Area", {
    refresh: function (frm) {
        // Add button to pick area center location (for Circle type)
        if (frm.doc.shape_type === 'Circle') {
            frm.add_custom_button(__('📍 Pick Area Location on Map'), function() {
                open_area_map_picker(frm);
            }, __('Map Tools'));
        }
        
        // Add button to view all locations on map (show for all types)
        frm.add_custom_button(__('🗺️ View All Locations on Map'), function() {
            open_combined_map_view(frm);
        }, __('Map Tools'));
        
        // Add button to fetch weather data for this field
        if (!frm.is_new()) {
            frm.add_custom_button(__('🌤️ Fetch Weather Data'), function() {
                fetch_weather_for_this_field(frm);
            }, __('Weather'));
            
            // Add button to view latest weather report
            frm.add_custom_button(__('📊 View Latest Weather'), function() {
                view_latest_weather(frm);
            }, __('Weather'));
        }
        
        // Add map picker buttons to warehouse child table (only if not already added)
        if (!frm._warehouse_map_setup_done) {
            frm._warehouse_map_setup_done = true;
            setTimeout(() => {
                setup_warehouse_map_buttons(frm);
            }, 500);
        }
        
        // Setup image formatter for warehouses grid
        if (frm.fields_dict.warehouses && frm.fields_dict.warehouses.grid) {
            setup_warehouse_images_formatter(frm);
        }
    },
    
    warehouses: function(frm) {
        // When warehouses field changes, setup buttons
        // Don't call setup here as it causes duplicates
    },

    geo_fencing_type: function (frm) {
        // Auto-set level sequence and fetch shape type when type changes
        if (frm.doc.geo_fencing_type) {
            const level_map = {
                "Farm": 1,
                "Cluster": 2,
                "Field": 3,
                "Block": 4,
                "Row": 5,
                "Plot": 99  // Deprecated - not used in hierarchy
            };
            frm.set_value("level_sequence", level_map[frm.doc.geo_fencing_type] || 0);
            
            // Fetch shape type and has_warehouse from linked Geo Fencing Type
            frappe.db.get_value("Geo Fencing Type", frm.doc.geo_fencing_type, ["shape_type", "has_warehouse"], (r) => {
                if (r) {
                    if (r.shape_type) {
                        frm.set_value("shape_type", r.shape_type);
                    }
                    if (r.has_warehouse !== undefined) {
                        frm.set_value("has_warehouse", r.has_warehouse);
                    }
                }
            });
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

// Function to setup map buttons for all warehouse rows
function setup_warehouse_map_buttons(frm) {
    if (!frm.fields_dict.warehouses || !frm.fields_dict.warehouses.grid) {
        return;
    }
    
    let grid = frm.fields_dict.warehouses.grid;
    
    if (!grid.wrapper) {
        return;
    }
    
    // CRITICAL: Remove ALL existing map buttons from everywhere
    $(grid.wrapper).find('.btn-open-warehouse-map').remove();
    
    // Check if we already added the button (using a flag on the grid object)
    if (grid._warehouse_map_button_added) {
        return; // Button already exists, don't add again
    }
    
    // Mark that we're adding the button
    grid._warehouse_map_button_added = true;
    
    // Find the grid footer/buttons area
    let $grid_footer = $(grid.wrapper).find('.grid-footer').first();
    if (!$grid_footer.length) {
        $grid_footer = $(grid.wrapper).find('.grid-buttons').first();
    }
    
    if (!$grid_footer.length) {
        grid._warehouse_map_button_added = false; // Reset if we can't find the footer
        return;
    }
    
    // Create the button (only once)
    let $map_btn = $('<button class="btn btn-xs btn-default btn-open-warehouse-map" ' +
        'style="margin-right: 8px; background-color: #4A90E2; color: white; border: none;">' +
        '<svg class="icon icon-sm" style="margin-right: 4px;"><use href="#icon-map-pin"></use></svg> ' +
        'Pick Location on Map</button>');
    
    $map_btn.on('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        
        // Get the currently selected/focused row
        let selected_row = grid.get_selected_children();
        if (selected_row && selected_row.length > 0) {
            let grid_row = grid.grid_rows_by_docname[selected_row[0].name];
            if (grid_row) {
                open_map_picker(grid_row, frm);
            }
        } else {
            // If no row selected, show message
            if (grid.grid_rows && grid.grid_rows.length > 0) {
                frappe.msgprint({
                    title: __('Select a Warehouse'),
                    indicator: 'blue',
                    message: __('Please check the checkbox next to the warehouse row you want to map, then click this button.')
                });
            } else {
                frappe.msgprint(__('Please add a warehouse row first'));
            }
        }
    });
    
    // Add hover effect
    $map_btn.hover(
        function() {
            $(this).css('background-color', '#357ABD');
        },
        function() {
            $(this).css('background-color', '#4A90E2');
        }
    );
    
    // Insert button before the delete button
    let $delete_btn = $grid_footer.find('.grid-remove-rows, .btn-danger').first();
    if ($delete_btn.length) {
        $delete_btn.before($map_btn);
    } else {
        $grid_footer.prepend($map_btn);
    }
    
    // Setup buttons for existing rows (for expanded form view only)
    if (grid.grid_rows) {
        grid.grid_rows.forEach(function(row) {
            add_map_button_to_row(row, frm);
        });
    }
    
    // Setup when rows are toggled/expanded (only once)
    if (!grid._warehouse_map_event_setup) {
        grid._warehouse_map_event_setup = true;
        $(grid.wrapper).on('click.warehouse_map', '.toggle-view', function() {
            setTimeout(() => {
                if (grid.grid_rows) {
                    grid.grid_rows.forEach(function(row) {
                        add_map_button_to_row(row, frm);
                    });
                }
            }, 300);
        });
    }
}

// Function to add inline map button in grid row
function add_inline_map_button(grid_row, frm) {
    if (!grid_row || !grid_row.wrapper) {
        return;
    }
    
    setTimeout(() => {
        let $row = $(grid_row.wrapper);
        
        // Check if button already exists
        if ($row.find('.btn-inline-map-picker').length) {
            return;
        }
        
        // Add button after the warehouse field or at the end of the row
        let $grid_row = $row.find('.grid-row, .data-row').first();
        if ($grid_row.length) {
            // Find the actions column or add at the end
            let $actions = $grid_row.find('.col[data-fieldname=""]').first();
            if (!$actions.length) {
                $actions = $grid_row.find('.row-index').first();
            }
            
            if ($actions.length && !$actions.find('.btn-inline-map-picker').length) {
                let $map_icon = $('<button class="btn btn-xs btn-inline-map-picker" ' +
                    'style="margin-left: 4px; padding: 2px 6px; background: transparent; border: none; color: #4A90E2;" ' +
                    'title="Pick location on map">' +
                    '<svg class="icon icon-sm" style="width: 14px; height: 14px;"><use href="#icon-map-marker"></use></svg>' +
                    '</button>');
                
                $map_icon.on('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    open_map_picker(grid_row, frm);
                });
                
                $actions.append($map_icon);
            }
        }
    }, 100);
}

// Child table event for warehouses
frappe.ui.form.on("Geo Fencing Area Warehouse", {
    form_render: function(frm, cdt, cdn) {
        // Add map button when row form is rendered (expanded view)
        setTimeout(() => {
            let row = locals[cdt][cdn];
            if (frm.fields_dict.warehouses && frm.fields_dict.warehouses.grid) {
                let grid_row = frm.fields_dict.warehouses.grid.grid_rows_by_docname[row.name];
                if (grid_row) {
                    add_map_button_to_row(grid_row, frm);
                }
            }
        }, 150);
        
        // Setup image display for images field
        setup_images_display(frm, cdt, cdn);
    },
    warehouses_add: function(frm, cdt, cdn) {
        // Don't setup buttons here to avoid duplicates
        // The main button in grid footer will handle all rows
    },
    images: function(frm, cdt, cdn) {
        // Refresh image display when images field changes
        setup_images_display(frm, cdt, cdn);
    }
});

// Function to setup image display for warehouse images field
function setup_images_display(frm, cdt, cdn) {
    setTimeout(() => {
        let row = locals[cdt][cdn];
        if (!row || !row.images) {
            return;
        }
        
        // Find the images field wrapper
        let field_wrapper = $(`[data-fieldname="images"][data-doctype="${cdt}"][data-name="${cdn}"]`);
        if (!field_wrapper.length) {
            // Try alternative selector
            field_wrapper = $(`[data-fieldname="images"]`).filter(function() {
                return $(this).closest('[data-doctype]').attr('data-doctype') === cdt &&
                       $(this).closest('[data-name]').attr('data-name') === cdn;
            });
        }
        
        if (!field_wrapper.length) {
            return;
        }
        
        // Remove existing image display if any
        field_wrapper.find('.warehouse-images-gallery').remove();
        
        // Parse comma-separated image URLs
        let image_urls = row.images.split(',').map(url => url.trim()).filter(url => url);
        
        if (image_urls.length === 0) {
            return;
        }
        
        // Create image gallery
        let gallery_html = '<div class="warehouse-images-gallery" style="margin-top: 10px; display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 10px;">';
        
        image_urls.forEach((url, index) => {
            // Construct full URL if needed
            let full_url = url;
            if (!url.startsWith('http') && !url.startsWith('/')) {
                full_url = '/' + url;
            } else if (url.startsWith('/files/')) {
                // Already a valid path
            }
            
            gallery_html += `
                <div style="position: relative; width: 100%; padding-top: 100%; background: #f0f0f0; border-radius: 4px; overflow: hidden; border: 1px solid #ddd; cursor: pointer;" 
                     onclick="window.open('${full_url}', '_blank')"
                     title="Click to view full size">
                    <img src="${full_url}" 
                         alt="Warehouse image ${index + 1}"
                         style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;"
                         onerror="this.style.display='none'; this.parentElement.innerHTML='<div style=\\'position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #999; font-size: 12px;\\'>Image not found</div>';">
                </div>
            `;
        });
        
        gallery_html += '</div>';
        
        // Insert gallery after the input field
        let input_field = field_wrapper.find('input, textarea');
        if (input_field.length) {
            input_field.after(gallery_html);
        } else {
            field_wrapper.append(gallery_html);
        }
    }, 200);
}

// Function to add map button to a grid row
function add_map_button_to_row(grid_row, frm) {
    if (!grid_row) {
        return;
    }
    
    // Wait for row to be fully rendered
    setTimeout(() => {
        if (!grid_row.$wrapper || !grid_row.$wrapper.length) {
            return;
        }
        
        let $row_wrapper = grid_row.$wrapper;
        
        // Check if button already exists
        if ($row_wrapper.find('.warehouse-map-picker-btn').length) {
            return;
        }
        
        // Find the center_latitude field to add button near it
        let $lat_field = $row_wrapper.find('[data-fieldname="center_latitude"]');
        let $lng_field = $row_wrapper.find('[data-fieldname="center_longitude"]');
        
        if ($lat_field.length || $lng_field.length) {
            // Find the parent form group
            let $target_field = $lat_field.length ? $lat_field : $lng_field;
            let $form_group = $target_field.closest('.form-group, .grid-form-row');
            
            if ($form_group.length) {
                // Create button container
                let $button_container = $('<div class="form-group" style="margin-top: 10px; clear: both;"></div>');
                let $map_button = $('<button class="btn btn-sm btn-secondary warehouse-map-picker-btn" type="button" style="width: 100%; max-width: 200px;">📍 Pick Location on Map</button>');
                
                $button_container.append($map_button);
                
                // Insert after the longitude field's form group
                if ($lng_field.length) {
                    $lng_field.closest('.form-group, .grid-form-row').after($button_container);
                } else {
                    $form_group.after($button_container);
                }
                
                // Bind click event
                $map_button.on('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    open_map_picker(grid_row, frm);
                });
                
                return;
            }
        }
        
        // Fallback: Add button at the end of the row
        let $form_section = $row_wrapper.find('.form-section, .grid-form-row, .row-content').first();
        if ($form_section.length) {
            let $button_container = $('<div class="form-group" style="margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 4px;"></div>');
            let $map_button = $('<button class="btn btn-sm btn-secondary warehouse-map-picker-btn" type="button" style="width: 100%;">📍 Pick Location on Map</button>');
            
            $button_container.append($('<label class="control-label" style="display: block; margin-bottom: 5px; font-weight: 600;">Location Picker</label>'));
            $button_container.append($map_button);
            $form_section.append($button_container);
            
            // Bind click event
            $map_button.on('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                open_map_picker(grid_row, frm);
            });
        }
    }, 200);
}

// Function to setup image formatter for warehouses grid
function setup_warehouse_images_formatter(frm) {
    if (!frm.fields_dict.warehouses || !frm.fields_dict.warehouses.grid) {
        return;
    }
    
    let grid = frm.fields_dict.warehouses.grid;
    
    // Add custom formatter for images field in grid
    if (grid.meta && grid.meta.fields) {
        let images_field = grid.meta.fields.find(f => f.fieldname === 'images');
        if (images_field) {
            images_field.formatter = function(value, row, column, data, default_formatter) {
                if (!value) {
                    return '<span style="color: #999;">-</span>';
                }
                
                let image_urls = value.split(',').map(url => url.trim()).filter(url => url);
                if (image_urls.length === 0) {
                    return '<span style="color: #999;">-</span>';
                }
                
                // Show first image as thumbnail with count badge
                let first_url = image_urls[0];
                if (!first_url.startsWith('http') && !first_url.startsWith('/')) {
                    first_url = '/' + first_url;
                }
                
                let badge_html = image_urls.length > 1 
                    ? `<span style="position: absolute; top: -5px; right: -5px; background: #2ecc71; color: white; border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold;">+${image_urls.length - 1}</span>`
                    : '';
                
                return `
                    <div style="position: relative; display: inline-block; width: 50px; height: 50px; border-radius: 4px; overflow: hidden; border: 1px solid #ddd;">
                        <img src="${first_url}" 
                             alt="Warehouse images"
                             style="width: 100%; height: 100%; object-fit: cover;"
                             onerror="this.style.display='none'; this.parentElement.innerHTML='<div style=\\'width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background: #f0f0f0; color: #999; font-size: 10px;\\'>${image_urls.length} image(s)</div>';">
                        ${badge_html}
                    </div>
                `;
            };
        }
    }
}

// Function to load Google Maps script dynamically
function load_google_maps(callback) {
    if (typeof google !== 'undefined' && typeof google.maps !== 'undefined') {
        callback();
        return;
    }
    
    // Get API key from Python method
    frappe.call({
        method: 'f2c.farm_to_crop.doctype.geo_fencing_area.geo_fencing_area.get_google_maps_api_key',
        callback: function(r) {
            let api_key = (r && r.message) || '';
            
            if (!api_key || api_key === 'YOUR_GOOGLE_MAPS_API_KEY_HERE') {
                frappe.msgprint({
                    title: __('Configuration Required'),
                    indicator: 'orange',
                    message: __('Please add your Google Maps API key to site_config.json as "google_maps_api_key"')
                });
                return;
            }
            
            // Check if script already exists
            if (document.querySelector('script[src*="maps.googleapis.com"]')) {
                // Script exists but not loaded yet, wait a bit
                let checkInterval = setInterval(function() {
                    if (typeof google !== 'undefined' && typeof google.maps !== 'undefined') {
                        clearInterval(checkInterval);
                        callback();
                    }
                }, 100);
                setTimeout(() => clearInterval(checkInterval), 5000);
                return;
            }
            
            // Load Google Maps script
            let script = document.createElement('script');
            script.src = `https://maps.googleapis.com/maps/api/js?key=${api_key}&libraries=drawing&callback=initGoogleMaps`;
            script.async = true;
            script.defer = true;
            
            window.initGoogleMaps = function() {
                callback();
            };
            
            script.onerror = function() {
                frappe.msgprint({
                    title: __('Error'),
                    indicator: 'red',
                    message: __('Failed to load Google Maps. Please check your API key in site_config.json')
                });
            };
            
            document.head.appendChild(script);
        }
    });
}

// Function to open map picker dialog
function open_map_picker(grid_row, frm) {
    let row_data = grid_row.doc;
    let current_lat = row_data.center_latitude || (frm.doc.center_latitude || 20.5937);
    let current_lng = row_data.center_longitude || (frm.doc.center_longitude || 78.9629);
    let current_radius = row_data.radius || 100;
    
    let d = new frappe.ui.Dialog({
        title: __('Pick Warehouse Location'),
        size: 'extra-large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'map_html'
            },
            {
                fieldtype: 'Column Break'
            },
            {
                fieldtype: 'Float',
                fieldname: 'latitude',
                label: 'Latitude',
                default: current_lat,
                precision: 8
            },
            {
                fieldtype: 'Float',
                fieldname: 'longitude',
                label: 'Longitude',
                default: current_lng,
                precision: 8
            },
            {
                fieldtype: 'Float',
                fieldname: 'radius',
                label: 'Radius (meters)',
                default: current_radius,
                precision: 2
            }
        ],
        primary_action_label: __('Set Location'),
        primary_action: function(values) {
            frappe.model.set_value(row_data.doctype, row_data.name, 'center_latitude', values.latitude);
            frappe.model.set_value(row_data.doctype, row_data.name, 'center_longitude', values.longitude);
            frappe.model.set_value(row_data.doctype, row_data.name, 'radius', values.radius);
            frm.refresh_field('warehouses');
            d.hide();
        }
    });
    
    d.show();
    
    // Initialize map after dialog is shown
    setTimeout(() => {
        let map_html = `
            <div id="warehouse_map" style="height: 500px; width: 100%; border: 1px solid #d1d8dd; border-radius: 4px;"></div>
            <div style="margin-top: 10px; padding: 10px; background: #f7fafc; border-radius: 4px;">
                <p style="margin: 0; font-size: 12px; color: #6c757d;">
                    <strong>Instructions:</strong> Click on the map to set the warehouse center location. 
                    The circle shows the warehouse coverage area based on the radius.
                </p>
            </div>
        `;
        d.fields_dict.map_html.$wrapper.html(map_html);
        
        // Load Google Maps script dynamically
        load_google_maps(function() {
            if (typeof google === 'undefined' || typeof google.maps === 'undefined') {
                frappe.msgprint({
                    title: __('Error'),
                    indicator: 'red',
                    message: __('Google Maps failed to load. Please check your API key in site_config.json')
                });
                return;
            }
            
            initialize_map(d, current_lat, current_lng, current_radius, row_data, frm);
        });
    }, 300);
}

// Function to initialize the map
function initialize_map(dialog, current_lat, current_lng, current_radius, row_data, frm) {
    
    // Initialize map
    let map = new google.maps.Map(document.getElementById('warehouse_map'), {
        center: { lat: current_lat, lng: current_lng },
        zoom: 15,
        mapTypeId: 'satellite'
    });
    
    // Add marker for center
    let marker = new google.maps.Marker({
        position: { lat: current_lat, lng: current_lng },
        map: map,
        draggable: true,
        title: 'Warehouse Center'
    });
    
    // Add circle for radius
    let circle = new google.maps.Circle({
        map: map,
        center: { lat: current_lat, lng: current_lng },
        radius: current_radius,
        fillColor: '#FF6B6B',
        fillOpacity: 0.2,
        strokeColor: '#FF6B6B',
        strokeOpacity: 0.8,
        strokeWeight: 2
    });
    
    // Update fields when marker is dragged
    marker.addListener('dragend', function() {
        let pos = marker.getPosition();
        dialog.set_value('latitude', pos.lat());
        dialog.set_value('longitude', pos.lng());
        circle.setCenter(pos);
    });
    
    // Update marker and circle when clicking on map
    map.addListener('click', function(e) {
        marker.setPosition(e.latLng);
        circle.setCenter(e.latLng);
        dialog.set_value('latitude', e.latLng.lat());
        dialog.set_value('longitude', e.latLng.lng());
    });
    
    // Update circle radius when radius field changes
    dialog.fields_dict.radius.$input.on('change', function() {
        let new_radius = parseFloat(dialog.get_value('radius')) || 100;
        circle.setRadius(new_radius);
    });
    
    // Update marker when lat/lng fields change
    dialog.fields_dict.latitude.$input.on('change', function() {
        let lat = parseFloat(dialog.get_value('latitude'));
        let lng = parseFloat(dialog.get_value('longitude'));
        if (!isNaN(lat) && !isNaN(lng)) {
            let newPos = new google.maps.LatLng(lat, lng);
            marker.setPosition(newPos);
            circle.setCenter(newPos);
            map.setCenter(newPos);
        }
    });
    
    dialog.fields_dict.longitude.$input.on('change', function() {
        let lat = parseFloat(dialog.get_value('latitude'));
        let lng = parseFloat(dialog.get_value('longitude'));
        if (!isNaN(lat) && !isNaN(lng)) {
            let newPos = new google.maps.LatLng(lat, lng);
            marker.setPosition(newPos);
            circle.setCenter(newPos);
            map.setCenter(newPos);
        }
    });
}

// Function to open map picker for main area location
function open_area_map_picker(frm) {
    let current_lat = frm.doc.center_latitude || 20.5937;
    let current_lng = frm.doc.center_longitude || 78.9629;
    let current_radius = frm.doc.radius || 100;
    
    let d = new frappe.ui.Dialog({
        title: __('Pick Area Center Location'),
        size: 'extra-large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'map_html'
            },
            {
                fieldtype: 'Column Break'
            },
            {
                fieldtype: 'Float',
                fieldname: 'latitude',
                label: 'Latitude',
                default: current_lat,
                precision: 8
            },
            {
                fieldtype: 'Float',
                fieldname: 'longitude',
                label: 'Longitude',
                default: current_lng,
                precision: 8
            },
            {
                fieldtype: 'Float',
                fieldname: 'radius',
                label: 'Radius (meters)',
                default: current_radius,
                precision: 2
            }
        ],
        primary_action_label: __('Set Location'),
        primary_action: function(values) {
            frm.set_value('center_latitude', values.latitude);
            frm.set_value('center_longitude', values.longitude);
            frm.set_value('radius', values.radius);
            d.hide();
        }
    });
    
    d.show();
    
    // Initialize map after dialog is shown
    setTimeout(() => {
        let map_html = `
            <div id="area_map" style="height: 500px; width: 100%; border: 1px solid #d1d8dd; border-radius: 4px;"></div>
            <div style="margin-top: 10px; padding: 10px; background: #f7fafc; border-radius: 4px;">
                <p style="margin: 0; font-size: 12px; color: #6c757d;">
                    <strong>Instructions:</strong> Click on the map to set the area center location. 
                    The circle shows the area coverage based on the radius.
                </p>
            </div>
        `;
        d.fields_dict.map_html.$wrapper.html(map_html);
        
        // Load Google Maps script dynamically
        load_google_maps(function() {
            if (typeof google === 'undefined' || typeof google.maps === 'undefined') {
                frappe.msgprint({
                    title: __('Error'),
                    indicator: 'red',
                    message: __('Google Maps failed to load. Please check your API key in site_config.json')
                });
                return;
            }
            
            initialize_area_map(d, current_lat, current_lng, current_radius, frm);
        });
    }, 300);
}

// Function to initialize the area map
function initialize_area_map(dialog, current_lat, current_lng, current_radius, frm) {
    // Initialize map
    let map = new google.maps.Map(document.getElementById('area_map'), {
        center: { lat: current_lat, lng: current_lng },
        zoom: 15,
        mapTypeId: 'satellite'
    });
    
    // Add marker for center
    let marker = new google.maps.Marker({
        position: { lat: current_lat, lng: current_lng },
        map: map,
        draggable: true,
        title: 'Area Center',
        label: {
            text: 'A',
            color: 'white'
        }
    });
    
    // Add circle for radius
    let circle = new google.maps.Circle({
        map: map,
        center: { lat: current_lat, lng: current_lng },
        radius: current_radius,
        fillColor: '#4A90E2',
        fillOpacity: 0.2,
        strokeColor: '#4A90E2',
        strokeOpacity: 0.8,
        strokeWeight: 2
    });
    
    // Update fields when marker is dragged
    marker.addListener('dragend', function() {
        let pos = marker.getPosition();
        dialog.set_value('latitude', pos.lat());
        dialog.set_value('longitude', pos.lng());
        circle.setCenter(pos);
    });
    
    // Update marker and circle when clicking on map
    map.addListener('click', function(e) {
        marker.setPosition(e.latLng);
        circle.setCenter(e.latLng);
        dialog.set_value('latitude', e.latLng.lat());
        dialog.set_value('longitude', e.latLng.lng());
    });
    
    // Update circle radius when radius field changes
    dialog.fields_dict.radius.$input.on('change', function() {
        let new_radius = parseFloat(dialog.get_value('radius')) || 100;
        circle.setRadius(new_radius);
    });
    
    // Update marker when lat/lng fields change
    dialog.fields_dict.latitude.$input.on('change', function() {
        let lat = parseFloat(dialog.get_value('latitude'));
        let lng = parseFloat(dialog.get_value('longitude'));
        if (!isNaN(lat) && !isNaN(lng)) {
            let newPos = new google.maps.LatLng(lat, lng);
            marker.setPosition(newPos);
            circle.setCenter(newPos);
            map.setCenter(newPos);
        }
    });
    
    dialog.fields_dict.longitude.$input.on('change', function() {
        let lat = parseFloat(dialog.get_value('latitude'));
        let lng = parseFloat(dialog.get_value('longitude'));
        if (!isNaN(lat) && !isNaN(lng)) {
            let newPos = new google.maps.LatLng(lat, lng);
            marker.setPosition(newPos);
            circle.setCenter(newPos);
            map.setCenter(newPos);
        }
    });
}

// Function to open combined map view showing area and all warehouses
function open_combined_map_view(frm) {
    let d = new frappe.ui.Dialog({
        title: __('View All Locations - {0}', [frm.doc.area_name || frm.doc.name]),
        size: 'extra-large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'map_html'
            }
        ]
    });
    
    d.show();
    
    // Initialize map after dialog is shown
    setTimeout(() => {
        let legend_items = [];
        if (frm.doc.center_latitude) {
            legend_items.push('<div style="margin-bottom: 8px;"><span style="display: inline-block; width: 20px; height: 20px; background: #4A90E2; border-radius: 50%; margin-right: 8px; vertical-align: middle;"></span><strong>Area Center (Circle)</strong></div>');
        }
        if (frm.doc.geo_fencing_coordinates && frm.doc.geo_fencing_coordinates.length > 0) {
            legend_items.push('<div style="margin-bottom: 8px;"><span style="display: inline-block; width: 20px; height: 20px; background: #4A90E2; margin-right: 8px; vertical-align: middle; border: 2px solid #2C5AA0;"></span><strong>Area Polygon</strong></div>');
        }
        if (frm.doc.warehouses && frm.doc.warehouses.length > 0) {
            legend_items.push('<div><span style="display: inline-block; width: 20px; height: 20px; background: #FF6B6B; border-radius: 50%; margin-right: 8px; vertical-align: middle;"></span><strong>Warehouses</strong></div>');
        }
        if (legend_items.length === 0) {
            legend_items.push('<div style="color: #718096;">No location data available. Add coordinates or warehouses to see them on the map.</div>');
        }
        
        let map_html = `
            <div id="combined_map" style="height: 600px; width: 100%; border: 1px solid #d1d8dd; border-radius: 4px;"></div>
            <div style="margin-top: 15px; padding: 15px; background: #f7fafc; border-radius: 4px;">
                <div style="font-size: 13px; color: #2d3748;">
                    <strong>Legend:</strong>
                    <div style="margin-top: 10px;">
                        ${legend_items.join('')}
                    </div>
                </div>
            </div>
        `;
        d.fields_dict.map_html.$wrapper.html(map_html);
        
        // Load Google Maps script dynamically
        load_google_maps(function() {
            if (typeof google === 'undefined' || typeof google.maps === 'undefined') {
                frappe.msgprint({
                    title: __('Error'),
                    indicator: 'red',
                    message: __('Google Maps failed to load. Please check your API key in site_config.json')
                });
                return;
            }
            
            initialize_combined_map(frm);
        });
    }, 300);
}

// Function to initialize combined map view
function initialize_combined_map(frm) {
    // Default center (India)
    let default_lat = 20.5937;
    let default_lng = 78.9629;
    let map_center = { lat: default_lat, lng: default_lng };
    
    // Use area center if available (for Circle)
    if (frm.doc.center_latitude && frm.doc.center_longitude) {
        map_center = { lat: frm.doc.center_latitude, lng: frm.doc.center_longitude };
    } 
    // Use polygon center if available
    else if (frm.doc.geo_fencing_coordinates && frm.doc.geo_fencing_coordinates.length > 0) {
        let first_coord = frm.doc.geo_fencing_coordinates[0];
        if (first_coord.latitude && first_coord.longitude) {
            map_center = { lat: first_coord.latitude, lng: first_coord.longitude };
        }
    }
    // Use first warehouse location if area center not available
    else if (frm.doc.warehouses && frm.doc.warehouses.length > 0) {
        let first_warehouse = frm.doc.warehouses.find(w => w.center_latitude && w.center_longitude);
        if (first_warehouse) {
            map_center = { lat: first_warehouse.center_latitude, lng: first_warehouse.center_longitude };
        }
    }
    
    // Initialize map
    let map = new google.maps.Map(document.getElementById('combined_map'), {
        center: map_center,
        zoom: 14,
        mapTypeId: 'satellite'
    });
    
    let bounds = new google.maps.LatLngBounds();
    let has_locations = false;
    
    // Add area center marker and circle
    if (frm.doc.center_latitude && frm.doc.center_longitude) {
        let area_marker = new google.maps.Marker({
            position: { lat: frm.doc.center_latitude, lng: frm.doc.center_longitude },
            map: map,
            title: `Area: ${frm.doc.area_name || frm.doc.name}`,
            label: {
                text: 'A',
                color: 'white'
            },
            icon: {
                path: google.maps.SymbolPath.CIRCLE,
                scale: 10,
                fillColor: '#4A90E2',
                fillOpacity: 1,
                strokeColor: 'white',
                strokeWeight: 2
            }
        });
        
        // Add info window
        let area_info = new google.maps.InfoWindow({
            content: `
                <div style="padding: 8px;">
                    <strong style="color: #4A90E2; font-size: 14px;">Area Center</strong><br>
                    <strong>${frm.doc.area_name || frm.doc.name}</strong><br>
                    Lat: ${frm.doc.center_latitude.toFixed(6)}<br>
                    Lng: ${frm.doc.center_longitude.toFixed(6)}<br>
                    ${frm.doc.radius ? `Radius: ${frm.doc.radius}m` : ''}
                </div>
            `
        });
        
        area_marker.addListener('click', function() {
            area_info.open(map, area_marker);
        });
        
        // Add circle for area
        if (frm.doc.radius) {
            new google.maps.Circle({
                map: map,
                center: { lat: frm.doc.center_latitude, lng: frm.doc.center_longitude },
                radius: frm.doc.radius,
                fillColor: '#4A90E2',
                fillOpacity: 0.15,
                strokeColor: '#4A90E2',
                strokeOpacity: 0.6,
                strokeWeight: 2
            });
        }
        
        bounds.extend(area_marker.getPosition());
        has_locations = true;
    }
    
    // Add polygon if coordinates exist
    if (frm.doc.geo_fencing_coordinates && frm.doc.geo_fencing_coordinates.length > 0) {
        let polygon_coords = [];
        frm.doc.geo_fencing_coordinates.forEach(coord => {
            if (coord.latitude && coord.longitude) {
                let latlng = { lat: coord.latitude, lng: coord.longitude };
                polygon_coords.push(latlng);
                bounds.extend(latlng);
                has_locations = true;
            }
        });
        
        if (polygon_coords.length > 0) {
            // Draw the polygon
            new google.maps.Polygon({
                map: map,
                paths: polygon_coords,
                strokeColor: '#4A90E2',
                strokeOpacity: 0.8,
                strokeWeight: 3,
                fillColor: '#4A90E2',
                fillOpacity: 0.2
            });
            
            // Add markers for each polygon vertex
            polygon_coords.forEach((coord, index) => {
                new google.maps.Marker({
                    position: coord,
                    map: map,
                    title: `Polygon Point ${index + 1}`,
                    label: {
                        text: (index + 1).toString(),
                        color: 'white'
                    },
                    icon: {
                        path: google.maps.SymbolPath.CIRCLE,
                        scale: 6,
                        fillColor: '#4A90E2',
                        fillOpacity: 0.8,
                        strokeColor: 'white',
                        strokeWeight: 1
                    }
                });
            });
        }
    }
    
    // Add warehouse markers and circles
    if (frm.doc.warehouses && frm.doc.warehouses.length > 0) {
        frm.doc.warehouses.forEach((warehouse, index) => {
            if (warehouse.center_latitude && warehouse.center_longitude) {
                let warehouse_marker = new google.maps.Marker({
                    position: { lat: warehouse.center_latitude, lng: warehouse.center_longitude },
                    map: map,
                    title: `Warehouse: ${warehouse.warehouse || 'Unnamed'}`,
                    label: {
                        text: (index + 1).toString(),
                        color: 'white'
                    },
                    icon: {
                        path: google.maps.SymbolPath.CIRCLE,
                        scale: 8,
                        fillColor: '#FF6B6B',
                        fillOpacity: 1,
                        strokeColor: 'white',
                        strokeWeight: 2
                    }
                });
                
                // Add info window
                let warehouse_info = new google.maps.InfoWindow({
                    content: `
                        <div style="padding: 8px;">
                            <strong style="color: #FF6B6B; font-size: 14px;">Warehouse ${index + 1}</strong><br>
                            <strong>${warehouse.warehouse || 'Unnamed'}</strong><br>
                            Lat: ${warehouse.center_latitude.toFixed(6)}<br>
                            Lng: ${warehouse.center_longitude.toFixed(6)}<br>
                            ${warehouse.radius ? `Radius: ${warehouse.radius}m` : ''}
                        </div>
                    `
                });
                
                warehouse_marker.addListener('click', function() {
                    warehouse_info.open(map, warehouse_marker);
                });
                
                // Add circle for warehouse
                if (warehouse.radius) {
                    new google.maps.Circle({
                        map: map,
                        center: { lat: warehouse.center_latitude, lng: warehouse.center_longitude },
                        radius: warehouse.radius,
                        fillColor: '#FF6B6B',
                        fillOpacity: 0.15,
                        strokeColor: '#FF6B6B',
                        strokeOpacity: 0.6,
                        strokeWeight: 1.5
                    });
                }
                
                bounds.extend(warehouse_marker.getPosition());
                has_locations = true;
            }
        });
    }
    
    // Fit map to show all markers
    if (has_locations) {
        map.fitBounds(bounds);
        
        // Add some padding
        google.maps.event.addListenerOnce(map, 'bounds_changed', function() {
            if (map.getZoom() > 18) {
                map.setZoom(18);
            }
        });
    }
}

// Function to fetch weather data for this field
function fetch_weather_for_this_field(frm) {
    frappe.call({
        method: 'f2c.weather.scheduler.fetch_weather_for_field',
        args: {
            geo_area_name: frm.doc.name
        },
        freeze: true,
        freeze_message: __('Fetching weather data...'),
        callback: function(r) {
            if (r.message && r.message.status === 'success') {
                frappe.msgprint({
                    title: __('Weather Data Fetched'),
                    indicator: 'green',
                    message: __('Weather report created: {0}', [
                        `<a href="/app/weather-report/${r.message.weather_report}">${r.message.weather_report}</a>`
                    ])
                });
            }
        },
        error: function(r) {
            frappe.msgprint({
                title: __('Error'),
                indicator: 'red',
                message: __('Failed to fetch weather data. Please check if coordinates are set for this field.')
            });
        }
    });
}

// Function to view latest weather report for this field
function view_latest_weather(frm) {
    frappe.call({
        method: 'f2c.weather.scheduler.get_latest_weather_for_field',
        args: {
            geo_area_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message) {
                let weather = r.message;
                let d = new frappe.ui.Dialog({
                    title: __('Latest Weather for {0}', [frm.doc.area_name || frm.doc.name]),
                    size: 'large',
                    fields: [
                        {
                            fieldtype: 'HTML',
                            fieldname: 'weather_html'
                        }
                    ],
                    primary_action_label: __('Open Full Report'),
                    primary_action: function() {
                        frappe.set_route('Form', 'Weather Report', weather.name);
                        d.hide();
                    }
                });
                
                let condition_icon = get_weather_icon(weather.weather_condition);
                
                let html = `
                    <div style="padding: 20px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                            <div>
                                <h3 style="margin: 0; color: #2d3748;">${weather.report_date}</h3>
                                <p style="margin: 5px 0 0 0; color: #718096;">Report: ${weather.name}</p>
                            </div>
                            <div style="text-align: right;">
                                <span style="font-size: 48px;">${condition_icon}</span>
                                <p style="margin: 0; color: #4a5568; font-weight: 500;">${weather.weather_condition || 'N/A'}</p>
                            </div>
                        </div>
                        
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px;">
                            <div style="background: #f7fafc; padding: 15px; border-radius: 8px; text-align: center;">
                                <p style="margin: 0; color: #718096; font-size: 12px;">Temperature</p>
                                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 600; color: #2d3748;">${weather.temperature !== null ? weather.temperature + '°C' : 'N/A'}</p>
                                <p style="margin: 0; color: #a0aec0; font-size: 11px;">Feels like ${weather.feels_like !== null ? weather.feels_like + '°C' : 'N/A'}</p>
                            </div>
                            
                            <div style="background: #f7fafc; padding: 15px; border-radius: 8px; text-align: center;">
                                <p style="margin: 0; color: #718096; font-size: 12px;">Humidity</p>
                                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 600; color: #2d3748;">${weather.humidity !== null ? weather.humidity + '%' : 'N/A'}</p>
                            </div>
                            
                            <div style="background: #f7fafc; padding: 15px; border-radius: 8px; text-align: center;">
                                <p style="margin: 0; color: #718096; font-size: 12px;">Wind Speed</p>
                                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 600; color: #2d3748;">${weather.wind_speed !== null ? weather.wind_speed + ' km/h' : 'N/A'}</p>
                                <p style="margin: 0; color: #a0aec0; font-size: 11px;">${weather.wind_direction || ''}</p>
                            </div>
                            
                            <div style="background: #f7fafc; padding: 15px; border-radius: 8px; text-align: center;">
                                <p style="margin: 0; color: #718096; font-size: 12px;">Precipitation</p>
                                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 600; color: #2d3748;">${weather.precipitation !== null ? weather.precipitation + ' mm' : 'N/A'}</p>
                            </div>
                            
                            <div style="background: #f7fafc; padding: 15px; border-radius: 8px; text-align: center;">
                                <p style="margin: 0; color: #718096; font-size: 12px;">Pressure</p>
                                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 600; color: #2d3748;">${weather.pressure !== null ? weather.pressure + ' hPa' : 'N/A'}</p>
                            </div>
                            
                            <div style="background: #f7fafc; padding: 15px; border-radius: 8px; text-align: center;">
                                <p style="margin: 0; color: #718096; font-size: 12px;">UV Index</p>
                                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 600; color: #2d3748;">${weather.uv_index !== null ? weather.uv_index : 'N/A'}</p>
                            </div>
                        </div>
                    </div>
                `;
                
                d.fields_dict.weather_html.$wrapper.html(html);
                d.show();
            } else {
                frappe.msgprint({
                    title: __('No Weather Data'),
                    indicator: 'orange',
                    message: __('No weather report found for this field. Click "Fetch Weather Data" to create one.')
                });
            }
        }
    });
}

// Helper function to get weather icon based on condition
function get_weather_icon(condition) {
    const icons = {
        'Clear': '☀️',
        'Partly Cloudy': '⛅',
        'Cloudy': '☁️',
        'Overcast': '☁️',
        'Fog': '🌫️',
        'Mist': '🌫️',
        'Light Rain': '🌦️',
        'Rain': '🌧️',
        'Heavy Rain': '🌧️',
        'Thunderstorm': '⛈️',
        'Drizzle': '🌦️',
        'Snow': '❄️',
        'Sleet': '🌨️',
        'Hail': '🌨️',
        'Windy': '💨',
        'Dust': '🌪️',
        'Sandstorm': '🌪️'
    };
    return icons[condition] || '🌡️';
}

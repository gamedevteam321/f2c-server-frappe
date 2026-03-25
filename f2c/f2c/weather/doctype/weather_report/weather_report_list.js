// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.listview_settings["Weather Report"] = {
	onload: function(listview) {
		// Add action button to fetch weather for all fields
		listview.page.add_inner_button(__("Fetch Weather for All Fields"), function() {
			frappe.confirm(
				__("This will fetch current weather data for all fields and create Weather Reports. This may take a few minutes depending on the number of fields. Continue?"),
				function() {
					// User confirmed
					frappe.call({
						method: "f2c.weather.scheduler.fetch_weather_for_all_fields_manual",
						freeze: true,
						freeze_message: __("Fetching weather data for all fields..."),
						callback: function(r) {
							if (r.message) {
								let msg = "";
								let indicator = "green";
								
								// Build summary message
								msg += `<p><strong>Summary:</strong></p>`;
								msg += `<ul>`;
								msg += `<li>Total Fields: ${r.message.total_fields || 0}</li>`;
								msg += `<li>Successfully Created: <strong style="color: green;">${r.message.success_count || 0}</strong></li>`;
								msg += `<li>Errors: <strong style="color: red;">${r.message.error_count || 0}</strong></li>`;
								msg += `<li>Skipped: ${r.message.skipped_count || 0}</li>`;
								msg += `</ul>`;
								
								// Show error details if any
								if (r.message.error_details && r.message.error_details.length > 0) {
									indicator = r.message.success_count > 0 ? "orange" : "red";
									msg += `<p><strong>Error Details (showing first ${r.message.error_details.length}):</strong></p>`;
									msg += `<ul style="max-height: 300px; overflow-y: auto;">`;
									r.message.error_details.forEach(function(error) {
										msg += `<li><strong>${error.field || error.field_id}:</strong> ${error.error || "Unknown error"}</li>`;
									});
									msg += `</ul>`;
									msg += `<p><small>Check Error Log for complete details.</small></p>`;
								}
								
								// Show success details if any
								if (r.message.success_reports && r.message.success_reports.length > 0) {
									msg += `<p><strong>Successfully Created Reports:</strong></p>`;
									msg += `<ul>`;
									r.message.success_reports.forEach(function(success) {
										msg += `<li><a href="/app/weather-report/${success.report}">${success.field}</a> - ${success.report}</li>`;
									});
									msg += `</ul>`;
								}
								
								frappe.msgprint({
									title: __("Weather Data Fetch Complete"),
									indicator: indicator,
									message: msg
								});
								
								// Refresh the list
								listview.refresh();
							}
						},
						error: function(r) {
							frappe.msgprint({
								title: __("Error"),
								indicator: "red",
								message: __("Failed to fetch weather data. Please check the error logs.")
							});
						}
					});
				}
			);
		}).addClass("btn-primary");
	},
	
	refresh: function(listview) {
		// Additional refresh logic if needed
	},
	
	formatters: {
		weather_condition: function(value) {
			if (!value) return "";
			
			const icons = {
				"Clear": "☀️",
				"Partly Cloudy": "⛅",
				"Cloudy": "☁️",
				"Overcast": "☁️",
				"Fog": "🌫️",
				"Mist": "🌫️",
				"Light Rain": "🌦️",
				"Rain": "🌧️",
				"Heavy Rain": "🌧️",
				"Thunderstorm": "⛈️",
				"Drizzle": "🌦️",
				"Snow": "❄️",
				"Sleet": "🌨️",
				"Hail": "🌨️",
				"Windy": "💨",
				"Dust": "🌪️",
				"Sandstorm": "🌪️"
			};
			
			const icon = icons[value] || "🌡️";
			return `${icon} ${value}`;
		},
		
		temperature: function(value) {
			if (value === null || value === undefined) return "";
			return `${value}°C`;
		}
	}
};

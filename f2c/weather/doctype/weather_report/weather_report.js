// Copyright (c) 2025, Orgatek and contributors
// For license information, please see license.txt

frappe.ui.form.on("Weather Report", {
	setup: function(frm) {
		// Filter location field to show only "Field" type Geo Fencing Areas
		frm.set_query("location", function() {
			return {
				filters: {
					"geo_fencing_type": "Field"
				}
			};
		});
	},

	refresh: function(frm) {
		// Add buttons to fetch weather data if coordinates are available
		if (frm.doc.latitude && frm.doc.longitude && !frm.is_new()) {
			frm.add_custom_button(__("Fetch Current Weather"), function() {
				frm.trigger("fetch_current_weather");
			}, __("Weather"));
			
			frm.add_custom_button(__("Fetch Hourly Forecast"), function() {
				frm.trigger("fetch_hourly_forecast");
			}, __("Weather"));
			
			frm.add_custom_button(__("Fetch All Weather Data"), function() {
				frm.trigger("fetch_all_weather_data");
			}, __("Weather"));
		}
	},

	location: function(frm) {
		// When location is selected, fetch coordinates from Geo Fencing Area
		if (frm.doc.location) {
			frappe.db.get_value("Geo Fencing Area", frm.doc.location, 
				["center_latitude", "center_longitude", "area_name", "shape_type", "geo_fencing_coordinates"], 
				function(r) {
					if (r) {
						// For Circle type, use center coordinates
						if (r.center_latitude) frm.set_value("latitude", r.center_latitude);
						if (r.center_longitude) frm.set_value("longitude", r.center_longitude);
					}
				}
			);
		}
	},

	fetch_current_weather: function(frm) {
		if (!frm.doc.latitude || !frm.doc.longitude) {
			frappe.msgprint(__("Please set latitude and longitude first"));
			return;
		}

		frappe.call({
			method: "f2c.weather.api.get_current_weather",
			args: {
				latitude: frm.doc.latitude,
				longitude: frm.doc.longitude
			},
			freeze: true,
			freeze_message: __("Fetching current weather..."),
			callback: function(r) {
				if (r.message) {
					frm.events.populate_current_weather(frm, r.message);
					frm.set_value("data_source", "Google Weather API");
					frappe.msgprint(__("Current weather fetched successfully"));
				}
			}
		});
	},

	fetch_hourly_forecast: function(frm) {
		if (!frm.doc.latitude || !frm.doc.longitude) {
			frappe.msgprint(__("Please set latitude and longitude first"));
			return;
		}

		frappe.call({
			method: "f2c.weather.api.get_hourly_forecast",
			args: {
				latitude: frm.doc.latitude,
				longitude: frm.doc.longitude,
				hours: 24
			},
			freeze: true,
			freeze_message: __("Fetching hourly forecast..."),
			callback: function(r) {
				if (r.message) {
					frm.events.populate_hourly_forecast(frm, r.message);
					frm.set_value("data_source", "Google Weather API");
					frappe.msgprint(__("Hourly forecast fetched successfully"));
				}
			}
		});
	},

	fetch_all_weather_data: function(frm) {
		if (!frm.doc.latitude || !frm.doc.longitude) {
			frappe.msgprint(__("Please set latitude and longitude first"));
			return;
		}

		frappe.call({
			method: "f2c.weather.api.get_weather_data",
			args: {
				latitude: frm.doc.latitude,
				longitude: frm.doc.longitude
			},
			freeze: true,
			freeze_message: __("Fetching all weather data..."),
			callback: function(r) {
				if (r.message) {
					// Populate current weather
					if (r.message.current) {
						frm.events.populate_current_weather(frm, r.message.current);
					}
					// Populate hourly forecast
					if (r.message.hourly) {
						frm.events.populate_hourly_forecast(frm, r.message.hourly);
					}
					frm.set_value("data_source", "Google Weather API");
					frm.set_value("raw_data", JSON.stringify(r.message, null, 2));
					frappe.msgprint(__("All weather data fetched successfully"));
				}
			}
		});
	},

	populate_current_weather: function(frm, data) {
		// Parse and populate fields from API response
		// This will need to be adjusted based on the actual API response structure
		if (data.currentConditions) {
			const conditions = data.currentConditions;
			
			if (conditions.temperature) {
				frm.set_value("temperature", conditions.temperature.degrees);
			}
			if (conditions.feelsLike) {
				frm.set_value("feels_like", conditions.feelsLike.degrees);
			}
			if (conditions.humidity) {
				frm.set_value("humidity", conditions.humidity.percent);
			}
			if (conditions.wind) {
				frm.set_value("wind_speed", conditions.wind.speed?.value);
				frm.set_value("wind_direction", conditions.wind.direction?.cardinal);
			}
			if (conditions.uvIndex) {
				frm.set_value("uv_index", conditions.uvIndex);
			}
			if (conditions.pressure) {
				frm.set_value("pressure", conditions.pressure?.value);
			}
			if (conditions.visibility) {
				frm.set_value("visibility", conditions.visibility?.value);
			}
			if (conditions.cloudCover) {
				frm.set_value("cloud_cover", conditions.cloudCover);
			}
			if (conditions.dewPoint) {
				frm.set_value("dew_point", conditions.dewPoint?.degrees);
			}
		}
	},

	populate_hourly_forecast: function(frm, data) {
		// Clear existing hourly forecast rows
		frm.clear_table("hourly_forecast");
		
		// Parse hourly forecast from API response
		// Adjust based on actual Google Weather API response structure
		const hourlyData = data.forecastHours || data.hourlyForecasts || data.hours || [];
		
		hourlyData.forEach(function(hour) {
			let row = frm.add_child("hourly_forecast");
			
			// Parse datetime
			if (hour.displayDateTime || hour.dateTime || hour.time) {
				row.forecast_datetime = hour.displayDateTime || hour.dateTime || hour.time;
			}
			
			// Parse hour
			if (hour.hour !== undefined) {
				row.hour = hour.hour;
			} else if (row.forecast_datetime) {
				// Extract hour from datetime
				const dt = new Date(row.forecast_datetime);
				row.hour = dt.getHours();
			}
			
			// Temperature
			if (hour.temperature) {
				row.temperature = hour.temperature.degrees || hour.temperature;
			}
			
			// Feels like
			if (hour.feelsLike) {
				row.feels_like = hour.feelsLike.degrees || hour.feelsLike;
			}
			
			// Humidity
			if (hour.humidity) {
				row.humidity = hour.humidity.percent || hour.humidity;
			}
			
			// Precipitation
			if (hour.precipitation) {
				row.precipitation = hour.precipitation.value || hour.precipitation.amount || hour.precipitation;
			}
			
			// Precipitation probability
			if (hour.precipitationProbability !== undefined) {
				row.precipitation_probability = hour.precipitationProbability.percent || hour.precipitationProbability;
			}
			
			// Wind
			if (hour.wind) {
				row.wind_speed = hour.wind.speed?.value || hour.wind.speed;
				row.wind_direction = hour.wind.direction?.cardinal || hour.wind.direction;
				row.wind_gust = hour.wind.gust?.value || hour.wind.gust;
			}
			
			// Cloud cover
			if (hour.cloudCover !== undefined) {
				row.cloud_cover = hour.cloudCover;
			}
			
			// UV Index
			if (hour.uvIndex !== undefined) {
				row.uv_index = hour.uvIndex;
			}
			
			// Visibility
			if (hour.visibility) {
				row.visibility = hour.visibility.value || hour.visibility;
			}
			
			// Pressure
			if (hour.pressure) {
				row.pressure = hour.pressure.value || hour.pressure;
			}
		});
		
		frm.refresh_field("hourly_forecast");
	}
});

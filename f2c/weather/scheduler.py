# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import now_datetime, nowdate, nowtime, today, get_datetime
from typing import Optional, Tuple, Dict, Any
import json


def get_field_coordinates(geo_area_name: str) -> Optional[Tuple[float, float]]:
	"""
	Get the coordinates (latitude, longitude) for a Geo Fencing Area.
	For Circle shapes, returns center coordinates.
	For Polygon shapes, returns the centroid.
	
	Args:
		geo_area_name: Name of the Geo Fencing Area
		
	Returns:
		Tuple of (latitude, longitude) or None if not available
	"""
	try:
		geo_area = frappe.get_doc("Geo Fencing Area", geo_area_name)
		frappe.logger().info(f"      - Field shape type: {geo_area.shape_type}")
		
		if geo_area.shape_type == "Circle":
			frappe.logger().info(f"      - Circle center_latitude: {geo_area.center_latitude}, center_longitude: {geo_area.center_longitude}")
			if geo_area.center_latitude and geo_area.center_longitude:
				return (geo_area.center_latitude, geo_area.center_longitude)
			else:
				frappe.logger().warning(f"      - Circle field {geo_area_name} missing center coordinates")
		elif geo_area.shape_type == "Polygon":
			# Calculate centroid from polygon coordinates
			frappe.logger().info(f"      - Polygon has {len(geo_area.geo_fencing_coordinates) if geo_area.geo_fencing_coordinates else 0} coordinates")
			if geo_area.geo_fencing_coordinates and len(geo_area.geo_fencing_coordinates) > 0:
				centroid = calculate_polygon_centroid(geo_area.geo_fencing_coordinates)
				frappe.logger().info(f"      - Calculated centroid: {centroid}")
				return centroid
			else:
				frappe.logger().warning(f"      - Polygon field {geo_area_name} has no coordinates")
		else:
			frappe.logger().warning(f"      - Field {geo_area_name} has unknown shape_type: {geo_area.shape_type}")
		
		return None
	except Exception as e:
		frappe.logger().error(f"      - Error getting coordinates for {geo_area_name}: {str(e)}")
		frappe.log_error(f"Error getting coordinates for {geo_area_name}: {str(e)}\nTraceback: {frappe.get_traceback()}", "Get Field Coordinates Error")
		return None


def calculate_polygon_centroid(coordinates) -> Optional[Tuple[float, float]]:
	"""
	Calculate the centroid of a polygon from its coordinates.
	
	Args:
		coordinates: List of coordinate objects with latitude and longitude
		
	Returns:
		Tuple of (latitude, longitude) for the centroid
	"""
	if not coordinates:
		return None
	
	total_lat = 0
	total_lng = 0
	count = len(coordinates)
	
	for coord in coordinates:
		total_lat += coord.latitude
		total_lng += coord.longitude
	
	return (total_lat / count, total_lng / count)


def create_weather_report_for_field(geo_area_name: str, fetch_hourly: bool = True) -> Optional[str]:
	"""
	Create a Weather Report for a specific Geo Fencing Area by fetching weather data.
	
	Args:
		geo_area_name: Name of the Geo Fencing Area
		fetch_hourly: Whether to fetch hourly forecast data
		
	Returns:
		Name of the created Weather Report or None if failed
	"""
	from f2c.weather.api import get_current_weather, get_hourly_forecast
	
	try:
		frappe.logger().info(f"    Step 1: Getting coordinates for {geo_area_name}")
		coordinates = get_field_coordinates(geo_area_name)
		if not coordinates:
			error_msg = f"No coordinates found for Geo Fencing Area: {geo_area_name}"
			frappe.logger().error(f"    ✗ {error_msg}")
			frappe.log_error(error_msg, "Weather Report Creation Failed")
			return None
		
		latitude, longitude = coordinates
		frappe.logger().info(f"    Step 2: Coordinates: {latitude}, {longitude}")
		
		# Fetch current weather
		frappe.logger().info(f"    Step 3: Fetching current weather from API")
		current_weather = get_current_weather(latitude, longitude)
		frappe.logger().info(f"    ✓ Current weather fetched successfully")
		
		# Fetch hourly forecast if requested
		hourly_data = None
		if fetch_hourly:
			try:
				frappe.logger().info(f"    Step 4: Fetching hourly forecast from API")
				hourly_data = get_hourly_forecast(latitude, longitude, hours=24)
				frappe.logger().info(f"    ✓ Hourly forecast fetched successfully")
				if hourly_data:
					frappe.logger().info(f"    - Hourly data keys: {list(hourly_data.keys()) if isinstance(hourly_data, dict) else 'Not a dict'}")
			except Exception as e:
				frappe.logger().error(f"    ✗ Failed to fetch hourly forecast: {str(e)}")
				frappe.log_error(f"Failed to fetch hourly forecast for {geo_area_name}: {str(e)}\nTraceback: {frappe.get_traceback()}", "Hourly Forecast Error")
				# Don't fail the whole report if hourly forecast fails
				hourly_data = None
		
		# Create Weather Report
		frappe.logger().info(f"    Step 5: Creating Weather Report document")
		weather_report = frappe.new_doc("Weather Report")
		weather_report.report_date = today()
		weather_report.report_time = nowtime()
		weather_report.location = geo_area_name
		weather_report.latitude = latitude
		weather_report.longitude = longitude
		weather_report.data_source = "Google Weather API"
		
		# Store raw data
		raw_data = {"current": current_weather}
		if hourly_data:
			raw_data["hourly"] = hourly_data
		weather_report.raw_data = json.dumps(raw_data, indent=2)
		
		# Parse and populate current weather fields
		frappe.logger().info(f"    Step 6: Populating current weather fields")
		populate_current_weather_fields(weather_report, current_weather)
		frappe.logger().info(f"    ✓ Current weather fields populated")
		
		# Parse and populate hourly forecast
		if hourly_data:
			frappe.logger().info(f"    Step 7: Populating hourly forecast fields")
			populate_hourly_forecast_fields(weather_report, hourly_data)
			frappe.logger().info(f"    ✓ Hourly forecast fields populated")
		
		frappe.logger().info(f"    Step 8: Inserting Weather Report into database")
		weather_report.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info(f"    ✓ Weather Report {weather_report.name} created successfully")
		
		return weather_report.name
		
	except Exception as e:
		error_msg = f"Failed to create weather report for {geo_area_name}: {str(e)}"
		traceback_msg = frappe.get_traceback()
		frappe.logger().error(f"    ✗ {error_msg}")
		frappe.logger().error(f"    Traceback: {traceback_msg}")
		
		detailed_error = f"{error_msg}\n\nTraceback:\n{traceback_msg}"
		frappe.log_error(detailed_error, "Weather Report Creation Failed")
		
		# Re-raise so the caller can catch and include the actual error message
		raise


# Map API weather type (e.g. CLEAR, RAIN) to our Select options (Clear, Rain)
API_WEATHER_TYPE_MAP = {
	"CLEAR": "Clear", "CLOUDY": "Cloudy", "RAIN": "Rain", "PARTLY_CLOUDY": "Partly Cloudy",
	"OVERCAST": "Overcast", "FOG": "Fog", "MIST": "Mist", "LIGHT_RAIN": "Light Rain",
	"HEAVY_RAIN": "Heavy Rain", "THUNDERSTORM": "Thunderstorm", "DRIZZLE": "Drizzle",
	"SNOW": "Snow", "SLEET": "Sleet", "HAIL": "Hail", "WINDY": "Windy", "DUST": "Dust",
	"SANDSTORM": "Sandstorm",
}

def map_weather_type(api_type):
	"""Map API weather type to our Select options."""
	if not api_type:
		return None
	# Normalize: remove spaces, convert to uppercase
	normalized = str(api_type).upper().replace(" ", "_").replace("-", "_")
	return API_WEATHER_TYPE_MAP.get(normalized)


def safe_extract_value(value, keys=None, default=None):
	"""
	Safely extract a scalar value from potentially nested dictionary structures.
	
	Args:
		value: The value to extract (can be dict, list, or scalar)
		keys: List of keys to try in order (e.g., ["degrees", "value", "amount"])
		default: Default value if extraction fails
		
	Returns:
		Scalar value (int, float, str) or default
	"""
	if value is None:
		return default
	
	# If it's already a scalar (int, float, str, bool), return it
	if not isinstance(value, (dict, list)):
		return value
	
	# If it's a list, try to get the first element
	if isinstance(value, list):
		if len(value) > 0:
			value = value[0]
		else:
			return default
	
	# If it's a dict, try to extract using provided keys
	if isinstance(value, dict):
		if keys:
			for key in keys:
				if key in value:
					extracted = value[key]
					# Recursively extract if still a dict/list
					if isinstance(extracted, (dict, list)):
						return safe_extract_value(extracted, keys, default)
					return extracted
		# If no keys provided or keys not found, try common keys
		for common_key in ["value", "degrees", "percent", "amount", "cardinal"]:
			if common_key in value:
				extracted = value[common_key]
				if isinstance(extracted, (dict, list)):
					return safe_extract_value(extracted, None, default)
				return extracted
		# If still a dict, return default
		return default
	
	return default


def populate_current_weather_fields(weather_report, data: Dict[str, Any]):
	"""
	Populate weather report fields from API response.
	Handles Google Weather API v1 response structure (currentConditions:lookup).
	See: https://developers.google.com/maps/documentation/weather/current-conditions
	"""
	if not data:
		return
	
	# Google Weather API v1 returns data at top level; some wrappers use 'currentConditions'
	conditions = data.get("currentConditions", data)
	
	if not isinstance(conditions, dict):
		frappe.logger().warning(f"    ⚠ Conditions data is not a dict: {type(conditions)}")
		return
	
	# Temperature (temperature: {degrees, unit})
	if "temperature" in conditions:
		weather_report.temperature = safe_extract_value(conditions["temperature"], ["degrees", "value"])
	
	# Feels like - API uses feelsLikeTemperature (not feelsLike)
	feels_like_val = safe_extract_value(
		conditions.get("feelsLikeTemperature") or conditions.get("feelsLike"),
		["degrees", "value"]
	)
	if feels_like_val is not None:
		weather_report.feels_like = feels_like_val
	
	# Min/Max temperature from currentConditionsHistory
	history = conditions.get("currentConditionsHistory")
	if isinstance(history, dict):
		if "minTemperature" in history:
			weather_report.temperature_min = safe_extract_value(history["minTemperature"], ["degrees", "value"])
		if "maxTemperature" in history:
			weather_report.temperature_max = safe_extract_value(history["maxTemperature"], ["degrees", "value"])
	
	# Humidity - API uses relativeHumidity (number) or humidity (object)
	humidity_val = conditions.get("relativeHumidity")
	if humidity_val is not None and not isinstance(humidity_val, (dict, list)):
		weather_report.humidity = humidity_val
	elif "humidity" in conditions:
		weather_report.humidity = safe_extract_value(conditions["humidity"], ["percent", "value"])
	
	# Wind
	if "wind" in conditions:
		wind = conditions["wind"]
		if isinstance(wind, dict):
			if "speed" in wind:
				weather_report.wind_speed = safe_extract_value(wind["speed"], ["value", "kmh", "km/h"])
			if "direction" in wind:
				weather_report.wind_direction = safe_extract_value(wind["direction"], ["cardinal", "value", "degrees"])
			if "gust" in wind:
				weather_report.wind_gust = safe_extract_value(wind["gust"], ["value", "kmh", "km/h"])
	
	# UV Index (number)
	if "uvIndex" in conditions:
		weather_report.uv_index = safe_extract_value(conditions["uvIndex"])
	
	# Pressure - API uses airPressure.meanSeaLevelMillibars (number)
	air_pressure = conditions.get("airPressure")
	if isinstance(air_pressure, dict) and "meanSeaLevelMillibars" in air_pressure:
		val = air_pressure["meanSeaLevelMillibars"]
		weather_report.pressure = val if isinstance(val, (int, float)) else safe_extract_value(val)
	elif "pressure" in conditions:
		weather_report.pressure = safe_extract_value(conditions["pressure"], ["value", "hPa", "meanSeaLevelMillibars"])
	
	# Visibility - API uses visibility.distance (not value)
	vis = conditions.get("visibility")
	if isinstance(vis, dict):
		weather_report.visibility = safe_extract_value(vis, ["distance", "value", "km"])
	else:
		weather_report.visibility = safe_extract_value(vis)
	
	# Cloud cover (number or object)
	if "cloudCover" in conditions:
		weather_report.cloud_cover = safe_extract_value(conditions["cloudCover"], ["percent", "value"])
	
	# Dew point
	if "dewPoint" in conditions:
		weather_report.dew_point = safe_extract_value(conditions["dewPoint"], ["degrees", "value"])
	
	# Precipitation - API: precipitation.qpf.quantity and precipitation.probability.percent
	precip = conditions.get("precipitation")
	if isinstance(precip, dict):
		qpf = precip.get("qpf")
		if isinstance(qpf, dict):
			weather_report.precipitation = safe_extract_value(qpf, ["quantity", "value", "amount", "mm"])
		else:
			weather_report.precipitation = safe_extract_value(precip, ["value", "amount", "mm"])
		prob = precip.get("probability")
		if isinstance(prob, dict):
			weather_report.precipitation_probability = safe_extract_value(prob, ["percent", "value"])
	elif "precipitation" in conditions:
		weather_report.precipitation = safe_extract_value(conditions["precipitation"], ["value", "amount", "mm"])
	
	# Rainfall - same as precipitation for current conditions
	if weather_report.precipitation is not None:
		weather_report.rainfall = weather_report.precipitation
	
	# Weather description/condition - API: weatherCondition.description.text and .type
	wc = conditions.get("weatherCondition")
	if isinstance(wc, dict):
		desc = wc.get("description")
		if isinstance(desc, dict) and "text" in desc:
			weather_report.weather_description = safe_extract_value(desc["text"])
		# Store weather condition as text (not mapped to Select options)
		if "type" in wc:
			api_type = safe_extract_value(wc["type"])
			if api_type:
				weather_report.weather_condition = str(api_type)
		# Also try to get description text for condition if type not available
		if not weather_report.weather_condition and desc and isinstance(desc, dict) and "text" in desc:
			weather_report.weather_condition = safe_extract_value(desc["text"])
	elif "weatherCondition" in conditions:
		weather_report.weather_description = safe_extract_value(conditions["weatherCondition"])
		weather_report.weather_condition = safe_extract_value(conditions["weatherCondition"])
	if "description" in conditions and not weather_report.weather_description:
		weather_report.weather_description = safe_extract_value(conditions["description"])
		if not weather_report.weather_condition:
			weather_report.weather_condition = safe_extract_value(conditions["description"])


def populate_hourly_forecast_fields(weather_report, data: Dict[str, Any]):
	"""
	Populate hourly forecast table from API response.
	"""
	if not data:
		frappe.logger().warning(f"    ⚠ No hourly data provided to populate_hourly_forecast_fields")
		return
	
	frappe.logger().info(f"    - Populating hourly forecast, data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
	
	# Try different keys for hourly data - Google API uses "forecastHours"
	hourly_list = (
		data.get("forecastHours") or 
		data.get("hourlyForecasts") or 
		data.get("hours") or
		data.get("hourly") or
		[]
	)
	
	frappe.logger().info(f"    - Found {len(hourly_list) if isinstance(hourly_list, list) else 0} hourly forecast entries")
	
	if not isinstance(hourly_list, list):
		frappe.logger().warning(f"    ⚠ Hourly data is not a list: {type(hourly_list)}, value: {hourly_list}")
		return
	
	if len(hourly_list) == 0:
		frappe.logger().warning(f"    ⚠ Hourly forecast list is empty")
		return
	
	for hour_data in hourly_list:
		if not isinstance(hour_data, dict):
			frappe.logger().warning(f"    ⚠ Hour data item is not a dict: {type(hour_data)}")
			continue
			
		row = weather_report.append("hourly_forecast", {})
		
		# Datetime - API uses displayDateTime (string) or interval.startTime
		dt_str = None
		interval = hour_data.get("interval")
		if isinstance(interval, dict) and "startTime" in interval:
			dt_str = interval["startTime"]
		else:
			dt_str = hour_data.get("displayDateTime") or hour_data.get("dateTime") or hour_data.get("time")
		
		if dt_str:
			# Convert to string if it's not already
			if isinstance(dt_str, dict):
				dt_str = safe_extract_value(dt_str, ["value", "startTime"])
			if dt_str:
				try:
					# Parse ISO 8601 format (e.g., '2026-01-29T07:30:00Z') and convert to MySQL datetime format
					dt = get_datetime(dt_str)
					# Format as MySQL datetime: 'YYYY-MM-DD HH:MM:SS'
					row.forecast_datetime = dt.strftime('%Y-%m-%d %H:%M:%S')
					row.hour = dt.hour
				except Exception as e:
					frappe.logger().warning(f"    ⚠ Could not parse datetime {dt_str}: {str(e)}")
					# Try to manually convert ISO format to MySQL format
					try:
						import re
						from datetime import datetime
						# Handle ISO 8601 format: 2026-01-29T07:30:00Z or 2026-01-29T07:30:00+00:00
						dt_str_clean = str(dt_str).replace('Z', '+00:00')
						# Try parsing with different formats
						for fmt in ['%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']:
							try:
								dt = datetime.strptime(dt_str_clean, fmt)
								row.forecast_datetime = dt.strftime('%Y-%m-%d %H:%M:%S')
								row.hour = dt.hour
								break
							except:
								continue
						# If all parsing fails, try to extract hour from string
						if not row.forecast_datetime:
							hour_match = re.search(r'(\d{1,2}):', str(dt_str))
							if hour_match:
								row.hour = int(hour_match.group(1))
								# Use today's date with the extracted hour
								from datetime import datetime, time
								today = datetime.now().date()
								row.forecast_datetime = datetime.combine(today, time(row.hour, 0)).strftime('%Y-%m-%d %H:%M:%S')
					except Exception as e2:
						frappe.logger().error(f"    ✗ Failed to convert datetime {dt_str}: {str(e2)}")
		
		# Temperature
		if "temperature" in hour_data:
			row.temperature = safe_extract_value(hour_data["temperature"], ["degrees", "value"])
		
		# Feels like - API may use apparentTemperature or feelsLike
		feels = hour_data.get("apparentTemperature") or hour_data.get("feelsLike")
		if feels is not None:
			row.feels_like = safe_extract_value(feels, ["degrees", "value"])
		
		# Weather condition - API: weatherCondition.type or description.text
		# Store as text (not mapped to Select options)
		wc = hour_data.get("weatherCondition")
		if isinstance(wc, dict):
			# Try to get description text first (more descriptive)
			desc = wc.get("description")
			if isinstance(desc, dict) and "text" in desc:
				row.weather_condition = safe_extract_value(desc["text"])
			# Fallback to type if description not available
			if not row.weather_condition and "type" in wc:
				row.weather_condition = safe_extract_value(wc["type"])
		
		# Humidity - API may use relativeHumidity (number) or humidity (object)
		if "relativeHumidity" in hour_data and not isinstance(hour_data["relativeHumidity"], (dict, list)):
			row.humidity = hour_data["relativeHumidity"]
		elif "humidity" in hour_data:
			row.humidity = safe_extract_value(hour_data["humidity"], ["percent", "value"])
		
		# Precipitation - API: precipitation.qpf.quantity
		if "precipitation" in hour_data:
			precip = hour_data["precipitation"]
			if isinstance(precip, dict) and "qpf" in precip:
				row.precipitation = safe_extract_value(precip["qpf"], ["quantity", "value", "amount", "mm"])
			else:
				row.precipitation = safe_extract_value(precip, ["value", "amount", "mm"])
		
		# Precipitation probability - API: precipitation.probability.percent
		if "precipitation" in hour_data:
			precip = hour_data["precipitation"]
			if isinstance(precip, dict) and "probability" in precip:
				row.precipitation_probability = safe_extract_value(precip["probability"], ["percent", "value"])
		if row.precipitation_probability is None and "precipitationProbability" in hour_data:
			row.precipitation_probability = safe_extract_value(hour_data["precipitationProbability"], ["percent", "value"])
		
		# Wind
		if "wind" in hour_data:
			wind = hour_data["wind"]
			if isinstance(wind, dict):
				if "speed" in wind:
					row.wind_speed = safe_extract_value(wind["speed"], ["value", "kmh", "km/h"])
				if "direction" in wind:
					row.wind_direction = safe_extract_value(wind["direction"], ["cardinal", "value", "degrees"])
				if "gust" in wind:
					row.wind_gust = safe_extract_value(wind["gust"], ["value", "kmh", "km/h"])
		
		# Cloud cover
		if "cloudCover" in hour_data:
			row.cloud_cover = safe_extract_value(hour_data["cloudCover"], ["percent", "value"])
		
		# UV Index
		if "uvIndex" in hour_data:
			row.uv_index = safe_extract_value(hour_data["uvIndex"])
		
		# Visibility - API uses visibility.distance
		if "visibility" in hour_data:
			row.visibility = safe_extract_value(hour_data["visibility"], ["distance", "value", "km"])
		
		# Pressure - API may use seaLevelPressure (number) or airPressure.meanSeaLevelMillibars
		if "seaLevelPressure" in hour_data and not isinstance(hour_data["seaLevelPressure"], (dict, list)):
			row.pressure = hour_data["seaLevelPressure"]
		else:
			ap = hour_data.get("airPressure")
			if isinstance(ap, dict) and "meanSeaLevelMillibars" in ap:
				row.pressure = safe_extract_value(ap["meanSeaLevelMillibars"])
			elif "pressure" in hour_data:
				row.pressure = safe_extract_value(hour_data["pressure"], ["value", "hPa"])


def fetch_weather_for_all_fields():
	"""
	Scheduled task to fetch weather data for all Geo Fencing Areas of type "Field".
	Runs daily at 6 AM.
	"""
	frappe.logger().info("Starting daily weather data collection for all fields")
	
	# Get all Geo Fencing Areas of type "Field" that have coordinates
	geo_areas = frappe.get_all(
		"Geo Fencing Area",
		filters={"geo_fencing_type": "Field"},
		fields=["name", "area_name", "shape_type"]
	)
	
	success_count = 0
	error_count = 0
	
	for geo_area in geo_areas:
		try:
			# Check if coordinates are available
			coordinates = get_field_coordinates(geo_area.name)
			if coordinates:
				report_name = create_weather_report_for_field(geo_area.name, fetch_hourly=True)
				if report_name:
					success_count += 1
					frappe.logger().info(f"Created weather report {report_name} for {geo_area.area_name}")
				else:
					error_count += 1
			else:
				frappe.logger().warning(f"No coordinates for {geo_area.area_name}, skipping weather fetch")
		except Exception as e:
			error_count += 1
			frappe.log_error(
				f"Error fetching weather for {geo_area.name}: {str(e)}",
				"Daily Weather Fetch Error"
			)
	
	frappe.logger().info(
		f"Daily weather collection completed: {success_count} successful, {error_count} errors"
	)


def update_today_weather_reports_hourly() -> None:
	"""
	Scheduled task to update (NOT create) today's Weather Report for each Field.
	Runs hourly.

	- Updates only current weather condition fields
	- Updates report_time with HH:MM:SS (no microseconds)
	- Does not modify hourly_forecast child rows
	"""
	from f2c.weather.api import get_current_weather

	run_date = today()
	run_time = now_datetime().strftime("%H:%M:%S")

	frappe.logger().info(f"Starting hourly weather update for reports on {run_date} at {run_time}")

	geo_areas = frappe.get_all(
		"Geo Fencing Area",
		filters={"geo_fencing_type": "Field"},
		fields=["name", "area_name", "shape_type"]
	)

	updated = 0
	skipped_no_report = 0
	skipped_no_coords = 0
	errors = 0

	for geo_area in geo_areas:
		field_id = geo_area.name
		field_label = geo_area.area_name or field_id

		try:
			# Find today's report (update existing only)
			existing = frappe.get_all(
				"Weather Report",
				filters={"location": field_id, "report_date": run_date},
				fields=["name", "report_time"],
				order_by="report_time desc",
				limit=1
			)
			if not existing:
				skipped_no_report += 1
				continue

			coords = get_field_coordinates(field_id)
			if not coords:
				skipped_no_coords += 1
				continue

			latitude, longitude = coords

			current_weather = get_current_weather(latitude, longitude)

			doc = frappe.get_doc("Weather Report", existing[0].name)

			# Update reporting time without microseconds
			doc.report_time = run_time

			# Keep hourly forecast intact; only refresh current conditions
			populate_current_weather_fields(doc, current_weather)

			# Update raw_data.current if present (keep it lightweight)
			try:
				raw = {}
				if doc.raw_data:
					raw = json.loads(doc.raw_data) if isinstance(doc.raw_data, str) else (doc.raw_data or {})
				if not isinstance(raw, dict):
					raw = {}
				raw["current"] = current_weather
				doc.raw_data = json.dumps(raw)
			except Exception:
				# Don't fail the update if raw_data isn't valid JSON
				pass

			doc.save(ignore_permissions=True)
			updated += 1

		except Exception as e:
			errors += 1
			frappe.log_error(
				f"Hourly update failed for field {field_label} ({field_id}): {str(e)}\nTraceback: {frappe.get_traceback()}",
				"Hourly Weather Update Error"
			)

	# Commit once at end for performance
	frappe.db.commit()

	frappe.logger().info(
		f"Hourly weather update completed: updated={updated}, skipped_no_report={skipped_no_report}, skipped_no_coords={skipped_no_coords}, errors={errors}"
	)


@frappe.whitelist()
def fetch_weather_for_all_fields_manual() -> Dict[str, Any]:
	"""
	API endpoint to manually fetch weather data for all fields.
	Called from the Weather Report list view action button.
	
	Returns:
		Dictionary with success_count, error_count, and error_details
	"""
	frappe.logger().info("=" * 80)
	frappe.logger().info("Starting manual weather data collection for all fields")
	frappe.logger().info("=" * 80)
	
	# Get all Geo Fencing Areas of type "Field" that have coordinates
	geo_areas = frappe.get_all(
		"Geo Fencing Area",
		filters={"geo_fencing_type": "Field"},
		fields=["name", "area_name", "shape_type"]
	)
	
	frappe.logger().info(f"Found {len(geo_areas)} fields to process")
	
	success_count = 0
	error_count = 0
	skipped_count = 0
	error_details = []
	success_reports = []
	
	for idx, geo_area in enumerate(geo_areas, 1):
		field_name = geo_area.area_name or geo_area.name
		frappe.logger().info(f"[{idx}/{len(geo_areas)}] Processing field: {field_name} ({geo_area.name})")
		
		try:
			# Check if coordinates are available
			frappe.logger().info(f"  - Getting coordinates for {field_name}")
			coordinates = get_field_coordinates(geo_area.name)
			
			if coordinates:
				latitude, longitude = coordinates
				frappe.logger().info(f"  - Coordinates found: {latitude}, {longitude}")
				frappe.logger().info(f"  - Creating weather report for {field_name}")
				
				try:
					report_name = create_weather_report_for_field(geo_area.name, fetch_hourly=True)
					
					if report_name:
						success_count += 1
						success_reports.append({
							"field": field_name,
							"field_id": geo_area.name,
							"report": report_name
						})
						frappe.logger().info(f"  ✓ Successfully created weather report {report_name} for {field_name}")
					else:
						error_count += 1
						error_msg = f"Failed to create weather report for {field_name} ({geo_area.name}) - create_weather_report_for_field returned None. Check Error Log for details."
						error_details.append({
							"field": field_name,
							"field_id": geo_area.name,
							"error": error_msg
						})
						frappe.logger().error(f"  ✗ {error_msg}")
				except Exception as create_error:
					error_count += 1
					error_msg = f"Error creating weather report for {field_name} ({geo_area.name}): {str(create_error)}"
					error_details.append({
						"field": field_name,
						"field_id": geo_area.name,
						"error": str(create_error)
					})
					frappe.logger().error(f"  ✗ {error_msg}")
					frappe.log_error(
						f"Error creating weather report for {geo_area.name} ({field_name}): {str(create_error)}\nTraceback: {frappe.get_traceback()}",
						"Manual Weather Fetch Error - Create Report"
					)
			else:
				skipped_count += 1
				error_msg = f"No coordinates available for {field_name} ({geo_area.name})"
				error_details.append({
					"field": field_name,
					"field_id": geo_area.name,
					"error": error_msg
				})
				frappe.logger().warning(f"  ⚠ {error_msg}")
				
		except Exception as e:
			error_count += 1
			error_msg = f"Exception while processing {field_name} ({geo_area.name}): {str(e)}"
			error_details.append({
				"field": field_name,
				"field_id": geo_area.name,
				"error": str(e)
			})
			frappe.logger().error(f"  ✗ {error_msg}")
			frappe.log_error(
				f"Error fetching weather for {geo_area.name} ({field_name}): {str(e)}\nTraceback: {frappe.get_traceback()}",
				"Manual Weather Fetch Error"
			)
	
	frappe.logger().info("=" * 80)
	frappe.logger().info(f"Manual weather collection completed:")
	frappe.logger().info(f"  - Total fields: {len(geo_areas)}")
	frappe.logger().info(f"  - Successful: {success_count}")
	frappe.logger().info(f"  - Errors: {error_count}")
	frappe.logger().info(f"  - Skipped: {skipped_count}")
	frappe.logger().info("=" * 80)
	
	return {
		"success_count": success_count,
		"error_count": error_count,
		"skipped_count": skipped_count,
		"total_fields": len(geo_areas),
		"error_details": error_details[:10],  # Return first 10 errors to avoid large response
		"success_reports": success_reports[:10]  # Return first 10 successes
	}


@frappe.whitelist()
def fetch_weather_for_field(geo_area_name: str) -> Dict[str, Any]:
	"""
	API endpoint to manually fetch weather data for a specific field.
	
	Args:
		geo_area_name: Name of the Geo Fencing Area
		
	Returns:
		Dictionary with status and weather report name
	"""
	if not geo_area_name:
		frappe.throw(_("Geo Fencing Area name is required"))
	
	# Verify the geo area exists
	if not frappe.db.exists("Geo Fencing Area", geo_area_name):
		frappe.throw(_("Geo Fencing Area {0} not found").format(geo_area_name))
	
	report_name = create_weather_report_for_field(geo_area_name, fetch_hourly=True)
	
	if report_name:
		return {
			"status": "success",
			"message": _("Weather report created successfully"),
			"weather_report": report_name
		}
	else:
		frappe.throw(_("Failed to create weather report. Check error logs for details."))


@frappe.whitelist()
def get_latest_weather_for_field(geo_area_name: str) -> Optional[Dict[str, Any]]:
	"""
	Get the latest weather report for a specific field.
	
	Args:
		geo_area_name: Name of the Geo Fencing Area
		
	Returns:
		Weather report data or None if not found
	"""
	if not geo_area_name:
		return None
	
	latest_report = frappe.get_all(
		"Weather Report",
		filters={"location": geo_area_name},
		fields=["*"],
		order_by="report_date desc, report_time desc",
		limit=1
	)
	
	if latest_report:
		return latest_report[0]
	
	return None

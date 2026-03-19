# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
import requests
from typing import Dict, Any, Optional


@frappe.whitelist(allow_guest=False)
def get_current_weather(latitude: float, longitude: float) -> Dict[str, Any]:
	"""
	Get current weather conditions from Google Weather API.
	
	Args:
		latitude: Latitude of the location
		longitude: Longitude of the location
		
	Returns:
		Dictionary containing current weather data
	"""
	# Try to get API key from site config, environment, or system settings
	google_api_key = (
		frappe.conf.get("google_maps_api_key") or 
		frappe.conf.get("VITE_GOOGLE_MAPS_API_KEY") or
		frappe.conf.get("GOOGLE_MAPS_API_KEY") or
		frappe.local.conf.get("google_maps_api_key")
	)
	
	if not google_api_key:
		frappe.throw("Google Maps API key not configured. Please add 'google_maps_api_key' to site_config.json")
	
	try:
		# Google Weather API v1 - Current Conditions
		# Endpoint format: GET /v1/currentConditions:lookup with query parameters
		url = f"https://weather.googleapis.com/v1/currentConditions:lookup"
		params = {
			"key": google_api_key,
			"location.latitude": float(latitude),
			"location.longitude": float(longitude)
		}
		
		response = requests.get(url, params=params, timeout=5)
		
		# Log response for debugging
		if not response.ok:
			frappe.log_error(f"Weather API Response: {response.status_code} - {response.text}", "Weather API Error")
		
		response.raise_for_status()
		
		return response.json()
	except requests.exceptions.HTTPError as e:
		error_detail = ""
		if hasattr(e.response, 'text'):
			error_detail = e.response.text
		frappe.log_error(f"Weather API HTTP Error: {str(e)} - {error_detail}", "Weather API Request Failed")
		frappe.throw(f"Failed to fetch weather data: {str(e)}. Response: {error_detail}")
	except requests.exceptions.RequestException as e:
		frappe.log_error(f"Weather API Error: {str(e)}", "Weather API Request Failed")
		frappe.throw(f"Failed to fetch weather data: {str(e)}")


@frappe.whitelist(allow_guest=False)
def get_weather_forecast(latitude: float, longitude: float, days: int = 5) -> Dict[str, Any]:
	"""
	Get weather forecast from Google Weather API.
	
	Args:
		latitude: Latitude of the location
		longitude: Longitude of the location
		days: Number of days for forecast (default: 5, max: 10)
		
	Returns:
		Dictionary containing forecast data
	"""
	# Try to get API key from site config, environment, or system settings
	google_api_key = (
		frappe.conf.get("google_maps_api_key") or 
		frappe.conf.get("VITE_GOOGLE_MAPS_API_KEY") or
		frappe.conf.get("GOOGLE_MAPS_API_KEY") or
		frappe.local.conf.get("google_maps_api_key")
	)
	
	if not google_api_key:
		frappe.throw("Google Maps API key not configured. Please add 'google_maps_api_key' to site_config.json")
	
	try:
		# Google Weather API v1 - Daily Forecast
		# Endpoint format: GET /v1/forecast/days:lookup with query parameters
		url = f"https://weather.googleapis.com/v1/forecast/days:lookup"
		params = {
			"key": google_api_key,
			"location.latitude": float(latitude),
			"location.longitude": float(longitude),
			"days": min(int(days), 10)  # Max 10 days
		}
		
		response = requests.get(url, params=params, timeout=5)
		
		# Log response for debugging
		if not response.ok:
			frappe.log_error(f"Forecast API Response: {response.status_code} - {response.text}", "Forecast API Error")
		
		response.raise_for_status()
		
		return response.json()
	except requests.exceptions.HTTPError as e:
		error_detail = ""
		if hasattr(e.response, 'text'):
			error_detail = e.response.text
		frappe.log_error(f"Forecast API HTTP Error: {str(e)} - {error_detail}", "Weather Forecast API Request Failed")
		frappe.throw(f"Failed to fetch weather forecast: {str(e)}. Response: {error_detail}")
	except requests.exceptions.RequestException as e:
		frappe.log_error(f"Weather Forecast API Error: {str(e)}", "Weather Forecast API Request Failed")
		frappe.throw(f"Failed to fetch weather forecast: {str(e)}")


@frappe.whitelist(allow_guest=False)
def get_hourly_forecast(latitude: float, longitude: float, hours: int = 24) -> Dict[str, Any]:
	"""
	Get hourly weather forecast from Google Weather API.
	
	Args:
		latitude: Latitude of the location
		longitude: Longitude of the location
		hours: Number of hours for forecast (default: 24, max: 240)
		
	Returns:
		Dictionary containing hourly forecast data
	"""
	# Try to get API key from site config, environment, or system settings
	google_api_key = (
		frappe.conf.get("google_maps_api_key") or 
		frappe.conf.get("VITE_GOOGLE_MAPS_API_KEY") or
		frappe.conf.get("GOOGLE_MAPS_API_KEY") or
		frappe.local.conf.get("google_maps_api_key")
	)
	
	if not google_api_key:
		frappe.throw("Google Maps API key not configured. Please add 'google_maps_api_key' to site_config.json")
	
	try:
		# Google Weather API v1 - Hourly Forecast
		# Endpoint format: GET /v1/forecast/hours:lookup with query parameters
		url = f"https://weather.googleapis.com/v1/forecast/hours:lookup"
		params = {
			"key": google_api_key,
			"location.latitude": float(latitude),
			"location.longitude": float(longitude),
			"hours": min(int(hours), 240)  # Max 240 hours
		}
		
		response = requests.get(url, params=params, timeout=5)
		
		# Log response for debugging
		if not response.ok:
			frappe.log_error(f"Hourly Forecast API Response: {response.status_code} - {response.text}", "Hourly Forecast API Error")
		
		response.raise_for_status()
		
		return response.json()
	except requests.exceptions.HTTPError as e:
		error_detail = ""
		if hasattr(e.response, 'text'):
			error_detail = e.response.text
		frappe.log_error(f"Hourly Forecast API HTTP Error: {str(e)} - {error_detail}", "Hourly Forecast API Request Failed")
		frappe.throw(f"Failed to fetch hourly forecast: {str(e)}. Response: {error_detail}")
	except requests.exceptions.RequestException as e:
		frappe.log_error(f"Hourly Forecast API Error: {str(e)}", "Hourly Forecast API Request Failed")
		frappe.throw(f"Failed to fetch hourly forecast: {str(e)}")


@frappe.whitelist(allow_guest=False)
def get_weather_data(latitude: float, longitude: float) -> Dict[str, Any]:
	"""
	Get both current weather and forecast data (daily and hourly).
	
	Args:
		latitude: Latitude of the location
		longitude: Longitude of the location
		
	Returns:
		Dictionary containing both current weather and forecast
	"""
	# Make API calls sequentially (threading doesn't work with Frappe's context)
	# Frontend parallelization with geocoding provides the main performance benefit
	current = get_current_weather(latitude, longitude)
	daily_forecast = get_weather_forecast(latitude, longitude, days=5)
	hourly_forecast = get_hourly_forecast(latitude, longitude, hours=24)
	
	return {
		"current": current,
		"forecast": daily_forecast,
		"hourly": hourly_forecast
	}


@frappe.whitelist(allow_guest=False)
def get_weather_data_for_field(geo_area_name: str) -> Dict[str, Any]:
	"""
	Get weather data for a Geo Fencing Area (Field) using the same coordinate logic
	as the scheduled Weather Report generator (centroid for polygons, center for circles).
	"""
	if not geo_area_name:
		frappe.throw("geo_area_name is required")
	if not frappe.db.exists("Geo Fencing Area", geo_area_name):
		frappe.throw(f"Geo Fencing Area not found: {geo_area_name}")

	from f2c.weather.scheduler import get_field_coordinates

	coords = get_field_coordinates(geo_area_name)
	if not coords:
		frappe.throw(f"No coordinates found for Geo Fencing Area: {geo_area_name}")

	lat, lng = coords
	data = get_weather_data(lat, lng)
	# Include the coordinates used for transparency/debugging
	data["_coordinates"] = {"latitude": lat, "longitude": lng, "geo_area_name": geo_area_name}
	return data


@frappe.whitelist(allow_guest=False)
def debug_compare_current_weather_for_field(field_identifier: str) -> Dict[str, Any]:
	"""
	Debug helper: Compare live current weather (API) with today's saved Weather Report for a field.

	field_identifier can be:
	- Geo Fencing Area name (e.g. GFA-0001)
	- Geo Fencing Area area_name (e.g. "Dabak 100")
	"""
	from frappe.utils import today
	from f2c.weather.scheduler import get_field_coordinates, populate_current_weather_fields

	if not field_identifier:
		frappe.throw("field_identifier is required")

	# Resolve Geo Fencing Area doc
	area = None
	if frappe.db.exists("Geo Fencing Area", field_identifier):
		area = frappe.get_doc("Geo Fencing Area", field_identifier)
	else:
		matches = frappe.get_all(
			"Geo Fencing Area",
			filters={"area_name": field_identifier},
			fields=["name"],
			limit=2
		)
		if matches:
			area = frappe.get_doc("Geo Fencing Area", matches[0].name)

	if not area:
		frappe.throw(f"Geo Fencing Area not found for '{field_identifier}'")

	coords = get_field_coordinates(area.name)
	if not coords:
		frappe.throw(f"No coordinates found for Geo Fencing Area: {area.name}")

	lat, lng = coords
	live = get_current_weather(lat, lng)

	# Find today's saved Weather Report (latest time)
	report_date = today()
	existing = frappe.get_all(
		"Weather Report",
		filters={"location": area.name, "report_date": report_date},
		fields=["name", "report_time", "modified"],
		order_by="report_time desc, modified desc",
		limit=1
	)

	if not existing:
		return {
			"field": {"name": area.name, "area_name": area.area_name, "lat": lat, "lng": lng},
			"report_date": report_date,
			"status": "no_saved_report_for_today",
			"live_current_keys": list((live or {}).keys()) if isinstance(live, dict) else None
		}

	saved_doc = frappe.get_doc("Weather Report", existing[0].name)

	# Build an "expected" doc by applying the same parsing on live response
	expected = frappe.new_doc("Weather Report")
	populate_current_weather_fields(expected, live)

	def num(v):
		try:
			if v is None or v == "":
				return None
			return float(v)
		except Exception:
			return None

	def cmp_field(fieldname: str, tol: float = 0.01):
		sv = getattr(saved_doc, fieldname, None)
		ev = getattr(expected, fieldname, None)
		sn = num(sv)
		en = num(ev)
		if sn is None and en is None:
			return {"saved": sv, "live_parsed": ev, "match": True, "diff": None}
		if sn is None or en is None:
			return {"saved": sv, "live_parsed": ev, "match": False, "diff": None}
		diff = en - sn
		return {"saved": sn, "live_parsed": en, "match": abs(diff) <= tol, "diff": diff}

	comparisons = {
		"temperature": cmp_field("temperature", tol=0.2),
		"feels_like": cmp_field("feels_like", tol=0.2),
		"humidity": cmp_field("humidity", tol=1.0),
		"precipitation": cmp_field("precipitation", tol=0.2),
		"precipitation_probability": cmp_field("precipitation_probability", tol=2.0),
		"wind_speed": cmp_field("wind_speed", tol=1.0),
		"wind_gust": cmp_field("wind_gust", tol=1.0),
		"pressure": cmp_field("pressure", tol=1.0),
		"uv_index": cmp_field("uv_index", tol=1.0),
		"visibility": cmp_field("visibility", tol=1.0),
		"cloud_cover": cmp_field("cloud_cover", tol=2.0),
	}

	# Condition strings - compare loosely
	saved_condition = (saved_doc.weather_condition or "").strip().lower()
	live_condition = (expected.weather_condition or "").strip().lower()
	condition_match = bool(saved_condition and live_condition and (saved_condition == live_condition))

	return {
		"field": {"name": area.name, "area_name": area.area_name, "lat": lat, "lng": lng},
		"report_date": report_date,
		"saved_report": {
			"name": saved_doc.name,
			"report_time": saved_doc.report_time,
			"modified": str(saved_doc.modified),
			"data_source": saved_doc.data_source,
		},
		"condition": {
			"saved": saved_doc.weather_condition,
			"live_parsed": expected.weather_condition,
			"match": condition_match,
		},
		"comparisons": comparisons,
		"note": "If values differ slightly, it's usually due to API updates between report refresh and this check."
	}


@frappe.whitelist(allow_guest=False)
def debug_get_forecast_for_field(field_identifier: str) -> Dict[str, Any]:
	"""
	Debug helper: Fetch forecast (days + hours) for a field and return a simplified summary.

	This checks the backend/provider payload (source of truth) that the Forecast tab consumes.
	"""
	from f2c.weather.scheduler import get_field_coordinates

	if not field_identifier:
		frappe.throw("field_identifier is required")

	# Resolve Geo Fencing Area doc
	area = None
	if frappe.db.exists("Geo Fencing Area", field_identifier):
		area = frappe.get_doc("Geo Fencing Area", field_identifier)
	else:
		matches = frappe.get_all(
			"Geo Fencing Area",
			filters={"area_name": field_identifier},
			fields=["name"],
			limit=2
		)
		if matches:
			area = frappe.get_doc("Geo Fencing Area", matches[0].name)

	if not area:
		frappe.throw(f"Geo Fencing Area not found for '{field_identifier}'")

	coords = get_field_coordinates(area.name)
	if not coords:
		frappe.throw(f"No coordinates found for Geo Fencing Area: {area.name}")

	lat, lng = coords
	payload = get_weather_data(lat, lng) or {}

	forecast = payload.get("forecast") or {}
	forecast_days = []
	try:
		forecast_days = (forecast.get("forecastDays") or []) if isinstance(forecast, dict) else []
	except Exception:
		forecast_days = []

	def safe_num(v):
		try:
			if v is None:
				return None
			if isinstance(v, (int, float)):
				return float(v)
			if isinstance(v, str) and v.strip():
				return float(v)
			if isinstance(v, dict):
				for k in ("degrees", "value", "percent", "quantity", "amount", "distance"):
					if k in v:
						return safe_num(v.get(k))
		except Exception:
			return None
		return None

	def get_text(obj, *path, default=None):
		cur = obj
		for p in path:
			if not isinstance(cur, dict) or p not in cur:
				return default
			cur = cur[p]
		return cur if cur is not None else default

	days_out = []
	for d in (forecast_days[:5] if isinstance(forecast_days, list) else []):
		display = d.get("displayDate") if isinstance(d, dict) else None
		iso = None
		if isinstance(display, dict):
			y = display.get("year")
			m = display.get("month")
			dd = display.get("day")
			if y and m and dd:
				iso = f"{int(y):04d}-{int(m):02d}-{int(dd):02d}"

		day_f = d.get("daytimeForecast") if isinstance(d, dict) else {}
		night_f = d.get("nighttimeForecast") if isinstance(d, dict) else {}

		max_t = safe_num(get_text(d, "maxTemperature", default=None)) or safe_num(get_text(day_f, "maxTemperature", default=None)) or safe_num(get_text(day_f, "temperature", default=None))
		min_t = safe_num(get_text(d, "minTemperature", default=None)) or safe_num(get_text(night_f, "temperature", default=None))

		cond = (
			get_text(day_f, "weatherCondition", "description", "text") or
			get_text(day_f, "weatherCondition", "type") or
			get_text(night_f, "weatherCondition", "description", "text") or
			get_text(night_f, "weatherCondition", "type")
		)

		humidity = safe_num(day_f.get("relativeHumidity") or day_f.get("humidity") or d.get("relativeHumidity") or d.get("humidity"))
		precip_prob = safe_num(get_text(day_f, "precipitationProbability", "percent")) or safe_num(day_f.get("precipitationProbability"))

		days_out.append({
			"date": iso,
			"condition": cond,
			"max_temp_c": round(max_t, 1) if isinstance(max_t, (int, float)) else None,
			"min_temp_c": round(min_t, 1) if isinstance(min_t, (int, float)) else None,
			"humidity_pct": round(humidity) if isinstance(humidity, (int, float)) else None,
			"precip_pct": round(precip_prob) if isinstance(precip_prob, (int, float)) else None,
		})

	hourly = payload.get("hourly") or {}
	forecast_hours = []
	try:
		forecast_hours = (hourly.get("forecastHours") or []) if isinstance(hourly, dict) else []
	except Exception:
		forecast_hours = []

	hours_out = []
	for h in (forecast_hours[:24] if isinstance(forecast_hours, list) else []):
		start_time = None
		if isinstance(h, dict):
			start_time = h.get("startTime") or get_text(h, "interval", "startTime")
		temp = safe_num(get_text(h, "temperature")) or safe_num(get_text(h, "weatherCondition", "temperature"))
		rh = safe_num(h.get("relativeHumidity") or h.get("humidity"))
		pp = safe_num(get_text(h, "precipitationProbability", "percent")) or safe_num(h.get("precipitationProbability"))
		hours_out.append({
			"start_time": start_time,
			"temp_c": round(temp, 1) if isinstance(temp, (int, float)) else None,
			"humidity_pct": round(rh) if isinstance(rh, (int, float)) else None,
			"precip_pct": round(pp) if isinstance(pp, (int, float)) else None,
		})

	return {
		"field": {"name": area.name, "area_name": area.area_name, "lat": lat, "lng": lng},
		"forecast_days_count": len(forecast_days) if isinstance(forecast_days, list) else None,
		"hours_count": len(forecast_hours) if isinstance(forecast_hours, list) else None,
		"days": days_out,
		"hours_first_24": hours_out,
	}


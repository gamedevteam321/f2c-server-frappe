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


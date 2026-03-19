# Copyright (c) 2026, Orgatek and contributors
# For license information, please see license.txt

import math

import frappe
from frappe.model.document import Document


def calculate_polygon_centroid(coords):
	"""Return the simple centroid of a polygon as (lat, lng)."""
	if not coords or len(coords) < 3:
		return None

	total_lat = 0.0
	total_lng = 0.0
	count = 0

	for coord in coords:
		lat = coord.get("latitude") if isinstance(coord, dict) else getattr(coord, "latitude", None)
		lng = coord.get("longitude") if isinstance(coord, dict) else getattr(coord, "longitude", None)
		if lat is None or lng is None:
			continue
		total_lat += float(lat)
		total_lng += float(lng)
		count += 1

	if count < 3:
		return None

	return (total_lat / count, total_lng / count)


def calculate_polygon_area_spherical(coords):
	"""Calculate polygon area in square meters using a spherical approximation."""
	if not coords or len(coords) < 3:
		return 0.0

	earth_radius_m = 6371000
	points = []

	for coord in coords:
		lat = coord.get("latitude") if isinstance(coord, dict) else getattr(coord, "latitude", None)
		lng = coord.get("longitude") if isinstance(coord, dict) else getattr(coord, "longitude", None)
		if lat is None or lng is None:
			continue
		points.append((math.radians(float(lat)), math.radians(float(lng))))

	if len(points) < 3:
		return 0.0

	if points[0] != points[-1]:
		points.append(points[0])

	area = 0.0
	for index in range(len(points) - 1):
		lat1, lng1 = points[index]
		lat2, lng2 = points[index + 1]
		area += (lng2 - lng1) * (2 + math.sin(lat1) + math.sin(lat2))

	return abs(area) * (earth_radius_m**2) / 2


class LeasePlot(Document):
	def validate(self):
		self._normalize_coordinates()
		self._validate_coordinates()
		self._update_summary_fields()

	def _normalize_coordinates(self):
		for index, coord in enumerate(self.geo_coordinates or [], start=1):
			coord.sequence = coord.sequence or index

	def _validate_coordinates(self):
		if not self.geo_coordinates or len(self.geo_coordinates) < 3:
			frappe.throw("At least 3 coordinates are required to create a Lease Plot.")

	def _update_summary_fields(self):
		coords = sorted(self.geo_coordinates or [], key=lambda row: row.sequence or 0)
		centroid = calculate_polygon_centroid(coords)
		if centroid:
			self.center_latitude = centroid[0]
			self.center_longitude = centroid[1]
		self.area_sq_meters = calculate_polygon_area_spherical(coords)

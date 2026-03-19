from __future__ import annotations

from collections.abc import Iterable

import frappe
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union

MERGE_GAP_TOLERANCE = 1e-6


def _normalize_ring(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
	cleaned = []
	for point in points:
		if not point or len(point) < 2:
			continue
		lng = float(point[0])
		lat = float(point[1])
		if cleaned and cleaned[-1] == (lng, lat):
			continue
		cleaned.append((lng, lat))

	if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
		cleaned.pop()

	return cleaned


def _ensure_polygon(points: Iterable[tuple[float, float]]) -> Polygon:
	ring = _normalize_ring(points)
	if len(ring) < 3:
		frappe.throw("Each selected plot must contain at least 3 valid coordinate points.")

	polygon = Polygon(ring)
	if not polygon.is_valid:
		polygon = polygon.buffer(0)

	if polygon.is_empty:
		frappe.throw("One of the selected plot boundaries could not be normalized into a valid polygon.")

	if isinstance(polygon, MultiPolygon):
		frappe.throw("One of the selected plot boundaries resolves to multiple polygons and cannot be merged safely.")

	if not isinstance(polygon, Polygon):
		frappe.throw("A selected plot boundary could not be converted into a polygon.")

	return polygon


def _extract_single_polygon(geometry) -> Polygon:
	if geometry.is_empty:
		frappe.throw("The selected plots do not produce a usable merged boundary.")

	if isinstance(geometry, Polygon):
		return geometry

	if isinstance(geometry, MultiPolygon):
		parts = [part for part in geometry.geoms if not part.is_empty]
		if len(parts) == 1:
			return parts[0]
		frappe.throw("Selected plots are disjoint. Please choose touching plots that can form one field boundary.")

	if isinstance(geometry, GeometryCollection):
		polygons = [part for part in geometry.geoms if isinstance(part, Polygon) and not part.is_empty]
		if len(polygons) == 1:
			return polygons[0]
		if len(polygons) > 1:
			frappe.throw("Selected plots are disjoint. Please choose touching plots that can form one field boundary.")

	frappe.throw("The selected plots could not be merged into a single polygon.")


def _close_small_gaps(polygons: list[Polygon], tolerance: float):
	expanded = [polygon.buffer(tolerance, join_style=2, cap_style=2) for polygon in polygons]
	merged = unary_union(expanded)

	if isinstance(merged, Polygon):
		merged = merged.buffer(-tolerance, join_style=2, cap_style=2)
	elif isinstance(merged, MultiPolygon):
		merged = MultiPolygon(
			[
				part.buffer(-tolerance, join_style=2, cap_style=2)
				for part in merged.geoms
				if not part.is_empty
			]
		)
	elif isinstance(merged, GeometryCollection):
		merged = GeometryCollection(
			[
				part.buffer(-tolerance, join_style=2, cap_style=2) if isinstance(part, Polygon) else part
				for part in merged.geoms
				if not part.is_empty
			]
		)

	return merged.buffer(0) if not merged.is_empty else merged


def merge_plot_boundaries(plot_coordinate_sets: list[list[tuple[float, float]]]) -> dict[str, object]:
	if not plot_coordinate_sets:
		frappe.throw("At least one saved plot is required to build a field draft.")

	polygons = [_ensure_polygon(coordinates) for coordinates in plot_coordinate_sets]
	merged = unary_union(polygons)
	if isinstance(merged, (MultiPolygon, GeometryCollection)):
		merged = _close_small_gaps(polygons, MERGE_GAP_TOLERANCE)
	polygon = _extract_single_polygon(merged)

	if polygon.interiors:
		frappe.throw("The selected plots create a merged boundary with holes, which cannot be used as a single field.")

	exterior = list(polygon.exterior.coords)
	if len(exterior) > 1 and exterior[0] == exterior[-1]:
		exterior.pop()

	coordinates = [
		{
			"latitude": float(lat),
			"longitude": float(lng),
			"sequence": index + 1,
		}
		for index, (lng, lat) in enumerate(exterior)
	]

	centroid = polygon.centroid
	return {
		"coordinates": coordinates,
		"center_latitude": float(centroid.y),
		"center_longitude": float(centroid.x),
	}

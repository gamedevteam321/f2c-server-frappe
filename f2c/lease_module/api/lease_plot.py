import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import frappe
from frappe.model.naming import make_autoname
from frappe.model.rename_doc import rename_doc
from frappe.utils.file_manager import get_files_path

from f2c.lease_module.plot_geomapper_bridge import merge_plot_boundaries
from f2c.lease_module.doctype.lease_plot.lease_plot import (
	calculate_polygon_area_spherical,
	calculate_polygon_centroid,
)

FIELD_PLOT_LEGEND_NAME = "Field"
FIELD_PLOT_LEGEND_DEFAULT_COLOR = "#6366F1"
FIELD_PLOT_LEGEND_DESCRIPTION = "System legend used for plots that have already been converted into fields."

def _parse_coordinate_text(raw_coordinates: str):
	points = []
	for token in (raw_coordinates or "").strip().split():
		parts = token.split(",")
		if len(parts) < 2:
			continue
		try:
			lng = float(parts[0])
			lat = float(parts[1])
		except (TypeError, ValueError):
			continue
		points.append((lat, lng))

	if len(points) > 3 and points[0] == points[-1]:
		points.pop()

	return points


def _plot_name(name: str | None, index: int) -> str:
	clean = (name or "").strip()
	return clean or f"Untitled Polygon {index + 1}"


def _parse_kml_plots(kml_string: str):
	if not kml_string or not isinstance(kml_string, str):
		return [], ["KML file is empty or invalid."]

	try:
		root = ET.fromstring(kml_string)
	except ET.ParseError:
		return [], ["Unable to parse the KML file."]

	plots = []
	warnings = []
	placemarks = root.findall(".//{*}Placemark")

	if not placemarks:
		return [], ["No Placemark elements were found in the KML file."]

	for placemark_index, placemark in enumerate(placemarks):
		name_node = placemark.find("./{*}name")
		plot_label = _plot_name(name_node.text if name_node is not None else "", placemark_index)
		polygons = placemark.findall(".//{*}Polygon")

		if not polygons:
			warnings.append(f"{plot_label} was skipped because it does not contain a polygon.")
			continue

		for polygon_index, polygon in enumerate(polygons):
			coordinates_node = polygon.find(".//{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
			if coordinates_node is None:
				coordinates_node = polygon.find(".//{*}coordinates")
			coordinates = _parse_coordinate_text(coordinates_node.text if coordinates_node is not None else "")
			if len(coordinates) < 3:
				warnings.append(f"{plot_label} polygon {polygon_index + 1} was skipped because it has fewer than 3 points.")
				continue

			plots.append(
				{
					"name": plot_label if len(polygons) == 1 else f"{plot_label} {polygon_index + 1}",
					"coordinates": coordinates,
				}
			)

	return plots, warnings


def _get_kml_file_path(file_url: str, file_name: str | None = None) -> str:
	clean_file_url = (file_url or "").strip().split("?")[0]
	if not clean_file_url.startswith("/files/") and not clean_file_url.startswith("/private/files/"):
		frappe.throw("Only uploaded KML files from Frappe can be imported.")

	if file_name and frappe.db.exists("File", file_name):
		try:
			file_doc = frappe.get_doc("File", file_name)
			file_path = file_doc.get_full_path()
			if file_path and os.path.exists(file_path):
				return file_path
		except Exception:
			pass

	if clean_file_url.startswith("/private/files/"):
		relative_path = clean_file_url.split("/private/files/", 1)[1]
		path_parts = [unquote(part) for part in relative_path.split("/") if part]
		file_path = get_files_path(*path_parts, is_private=1)
	else:
		relative_path = clean_file_url.split("/files/", 1)[1]
		path_parts = [unquote(part) for part in relative_path.split("/") if part]
		file_path = get_files_path(*path_parts)

	if not os.path.exists(file_path):
		frappe.throw(f"KML file not found: {clean_file_url}")

	return file_path


def _build_import_batch(source_file_name: str) -> str:
	base = re.sub(r"[^A-Za-z0-9]+", "-", Path(source_file_name).stem).strip("-").upper()
	base = (base or "LEASE-PLOT")[:24]
	return f"{base}-{frappe.utils.now_datetime().strftime('%Y%m%d-%H%M%S')}-{frappe.generate_hash(length=6).upper()}"


def _normalize_plot_color(plot_color: str | None) -> str:
	clean = (plot_color or "").strip().upper()
	if not clean:
		return ""

	if not re.fullmatch(r"#[0-9A-F]{6}", clean):
		frappe.throw("Plot color must be a valid hex color like #10B981.")

	return clean


def _normalize_plot_legend(plot_legend: str | None) -> str:
	clean = (plot_legend or "").strip()
	if not clean:
		return ""

	if clean == FIELD_PLOT_LEGEND_NAME:
		return _ensure_field_plot_legend()

	if not frappe.db.exists("Lease Plot Legend", clean):
		frappe.throw(f"Lease Plot Legend not found: {clean}")

	return clean


def _ensure_field_plot_legend() -> str:
	if frappe.db.exists("Lease Plot Legend", FIELD_PLOT_LEGEND_NAME):
		return FIELD_PLOT_LEGEND_NAME

	doc = frappe.get_doc(
		{
			"doctype": "Lease Plot Legend",
			"legend_name": FIELD_PLOT_LEGEND_NAME,
			"legend_color": FIELD_PLOT_LEGEND_DEFAULT_COLOR,
			"description": FIELD_PLOT_LEGEND_DESCRIPTION,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _coerce_plot_colors(plot_colors):
	if not plot_colors:
		return []

	if isinstance(plot_colors, str):
		try:
			plot_colors = json.loads(plot_colors)
		except Exception:
			plot_colors = [plot_colors]

	if not isinstance(plot_colors, (list, tuple)):
		return []

	return [_normalize_plot_color(color) for color in plot_colors]


def _coerce_plot_names(plot_names):
	if not plot_names:
		return []

	if isinstance(plot_names, str):
		try:
			plot_names = json.loads(plot_names)
		except Exception:
			plot_names = [plot_names]

	if not isinstance(plot_names, (list, tuple)):
		frappe.throw("plot_names must be a list of Lease Plot names.")

	unique_names = []
	seen = set()
	for plot_name in plot_names:
		clean = (plot_name or "").strip()
		if not clean or clean in seen:
			continue
		seen.add(clean)
		unique_names.append(clean)

	return unique_names


def _get_selected_plots(plot_names):
	rows = frappe.get_all(
		"Lease Plot",
		filters={"name": ["in", plot_names]},
		fields=[
			"name",
			"plot_name",
			"import_batch",
			"plot_index",
			"source_file_name",
			"plot_legend",
			"plot_color",
			"linked_geo_fencing_area",
			"area_sq_meters",
			"center_latitude",
			"center_longitude",
		],
		limit=len(plot_names),
	)
	if len(rows) != len(plot_names):
		found = {row["name"] for row in rows}
		missing = [plot_name for plot_name in plot_names if plot_name not in found]
		frappe.throw(f"Some selected plots were not found: {', '.join(missing)}")

	coordinates = frappe.get_all(
		"Lease Plot Coordinate",
		filters={
			"parent": ["in", plot_names],
			"parenttype": "Lease Plot",
		},
		fields=["parent", "latitude", "longitude", "sequence"],
		order_by="parent asc, sequence asc",
		limit_page_length=max(1000, len(plot_names) * 100),
	)

	coordinates_by_parent = {}
	for row in coordinates:
		coordinates_by_parent.setdefault(row["parent"], []).append(
			{
				"latitude": row["latitude"],
				"longitude": row["longitude"],
				"sequence": row["sequence"],
			}
		)

	plots_by_name = {row["name"]: row for row in rows}
	selected_plots = []
	for plot_name in plot_names:
		plot = dict(plots_by_name[plot_name])
		plot["geo_coordinates"] = coordinates_by_parent.get(plot_name, [])
		if len(plot["geo_coordinates"]) < 3:
			frappe.throw(f'Selected plot "{plot.get("plot_name") or plot_name}" does not have a valid saved boundary.')
		selected_plots.append(plot)

	return selected_plots


def _assert_plots_are_unmapped(selected_plots):
	mapped_plots = [plot for plot in selected_plots if (plot.get("linked_geo_fencing_area") or "").strip()]
	if mapped_plots:
		plot_labels = [plot.get("plot_name") or plot.get("name") for plot in mapped_plots]
		frappe.throw(
			"These plots are already mapped to a field and cannot be used again: "
			+ ", ".join(plot_labels[:5])
			+ ("." if len(plot_labels) <= 5 else ", and more.")
		)


def _insert_plots_bulk(import_batch: str, source_file_name: str, file_url: str, plots, plot_colors=None):
	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"
	plot_colors = _coerce_plot_colors(plot_colors)

	plot_fields = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"plot_name",
		"import_batch",
		"plot_index",
		"source_file_name",
		"kml_file",
		"plot_legend",
		"plot_color",
		"center_latitude",
		"center_longitude",
		"area_sq_meters",
	]
	coordinate_fields = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
		"latitude",
		"longitude",
		"sequence",
	]

	plot_values = []
	coordinate_values = []
	for index, plot in enumerate(plots, start=1):
		coordinates = []
		for sequence, (lat, lng) in enumerate(plot["coordinates"], start=1):
			coordinates.append(
				{
					"latitude": lat,
					"longitude": lng,
					"sequence": sequence,
				}
			)

		centroid = calculate_polygon_centroid(coordinates)
		area_sq_meters = calculate_polygon_area_spherical(coordinates)
		plot_name = make_autoname("LPL-.#####")
		plot_color = plot_colors[index - 1] if index - 1 < len(plot_colors) else ""

		plot_values.append(
			(
				plot_name,
				now,
				now,
				user,
				user,
				0,
				index,
				plot["name"],
				import_batch,
				index,
				source_file_name,
				file_url,
				None,
					plot_color or None,
				centroid[0] if centroid else None,
				centroid[1] if centroid else None,
				area_sq_meters,
			)
		)

		for coord in coordinates:
			coordinate_values.append(
				(
					frappe.generate_hash(length=10),
					now,
					now,
					user,
					user,
					0,
					coord["sequence"],
					plot_name,
					"geo_coordinates",
					"Lease Plot",
					coord["latitude"],
					coord["longitude"],
					coord["sequence"],
				)
			)

	frappe.db.bulk_insert("Lease Plot", fields=plot_fields, values=plot_values, chunk_size=500)
	if coordinate_values:
		frappe.db.bulk_insert(
			"Lease Plot Coordinate",
			fields=coordinate_fields,
			values=coordinate_values,
			chunk_size=5000,
		)


def _get_first_plot_from_kml(file_url: str):
	file_rows = frappe.get_all(
		"File",
		filters={"file_url": file_url},
		fields=["name", "file_name"],
		limit=1,
	)
	if not file_rows:
		frappe.throw("The uploaded file could not be found in Frappe.")

	file_path = _get_kml_file_path(file_url, file_rows[0].get("name"))
	with open(file_path, "r", encoding="utf-8") as handle:
		kml_string = handle.read()

	plots, warnings = _parse_kml_plots(kml_string)
	if not plots:
		frappe.throw("\n".join(warnings) or "No valid plots were found in the KML file.")

	if len(plots) > 1:
		warnings.append("Plot edit used only the first polygon found in the selected KML file.")

	return plots[0], warnings


@frappe.whitelist()
def import_kml(file_url: str, import_mode: str = "bulk", plot_colors=None):
	file_url = (file_url or "").strip()
	import_mode = (import_mode or "bulk").strip().lower()
	if import_mode not in {"single", "bulk"}:
		frappe.throw("import_mode must be either 'single' or 'bulk'.")

	file_doc = frappe.get_all(
		"File",
		filters={"file_url": file_url},
		fields=["name", "file_name", "file_url"],
		limit=1,
	)
	if not file_doc:
		frappe.throw("The uploaded file could not be found in Frappe.")

	source_file_name = file_doc[0].get("file_name") or Path(file_url).name
	file_path = _get_kml_file_path(file_url, file_doc[0].get("name"))
	with open(file_path, "r", encoding="utf-8") as handle:
		kml_string = handle.read()

	plots, warnings = _parse_kml_plots(kml_string)
	if not plots:
		frappe.throw("\n".join(warnings) or "No valid plots were found in the KML file.")

	if import_mode == "single":
		if len(plots) > 1:
			warnings.append("Single plot import used only the first polygon found in the KML file.")
		plots = plots[:1]

	import_batch = _build_import_batch(source_file_name)
	_insert_plots_bulk(import_batch, source_file_name, file_url, plots, plot_colors=plot_colors)

	frappe.db.commit()
	return {
		"import_batch": import_batch,
		"source_file_name": source_file_name,
		"plot_count": len(plots),
		"warnings": warnings,
	}


@frappe.whitelist()
def list_plot_import_batches():
	rows = frappe.db.sql(
		"""
		SELECT
			import_batch,
			MAX(source_file_name) AS source_file_name,
			MAX(kml_file) AS kml_file,
			COUNT(name) AS plot_count,
			MAX(modified) AS modified
		FROM `tabLease Plot`
		WHERE COALESCE(import_batch, '') != ''
		GROUP BY import_batch
		ORDER BY MAX(modified) DESC
		""",
		as_dict=True,
	)
	return rows


@frappe.whitelist()
def search_plot_import_batches(query: str):
	query = (query or "").strip().lower()
	if not query:
		return []

	like_query = f"%{query}%"
	rows = frappe.db.sql(
		"""
		SELECT
			p.import_batch,
			MAX(p.source_file_name) AS source_file_name,
			MAX(p.kml_file) AS kml_file,
			COUNT(DISTINCT p.name) AS plot_count,
			MAX(p.modified) AS modified
		FROM `tabLease Plot` p
		WHERE
			LOWER(COALESCE(p.import_batch, '')) LIKE %s
			OR LOWER(COALESCE(p.source_file_name, '')) LIKE %s
			OR LOWER(COALESCE(p.plot_name, '')) LIKE %s
		GROUP BY p.import_batch
		ORDER BY MAX(p.modified) DESC
		""",
		(like_query, like_query, like_query),
		as_dict=True,
	)
	return rows


@frappe.whitelist()
def get_plots_by_batch(import_batch: str):
	import_batch = (import_batch or "").strip()
	if not import_batch:
		return []

	rows = frappe.db.sql(
		"""
		SELECT
			p.name,
			p.plot_name,
			p.import_batch,
			p.plot_index,
			p.source_file_name,
			p.village_name,
			p.village_code,
			p.tehsil_name,
			p.area_type,
			p.remarks,
			p.kml_file,
			p.plot_legend,
			p.plot_color,
			p.linked_geo_fencing_area,
			p.area_sq_meters,
			p.center_latitude,
			p.center_longitude,
			c.latitude,
			c.longitude,
			c.sequence
		FROM `tabLease Plot` p
		LEFT JOIN `tabLease Plot Coordinate` c
			ON c.parent = p.name
			AND c.parenttype = 'Lease Plot'
		WHERE p.import_batch = %s
		ORDER BY p.plot_index ASC, c.sequence ASC
		""",
		(import_batch,),
		as_dict=True,
	)

	plots_by_name = {}
	for row in rows:
		plot_name = row["name"]
		if plot_name not in plots_by_name:
			plots_by_name[plot_name] = {
				"name": plot_name,
				"plot_name": row["plot_name"],
				"import_batch": row["import_batch"],
				"plot_index": row["plot_index"],
				"source_file_name": row["source_file_name"],
				"village_name": row["village_name"],
				"village_code": row["village_code"],
				"tehsil_name": row["tehsil_name"],
				"area_type": row["area_type"],
				"remarks": row["remarks"],
				"kml_file": row["kml_file"],
				"plot_legend": row["plot_legend"],
				"plot_color": row["plot_color"],
				"linked_geo_fencing_area": row["linked_geo_fencing_area"],
				"area_sq_meters": row["area_sq_meters"],
				"center_latitude": row["center_latitude"],
				"center_longitude": row["center_longitude"],
				"geo_coordinates": [],
			}

		if row.get("latitude") is not None and row.get("longitude") is not None:
			plots_by_name[plot_name]["geo_coordinates"].append(
				{
					"latitude": row["latitude"],
					"longitude": row["longitude"],
					"sequence": row["sequence"],
				}
			)

	return list(plots_by_name.values())


@frappe.whitelist()
def build_geo_mapper_field_draft(plot_names):
	selected_plot_names = _coerce_plot_names(plot_names)
	if not selected_plot_names:
		frappe.throw("Select at least one saved plot before creating a field.")

	selected_plots = _get_selected_plots(selected_plot_names)
	_assert_plots_are_unmapped(selected_plots)
	merged_boundary = merge_plot_boundaries(
		[
			[
				(float(coord["longitude"]), float(coord["latitude"]))
				for coord in sorted(plot["geo_coordinates"], key=lambda item: item.get("sequence") or 0)
			]
			for plot in selected_plots
		]
	)
	merged_area_sq_meters = calculate_polygon_area_spherical(merged_boundary["coordinates"])

	return {
		"geo_fencing_type": "Field",
		"shape_type": "Polygon",
		"plot_count": len(selected_plots),
		"plot_names": selected_plot_names,
		"import_batches": sorted(
			{
				(plot.get("import_batch") or "").strip()
				for plot in selected_plots
				if (plot.get("import_batch") or "").strip()
			}
		),
		"selected_plots": [
			{
				"name": plot["name"],
				"plot_name": plot["plot_name"],
				"import_batch": plot["import_batch"],
				"plot_legend": plot.get("plot_legend"),
				"plot_color": plot.get("plot_color"),
				"linked_geo_fencing_area": plot.get("linked_geo_fencing_area"),
				"area_sq_meters": plot.get("area_sq_meters"),
			}
			for plot in selected_plots
		],
		"merged_area_sq_meters": merged_area_sq_meters,
		"center_latitude": merged_boundary["center_latitude"],
		"center_longitude": merged_boundary["center_longitude"],
		"geo_coordinates": merged_boundary["coordinates"],
		"geo_location": [
			[coord["latitude"], coord["longitude"]]
			for coord in merged_boundary["coordinates"]
		],
	}


@frappe.whitelist()
def mark_plots_as_field_mapped(plot_names, geo_fencing_area_name: str):
	selected_plot_names = _coerce_plot_names(plot_names)
	geo_fencing_area_name = (geo_fencing_area_name or "").strip()
	if not selected_plot_names:
		frappe.throw("plot_names is required.")
	if not geo_fencing_area_name:
		frappe.throw("geo_fencing_area_name is required.")
	if not frappe.db.exists("Geo Fencing Area", geo_fencing_area_name):
		frappe.throw(f"Geo Fencing Area not found: {geo_fencing_area_name}")

	selected_plots = _get_selected_plots(selected_plot_names)
	_assert_plots_are_unmapped(selected_plots)
	field_legend_name = _ensure_field_plot_legend()
	field_legend_color = frappe.db.get_value("Lease Plot Legend", field_legend_name, "legend_color") or FIELD_PLOT_LEGEND_DEFAULT_COLOR

	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"
	frappe.db.sql(
		"""
		UPDATE `tabLease Plot`
		SET
			linked_geo_fencing_area = %(geo_fencing_area_name)s,
			plot_legend = %(field_legend_name)s,
			plot_color = %(field_legend_color)s,
			modified = %(now)s,
			modified_by = %(user)s
		WHERE name IN %(plot_names)s
		""",
		{
			"plot_names": tuple(selected_plot_names),
			"geo_fencing_area_name": geo_fencing_area_name,
			"field_legend_name": field_legend_name,
			"field_legend_color": field_legend_color,
			"now": now,
			"user": user,
		},
	)
	frappe.db.commit()
	return {
		"geo_fencing_area_name": geo_fencing_area_name,
		"plot_names": selected_plot_names,
	}


@frappe.whitelist()
def clear_field_mapping_for_geo_area(geo_fencing_area_name: str):
	geo_fencing_area_name = (geo_fencing_area_name or "").strip()
	if not geo_fencing_area_name:
		frappe.throw("geo_fencing_area_name is required.")

	field_legend_name = _ensure_field_plot_legend()
	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"
	frappe.db.sql(
		"""
		UPDATE `tabLease Plot`
		SET
			linked_geo_fencing_area = NULL,
			plot_legend = CASE
				WHEN plot_legend = %(field_legend_name)s THEN NULL
				ELSE plot_legend
			END,
			plot_color = CASE
				WHEN plot_legend = %(field_legend_name)s THEN NULL
				ELSE plot_color
			END,
			modified = %(now)s,
			modified_by = %(user)s
		WHERE linked_geo_fencing_area = %(geo_fencing_area_name)s
		""",
		{
			"geo_fencing_area_name": geo_fencing_area_name,
			"field_legend_name": field_legend_name,
			"now": now,
			"user": user,
		},
	)
	frappe.db.commit()
	return {"geo_fencing_area_name": geo_fencing_area_name}


@frappe.whitelist()
def list_plot_legends():
	_ensure_field_plot_legend()
	return frappe.get_all(
		"Lease Plot Legend",
		fields=["name", "legend_name", "legend_color", "description", "modified"],
		order_by="modified desc",
	)


@frappe.whitelist()
def create_plot_legend(legend_name: str, legend_color: str, description: str | None = None):
	legend_name = (legend_name or "").strip()
	if not legend_name:
		frappe.throw("legend_name is required.")

	doc = frappe.get_doc(
		{
			"doctype": "Lease Plot Legend",
			"legend_name": legend_name,
			"legend_color": _normalize_plot_color(legend_color),
			"description": (description or "").strip(),
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {
		"name": doc.name,
		"legend_name": doc.legend_name,
		"legend_color": doc.legend_color,
		"description": doc.description,
	}


@frappe.whitelist()
def rename_plot_legend(legend_name: str, new_name: str):
	legend_name = _normalize_plot_legend(legend_name)
	new_name = (new_name or "").strip()
	if not legend_name or not new_name:
		frappe.throw("legend_name and new_name are required.")
	if legend_name == FIELD_PLOT_LEGEND_NAME:
		frappe.throw("The Field legend is system-managed and cannot be renamed.")
	if legend_name == new_name:
		return {"name": legend_name, "legend_name": legend_name}

	renamed = rename_doc(
		"Lease Plot Legend",
		legend_name,
		new_name,
		ignore_permissions=True,
		show_alert=False,
	)
	frappe.db.commit()
	return {"name": renamed, "legend_name": renamed}


@frappe.whitelist()
def delete_plot_legend(legend_name: str):
	legend_name = _normalize_plot_legend(legend_name)
	if not legend_name:
		frappe.throw("legend_name is required.")
	if legend_name == FIELD_PLOT_LEGEND_NAME:
		frappe.throw("The Field legend is system-managed and cannot be removed.")

	frappe.db.sql(
		"""
		UPDATE `tabLease Plot`
		SET plot_legend = NULL, plot_color = NULL, modified = %s, modified_by = %s
		WHERE plot_legend = %s
		""",
		(frappe.utils.now(), frappe.session.user or "Administrator", legend_name),
	)
	frappe.delete_doc("Lease Plot Legend", legend_name, ignore_permissions=True)
	frappe.db.commit()
	return {"name": legend_name, "deleted": 1}


@frappe.whitelist()
def update_plot_legend_color(legend_name: str, legend_color: str):
	legend_name = _normalize_plot_legend(legend_name)
	if not legend_name:
		frappe.throw("legend_name is required.")

	normalized_color = _normalize_plot_color(legend_color)
	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"

	frappe.db.sql(
		"""
		UPDATE `tabLease Plot Legend`
		SET legend_color = %(legend_color)s, modified = %(now)s, modified_by = %(user)s
		WHERE name = %(legend_name)s
		""",
		{
			"legend_name": legend_name,
			"legend_color": normalized_color,
			"now": now,
			"user": user,
		},
	)

	frappe.db.sql(
		"""
		UPDATE `tabLease Plot`
		SET plot_color = %(legend_color)s, modified = %(now)s, modified_by = %(user)s
		WHERE plot_legend = %(legend_name)s
		""",
		{
			"legend_name": legend_name,
			"legend_color": normalized_color,
			"now": now,
			"user": user,
		},
	)

	frappe.db.commit()
	return {"name": legend_name, "legend_color": normalized_color}


@frappe.whitelist()
def rename_plot(plot_name: str, new_name: str):
	plot_name = (plot_name or "").strip()
	new_name = (new_name or "").strip()
	if not plot_name or not new_name:
		frappe.throw("plot_name and new_name are required.")

	doc = frappe.get_doc("Lease Plot", plot_name)
	doc.plot_name = new_name
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name, "plot_name": doc.plot_name}


@frappe.whitelist()
def update_plot_metadata(
	plot_name: str,
	village_name: str | None = None,
	village_code: str | None = None,
	tehsil_name: str | None = None,
	area_type: str | None = None,
	remarks: str | None = None,
):
	plot_name = (plot_name or "").strip()
	if not plot_name:
		frappe.throw("plot_name is required.")

	doc = frappe.get_doc("Lease Plot", plot_name)
	doc.village_name = (village_name or "").strip() or None
	doc.village_code = (village_code or "").strip() or None
	doc.tehsil_name = (tehsil_name or "").strip() or None
	doc.area_type = (area_type or "").strip() or None
	doc.remarks = (remarks or "").strip() or None
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"name": doc.name,
		"village_name": doc.village_name,
		"village_code": doc.village_code,
		"tehsil_name": doc.tehsil_name,
		"area_type": doc.area_type,
		"remarks": doc.remarks,
	}


@frappe.whitelist()
def update_import_batch_metadata(
	import_batch: str,
	village_name: str | None = None,
	village_code: str | None = None,
	tehsil_name: str | None = None,
	area_type: str | None = None,
):
	import_batch = (import_batch or "").strip()
	if not import_batch:
		frappe.throw("import_batch is required.")

	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"
	values = {
		"import_batch": import_batch,
		"village_name": (village_name or "").strip() or None,
		"village_code": (village_code or "").strip() or None,
		"tehsil_name": (tehsil_name or "").strip() or None,
		"area_type": (area_type or "").strip() or None,
		"now": now,
		"user": user,
	}

	frappe.db.sql(
		"""
		UPDATE `tabLease Plot`
		SET
			village_name = %(village_name)s,
			village_code = %(village_code)s,
			tehsil_name = %(tehsil_name)s,
			area_type = %(area_type)s,
			modified = %(now)s,
			modified_by = %(user)s
		WHERE import_batch = %(import_batch)s
		""",
		values,
	)
	frappe.db.commit()
	return {
		"import_batch": import_batch,
		"village_name": values["village_name"],
		"village_code": values["village_code"],
		"tehsil_name": values["tehsil_name"],
		"area_type": values["area_type"],
	}


@frappe.whitelist()
def rename_import_batch(import_batch: str, new_label: str):
	import_batch = (import_batch or "").strip()
	new_label = (new_label or "").strip()
	if not import_batch or not new_label:
		frappe.throw("import_batch and new_label are required.")

	frappe.db.sql(
		"""
		UPDATE `tabLease Plot`
		SET source_file_name = %s, modified = %s, modified_by = %s
		WHERE import_batch = %s
		""",
		(new_label, frappe.utils.now(), frappe.session.user or "Administrator", import_batch),
	)
	frappe.db.commit()
	return {"import_batch": import_batch, "source_file_name": new_label}


@frappe.whitelist()
def delete_plot(plot_name: str):
	plot_name = (plot_name or "").strip()
	if not plot_name:
		frappe.throw("plot_name is required.")

	if not frappe.db.exists("Lease Plot", plot_name):
		frappe.throw("Lease Plot not found.")

	frappe.delete_doc("Lease Plot", plot_name, ignore_permissions=True)
	frappe.db.commit()
	return {"name": plot_name, "deleted": 1}


@frappe.whitelist()
def delete_import_batch(import_batch: str):
	import_batch = (import_batch or "").strip()
	if not import_batch:
		frappe.throw("import_batch is required.")

	plot_names = frappe.get_all("Lease Plot", filters={"import_batch": import_batch}, pluck="name")
	if not plot_names:
		return {"import_batch": import_batch, "deleted": 0}

	frappe.db.delete(
		"Lease Plot Coordinate",
		{
			"parent": ["in", plot_names],
			"parenttype": "Lease Plot",
		},
	)
	frappe.db.delete("Lease Plot", {"import_batch": import_batch})
	frappe.db.commit()
	return {"import_batch": import_batch, "deleted": len(plot_names)}


@frappe.whitelist()
def update_plot_geometry_from_kml(plot_name: str, file_url: str):
	plot_name = (plot_name or "").strip()
	file_url = (file_url or "").strip()
	if not plot_name or not file_url:
		frappe.throw("plot_name and file_url are required.")

	doc = frappe.get_doc("Lease Plot", plot_name)
	first_plot, warnings = _get_first_plot_from_kml(file_url)
	doc.kml_file = file_url
	doc.set(
		"geo_coordinates",
		[
			{
				"doctype": "Lease Plot Coordinate",
				"latitude": lat,
				"longitude": lng,
				"sequence": sequence,
			}
			for sequence, (lat, lng) in enumerate(first_plot["coordinates"], start=1)
		],
	)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"name": doc.name,
		"plot_name": doc.plot_name,
		"plot_legend": doc.plot_legend,
		"plot_color": doc.plot_color,
		"area_sq_meters": doc.area_sq_meters,
		"warnings": warnings,
	}


@frappe.whitelist()
def update_plot_color(plot_name: str, plot_color: str):
	plot_name = (plot_name or "").strip()
	if not plot_name:
		frappe.throw("plot_name is required.")

	doc = frappe.get_doc("Lease Plot", plot_name)
	doc.plot_color = _normalize_plot_color(plot_color)
	if doc.linked_geo_fencing_area and doc.plot_legend == FIELD_PLOT_LEGEND_NAME:
		doc.plot_legend = FIELD_PLOT_LEGEND_NAME
	else:
		doc.plot_legend = None
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name, "plot_color": doc.plot_color}


@frappe.whitelist()
def update_plot_legend(plot_name: str, plot_legend: str | None = None):
	plot_name = (plot_name or "").strip()
	if not plot_name:
		frappe.throw("plot_name is required.")

	doc = frappe.get_doc("Lease Plot", plot_name)
	legend_name = _normalize_plot_legend(plot_legend)
	if doc.linked_geo_fencing_area:
		field_legend_name = _ensure_field_plot_legend()
		if not legend_name or legend_name != field_legend_name:
			frappe.throw("Plots that are already mapped to a field must keep the Field legend assigned.")

	if not legend_name:
		doc.plot_legend = None
		doc.plot_color = None
	else:
		legend_doc = frappe.get_doc("Lease Plot Legend", legend_name)
		doc.plot_legend = legend_doc.name
		doc.plot_color = legend_doc.legend_color

	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"name": doc.name,
		"plot_legend": doc.plot_legend,
		"plot_color": doc.plot_color,
	}

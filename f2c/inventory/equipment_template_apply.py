# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Tuple

import frappe
from frappe.model.document import Document


def _copy(template: Document, doc: Document, src: str, dest: str):
	# Overwrite-all semantics: copy even falsy values (None clears),
	# but never clear critical link fields if template doesn't provide one.
	val = template.get(src)
	# For JSON specs, older templates may not have the field; don't wipe doc specs in that case.
	if src in {"default_item_code", "default_status", "specs_json"} and (val is None or val == ""):
		return
	doc.set(dest, val)


_CONFIG: Dict[str, Dict] = {
	"Machinery": {
		"template_field": "machinery_template",
		"template_doctype": "Machinery Template",
		"type_field": "machinery_type",
		"mappings": [
			("machinery_type", "machinery_type"),
			("brand", "brand"),
			("model", "model"),
			("default_item_code", "item_code"),
			("default_status", "status"),
			("hp", "hp"),
			("cylinders", "cylinders"),
			("cc", "cc"),
			("rpm", "rpm"),
			("torque", "torque"),
			("cooling", "cooling"),
			("fuel_type", "fuel_type"),
			("engine_model", "engine_model"),
			("transmission_type", "transmission_type"),
			("gearbox", "gearbox"),
			("clutch", "clutch"),
			("speeds_forward", "speeds_forward"),
			("speeds_reverse", "speeds_reverse"),
			("brake_type", "brake_type"),
			("steering_type", "steering_type"),
			("lifting_capacity", "lifting_capacity"),
			("linkage", "linkage"),
			("pto_specs", "pto_specs"),
			("tank_capacity", "tank_capacity"),
			("weight", "weight"),
			("wheelbase", "wheelbase"),
			("length", "length"),
			("width", "width"),
			("height", "height"),
			("drive_type", "drive_type"),
			("front_tyre_size", "front_tyre_size"),
			("rear_tyre_size", "rear_tyre_size"),
			("cutter_width", "cutter_width"),
			("cutting_height", "cutting_height"),
			("reel_controls", "reel_controls"),
			("drum_specs", "drum_specs"),
			("drum_speed", "drum_speed"),
			("concave_adjustment", "concave_adjustment"),
			("spray_tank_capacity", "spray_tank_capacity"),
			("spray_width", "spray_width"),
			("nozzle_count", "nozzle_count"),
			("pressure_range", "pressure_range"),
			("pump_type", "pump_type"),
			("warranty", "warranty"),
			("battery", "battery"),
			("default_description", "description"),
			("default_features", "features"),
			("specs_json", "specs_json"),
		],
	},
	"Implement": {
		"template_field": "implement_template",
		"template_doctype": "Implement Template",
		"type_field": "implement_type",
		"mappings": [
			("implement_type", "implement_type"),
			("brand", "brand"),
			("model", "model"),
			("default_item_code", "item_code"),
			("default_status", "status"),
			("category", "category"),
			("power_requirement", "power_requirement"),
			("working_width", "working_width"),
			("working_depth", "working_depth"),
			("weight", "weight"),
			("tines_blades", "tines_blades"),
			("pto_speed", "pto_speed"),
			("hitch_type", "hitch_type"),
			("length", "length"),
			("width", "width"),
			("height", "height"),
			("transmission_type", "transmission_type"),
			("safety_devices", "safety_devices"),
			("applications", "applications"),
			("default_description", "description"),
			("default_features", "features"),
			("specs_json", "specs_json"),
		],
	},
	"Hand Tool": {
		"template_field": "hand_tool_template",
		"template_doctype": "Hand Tool Template",
		"type_field": "hand_tool_type",
		"mappings": [
			("hand_tool_type", "hand_tool_type"),
			("brand", "brand"),
			("model", "model"),
			("default_item_code", "item_code"),
			("default_status", "status"),
			("material", "material"),
			("dimensions", "dimensions"),
			("capacity", "capacity"),
			("pressure_rating", "pressure_rating"),
			("connection_type", "connection_type"),
			("usage", "usage"),
			("weight", "weight"),
			("default_description", "description"),
			("default_features", "features"),
			("specs_json", "specs_json"),
		],
	},
	"Other Tool": {
		"template_field": "other_tool_template",
		"template_doctype": "Other Tool Template",
		"type_field": "other_tool_type",
		"mappings": [
			("other_tool_type", "other_tool_type"),
			("brand", "brand"),
			("model", "model"),
			("default_item_code", "item_code"),
			("default_status", "status"),
			("material", "material"),
			("dimensions", "dimensions"),
			("weight", "weight"),
			("default_description", "description"),
			("default_features", "features"),
			("specs_json", "specs_json"),
		],
	},
}


def apply_template_overwrite(doc: Document) -> bool:
	"""
	If the doc has a template link field set, overwrite mapped fields from that template.
	Returns True if applied.
	"""
	if not doc or not getattr(doc, "doctype", None):
		return False

	cfg = _CONFIG.get(doc.doctype)
	if not cfg:
		return False

	template_name = doc.get(cfg["template_field"])
	if not template_name:
		return False

	template = frappe.get_doc(cfg["template_doctype"], template_name)

	# Safety: ensure template type matches doc type field (or overwrite it).
	for src, dest in cfg["mappings"]:
		_copy(template, doc, src, dest)

	return True


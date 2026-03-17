# -*- coding: utf-8 -*-
"""
Default values for Asset creation when Item does not have asset_naming_series
or asset_category set. Used by Machinery, Implement, Hand Tool, and Other Tool
when auto-creating Assets.
"""
from __future__ import annotations

import frappe

# Must match f2c.patches.asset_naming_series.NEW_ASSET_NAMING_SERIES
DEFAULT_ASSET_NAMING_SERIES = "AST-.YYYY.-"


def get_default_asset_naming_series() -> str:
	"""Return the default Asset naming series when Item has none set."""
	return DEFAULT_ASSET_NAMING_SERIES


def get_default_asset_category() -> str | None:
	"""Return the first available Asset Category name, or None if none exists."""
	if not frappe.db.table_exists("Asset Category"):
		return None
	name = frappe.db.get_value("Asset Category", None, "name", order_by="name asc")
	return name

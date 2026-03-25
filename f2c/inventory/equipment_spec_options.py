# -*- coding: utf-8 -*-
# Copyright (c) 2026, Orgatek and contributors

from __future__ import annotations

import frappe
from frappe import _

OPTION_TYPES: tuple[str, ...] = (
	"Market",
	"Power Source",
	"Fuel Type",
	"Mounting Type",
	"Spec Status",
	"Criticality",
	"PTO Speed Required",
	"Hitch Category",
	"Safety Mechanism",
)


def _titles_for_type(option_type: str) -> list[str]:
	rows = frappe.get_all(
		"Equipment Spec Option",
		filters={"option_type": option_type, "disabled": 0},
		fields=["title"],
		order_by="sort_order asc, title asc",
		limit_page_length=500,
	)
	return [r.title for r in rows if r.get("title")]


@frappe.whitelist()
def get_options(option_type: str) -> list[str]:
	"""Return sorted active titles for one option type (for dropdowns)."""
	option_type = (option_type or "").strip()
	if option_type not in OPTION_TYPES:
		frappe.throw(_("Invalid option_type: {0}").format(option_type))
	return _titles_for_type(option_type)


@frappe.whitelist()
def get_all_option_lists() -> dict[str, list[str]]:
	"""Return all option lists in one call (titles only, for React)."""
	return {ot: _titles_for_type(ot) for ot in OPTION_TYPES}

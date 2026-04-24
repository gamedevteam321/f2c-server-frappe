# -*- coding: utf-8 -*-
"""User document hooks for F2C access fields."""

from __future__ import annotations

from f2c.access.constants import FULL_ACCESS_USERS


def validate(doc, method=None):
	"""
	F2C scope roles may be combined on User; geo rules use the highest role on Employee.

	Allowed Geo Areas validation (farm / cluster / field levels) runs on Employee save via
	``f2c.access.geo_scope_validate`` using rank: Project Manager > Farm Manager >
	Cluster Supervisor / Driver > Field Supervisor.
	"""
	if (doc.name or "").strip() in FULL_ACCESS_USERS:
		return

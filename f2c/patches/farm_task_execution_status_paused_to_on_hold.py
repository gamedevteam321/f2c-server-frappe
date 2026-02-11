# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Migrate Farm Task Execution status from 'Paused' to 'On Hold'."""
	frappe.db.sql(
		"""
		UPDATE `tabFarm Task Execution`
		SET status = 'On Hold'
		WHERE status = 'Paused'
		"""
	)
	frappe.db.commit()

# Copyright (c) 2025, F2C and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Migrate Farm Task Execution status from 'Started' to 'Ready'."""
	frappe.db.sql(
		"""
		UPDATE `tabFarm Task Execution`
		SET status = 'Ready'
		WHERE status = 'Started'
		"""
	)
	frappe.db.commit()

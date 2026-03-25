# -*- coding: utf-8 -*-
"""
Backfill report_module for existing Farm Report Ticket records.

Sets report_module from refs:
- execution_ref -> Execution
- schedule_ref -> Scheduling
- on_demand_activity_ref -> On Demand
- else -> Labour

Safe to run multiple times. Only updates rows where report_module is null or empty.
"""
import frappe


def execute():
	if not frappe.db.table_exists("Farm Report Ticket"):
		return
	if not frappe.db.has_column("Farm Report Ticket", "report_module"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabFarm Report Ticket`
		SET report_module = CASE
			WHEN execution_ref IS NOT NULL AND TRIM(COALESCE(execution_ref, '')) != '' THEN 'Execution'
			WHEN schedule_ref IS NOT NULL AND TRIM(COALESCE(schedule_ref, '')) != '' THEN 'Scheduling'
			WHEN on_demand_activity_ref IS NOT NULL AND TRIM(COALESCE(on_demand_activity_ref, '')) != '' THEN 'On Demand'
			ELSE 'Labour'
		END
		WHERE report_module IS NULL OR TRIM(COALESCE(report_module, '')) = ''
		"""
	)
	frappe.db.commit()

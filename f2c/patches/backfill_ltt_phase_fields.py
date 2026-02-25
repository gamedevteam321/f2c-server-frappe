# -*- coding: utf-8 -*-
"""
Backfill pickup_phase and drop_off_phase for existing Logistics Transfer Ticket records.

Maps current status to phases:
- Pending Pickup -> pickup_phase = Upcoming, drop_off_phase = NULL
- In Transit -> pickup_phase = Picked Up, drop_off_phase = In Transit (so Deliver still works)
- Received -> pickup_phase = Picked Up, drop_off_phase = Delivered
- Reported / Cancelled -> pickup_phase = Picked Up, drop_off_phase = NULL (terminal state)

Safe to run multiple times. Only updates rows where pickup_phase is null.
"""
import frappe


def execute():
	if not frappe.db.table_exists("Logistics Transfer Ticket"):
		return
	if not frappe.db.has_column("Logistics Transfer Ticket", "pickup_phase"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabLogistics Transfer Ticket`
		SET
			pickup_phase = CASE
				WHEN status = 'Pending Pickup' THEN 'Upcoming'
				WHEN status IN ('In Transit', 'Received', 'Reported', 'Cancelled') THEN 'Picked Up'
				ELSE 'Upcoming'
			END,
			drop_off_phase = CASE
				WHEN status = 'In Transit' THEN 'In Transit'
				WHEN status = 'Received' THEN 'Delivered'
				ELSE NULL
			END
		WHERE pickup_phase IS NULL OR TRIM(COALESCE(pickup_phase, '')) = ''
		"""
	)
	frappe.db.commit()

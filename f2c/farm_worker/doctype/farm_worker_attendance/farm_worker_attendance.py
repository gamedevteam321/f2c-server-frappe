# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FarmWorkerAttendance(Document):
	def validate(self):
		# Ensure unique combination of farm_worker and attendance_date
		existing_attendance = frappe.db.exists(
			"Farm Worker Attendance",
			{
				"farm_worker": self.farm_worker,
				"attendance_date": self.attendance_date,
				"name": ["!=", self.name]
			}
		)
		
		if existing_attendance:
			worker_name = self.worker_name or self.farm_worker
			frappe.throw(
				f"Attendance for worker {worker_name} on {self.attendance_date} already exists.",
				title="Duplicate Attendance"
			)


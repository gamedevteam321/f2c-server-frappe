# Script to create dummy Farm Task Execution Day records for testing day-wise view/update.
# Run from bench root:
#   bench --site <your-site> execute f2c.scripts.create_dummy_execution_days.run
# Or for a specific execution:
#   bench --site <your-site> execute f2c.scripts.create_dummy_execution_days.run --args '["FTE-0664"]'

import frappe
from f2c.farm_execution.doctype.farm_task_execution.farm_task_execution import (
	create_dummy_execution_days_for_testing,
)


def run(execution_name=None):
	"""Create dummy execution days. execution_name optional (uses first In Progress if None)."""
	created = create_dummy_execution_days_for_testing(execution_name=execution_name)
	print(f"Created/used {len(created)} day(s): {created}")
	return created

"""
One-time patch: Ensure Asset naming series is set to AST-.YYYY.- (overrides ACC-ASS or any other).

Useful when asset_naming_series patch was already executed with different constants
or when another app (e.g. India Compliance) set a different series. Runs the same
logic as asset_naming_series so all Property Setters for Asset.naming_series.options
are set to AST-.YYYY.-.
"""
from f2c.patches.asset_naming_series import execute as _execute


def execute():
	_execute()

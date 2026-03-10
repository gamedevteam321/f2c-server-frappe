"""
Patch: Set Asset doctype naming series options so that new Assets use the desired series.

Runs on migrate (post_model_sync). Idempotent: safe to run multiple times.

To change the naming series, edit NEW_ASSET_NAMING_SERIES below. Use a single series
(e.g. "F2C-AST-.YYYY.-") or multiple lines separated by newline for a dropdown.
Frappe will create the series counter automatically when the first Asset with that
series is created.

Also updates Item.asset_naming_series from the old default to the new series so that
equipment/Asset creation from Item uses the new series.
"""
import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

# Old series (e.g. produced names like ACC-ASS-2026-00012). Items with this are updated to new.
OLD_ASSET_NAMING_SERIES = "ACC-ASS-.YYYY.-"

# New Asset naming series (produces names like AST-2026-00001).
NEW_ASSET_NAMING_SERIES = "AST-.YYYY.-"

PROPERTY_SETTER_DOCTYPE = "Asset"
PROPERTY_SETTER_FIELD = "naming_series"
PROPERTY_SETTER_PROPERTY = "options"


def execute():
	if not frappe.db.exists("DocType", "Asset"):
		return
	if frappe.db.table_exists("Property Setter"):
		_set_asset_naming_series_options()
	_update_docfield_naming_series_options()
	_update_item_asset_naming_series()
	frappe.clear_cache(doctype="Asset")
	frappe.clear_cache(doctype="DocType")


def _set_asset_naming_series_options():
	# Find all Property Setters for Asset.naming_series.options (e.g. from different apps).
	# Update every one so our value wins regardless of merge order.
	existing = frappe.get_all(
		"Property Setter",
		filters={
			"doc_type": PROPERTY_SETTER_DOCTYPE,
			"field_name": PROPERTY_SETTER_FIELD,
			"property": PROPERTY_SETTER_PROPERTY,
		},
		fields=["name", "value"],
	)

	if existing:
		all_already_correct = all(
			row["value"] == NEW_ASSET_NAMING_SERIES for row in existing
		)
		if all_already_correct:
			return
		for row in existing:
			frappe.db.set_value(
				"Property Setter",
				row["name"],
				"value",
				NEW_ASSET_NAMING_SERIES,
			)
		frappe.db.commit()
	else:
		make_property_setter(
			doctype=PROPERTY_SETTER_DOCTYPE,
			fieldname=PROPERTY_SETTER_FIELD,
			property=PROPERTY_SETTER_PROPERTY,
			value=NEW_ASSET_NAMING_SERIES,
			property_type="Text",
			validate_fields_for_doctype=False,
		)
		frappe.db.commit()


def _update_docfield_naming_series_options():
	"""Update the DocField options for Asset.naming_series so the Options field shows AST-.YYYY.-."""
	updated = frappe.db.sql(
		"""
		UPDATE tabDocField
		SET options = %(new_options)s
		WHERE parent = %(doctype)s AND fieldname = %(fieldname)s AND (options != %(new_options)s OR options IS NULL)
		""",
		{
			"doctype": PROPERTY_SETTER_DOCTYPE,
			"fieldname": PROPERTY_SETTER_FIELD,
			"new_options": NEW_ASSET_NAMING_SERIES,
		},
	)
	frappe.db.commit()


def _update_item_asset_naming_series():
	"""Update Item.asset_naming_series from old default to new series where applicable."""
	if not frappe.db.has_column("Item", "asset_naming_series"):
		return
	frappe.db.sql(
		"""
		UPDATE tabItem
		SET asset_naming_series = %(new_series)s
		WHERE asset_naming_series = %(old_series)s
		""",
		{"new_series": NEW_ASSET_NAMING_SERIES, "old_series": OLD_ASSET_NAMING_SERIES},
	)
	frappe.db.commit()

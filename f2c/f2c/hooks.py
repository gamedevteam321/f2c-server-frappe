app_name = "f2c"
app_title = "Farm to Crop"
app_publisher = "Orgatek"
app_description = "Farm Management System"
app_email = "symbiotixhost@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "f2c",
# 		"logo": "/assets/f2c/logo.png",
# 		"title": "Farm to Crop",
# 		"route": "/f2c",
# 		"has_permission": "f2c.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/f2c/css/f2c.css"
# app_include_js = "/assets/f2c/js/f2c.js"

# include js, css files in header of web template
# web_include_css = "/assets/f2c/css/f2c.css"
# web_include_js = "/assets/f2c/js/f2c.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "f2c/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# Inject small desk customizations for specific doctypes
doctype_js = {
	"Location": "public/js/location_geo_fencing_sync.js",
}
# List-view customization for doctypes
doctype_list_js = {
	"Location": "public/js/location_list_geo_fencing_sync.js",
	"Asset": "public/js/asset_list_override.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "f2c/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "f2c.utils.jinja_methods",
# 	"filters": "f2c.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "f2c.install.before_install"
# after_install = "f2c.install.after_install"

# Seed required master data after installs/migrations
after_install = "f2c.seed_defaults.after_install"
after_migrate = "f2c.seed_defaults.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "f2c.uninstall.before_uninstall"
# after_uninstall = "f2c.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "f2c.utils.before_app_install"
# after_app_install = "f2c.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "f2c.utils.before_app_uninstall"
# after_app_uninstall = "f2c.utils.after_app_uninstall"

# File storage: optional Local or S3 (configure in File Storage Settings or site config)
# ------------------
write_file = ["f2c.file_storage.write_file"]
delete_file_data_content = ["f2c.file_storage.delete_file_data_content"]

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "f2c.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Asset Movement": "f2c.inventory.overrides.asset_movement.F2CAssetMovement",
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	# Stock quantity changes
	"Stock Entry": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	"Purchase Receipt": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	"Delivery Note": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	"Stock Reconciliation": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	"Subcontracting Receipt": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	"Asset Capitalization": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	# Asset location changes
	"Asset Movement": {
		"on_submit": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
		"on_cancel": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync",
	},
	"Asset": {
		"on_update": "f2c.inventory.warehouse_stock_sync_hooks.trigger_warehouse_stock_sync_on_asset_update",
	},
	"Item": {
		"before_validate": "f2c.inventory.item_hsn_default.set_default_gst_hsn_code_for_fixed_asset",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		# Auto-create Warehouse Stock docs for warehouses that have stock available
		"f2c.inventory.doctype.warehouse_stock.warehouse_stock.sync_warehouse_stock",
	],
	"hourly": [
		# Update today's Weather Report current conditions (do not create new)
		"f2c.weather.scheduler.update_today_weather_reports_hourly",
	],
	"cron": {
		# Fetch weather data for all fields daily at 6 AM
		"0 6 * * *": [
			"f2c.weather.scheduler.fetch_weather_for_all_fields"
		]
	}
}

# Testing
# -------

# before_tests = "f2c.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "f2c.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "f2c.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["f2c.utils.before_request"]
# after_request = ["f2c.utils.after_request"]

# Job Events
# ----------
# before_job = ["f2c.utils.before_job"]
# after_job = ["f2c.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"f2c.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }


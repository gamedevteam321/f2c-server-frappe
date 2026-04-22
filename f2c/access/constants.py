FIELD_SUPERVISOR_ROLE = "Field Supervisor"
CLUSTER_SUPERVISOR_ROLE = "Cluster Supervisor"
DRIVER_ROLE = "Driver"
FARM_MANAGER_ROLE = "Farm Manager"
PROJECT_MANAGER_ROLE = "Project Manager"
# Geo Fencing Type master: geo_fencing_type_name for areas that may be assigned as scope roots.
FIELD_LEVEL_GFA_TYPE_NAME = "Field"
CLUSTER_LEVEL_GFA_TYPE_NAME = "Cluster"
FARM_LEVEL_GFA_TYPE_NAME = "Farm"
# Desk roles that may assign or edit Employee Allowed Geo Areas for users with scoped F2C roles
# (Administrator bypasses via FULL_ACCESS_USERS / user_bypasses_field_supervisor_restrictions).
ROLES_ALLOWED_TO_EDIT_EMPLOYEE_GEO_SCOPE = frozenset({"System Manager"})
# Backward-compatible alias
ROLES_ALLOWED_TO_EDIT_USER_F2C_SCOPE = ROLES_ALLOWED_TO_EDIT_EMPLOYEE_GEO_SCOPE
USER_ASSIGNED_FIELD_FIELDNAME = "f2c_assigned_field"
# Geo scope lives on Employee (attendance_portal custom field + child table).
EMPLOYEE_ALLOWED_GEO_FIELDNAME = "allowed_geo_areas"
EMPLOYEE_ALLOWED_GEO_CHILD_DOCTYPE = "Employee Allowed Geo Area"
EMPLOYEE_ALLOWED_GEO_ROW_FIELDNAME = "geo_fencing_area"
# Legacy User child table / field — removed after migrate patch; kept for patch code only.
LEGACY_USER_SCOPE_AREAS_FIELDNAME = "f2c_scope_areas"
LEGACY_USER_SCOPE_CHILD_DOCTYPE = "User F2C Scope Area"

# Users who never get Field Supervisor data/UI restrictions (full access).
FULL_ACCESS_USERS = frozenset({"Administrator"})

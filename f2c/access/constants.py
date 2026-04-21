FIELD_SUPERVISOR_ROLE = "Field Supervisor"
# Geo Fencing Type master: geo_fencing_type_name for areas that may be assigned as FS scope roots on User.
FIELD_LEVEL_GFA_TYPE_NAME = "Field"
# Desk roles that may assign or edit User F2C scope for Field Supervisors (Administrator bypasses via FULL_ACCESS_USERS).
ROLES_ALLOWED_TO_EDIT_USER_F2C_SCOPE = frozenset({"System Manager"})
USER_ASSIGNED_FIELD_FIELDNAME = "f2c_assigned_field"
# Child table on User (rows: Link Geo Fencing Area) — multiple farms/clusters/fields.
USER_SCOPE_AREAS_FIELDNAME = "f2c_scope_areas"
USER_SCOPE_CHILD_DOCTYPE = "User F2C Scope Area"

# Users who never get Field Supervisor data/UI restrictions (full access).
FULL_ACCESS_USERS = frozenset({"Administrator"})

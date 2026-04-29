from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from f2c.access.field_scope import user_bypasses_field_supervisor_restrictions


class TestFieldScope(FrappeTestCase):
	def test_administrator_role_bypasses_scoped_restrictions(self):
		with patch("f2c.access.field_scope.frappe.get_roles", return_value=["Field Supervisor", "Administrator"]):
			self.assertTrue(user_bypasses_field_supervisor_restrictions("test-admin-role-user@example.com"))

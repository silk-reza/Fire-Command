import unittest

from controllers import access_controller as access
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import AccessDenied, AuthServiceError, authenticate
from utils import permissions as perms
from utils import session

DEMO_PASSWORD = "FireDemo2026!"

OPERATOR_ALLOWED = {
    perms.PERMISSION_OPERATE_INCIDENTS,
    perms.PERMISSION_OPERATE_DISPATCHES,
    perms.PERMISSION_MANAGE_RESOURCES,
    perms.PERMISSION_VIEW_OPERATIONAL_DATA,
    perms.PERMISSION_VIEW_REPORTS,
}
VIEW_ONLY = {perms.PERMISSION_VIEW_OPERATIONAL_DATA, perms.PERMISSION_VIEW_REPORTS}


def start(role_code):
    session.start_session({"user_id": 1, "username": "x", "role_code": role_code})


class AccessBase(unittest.TestCase):
    def setUp(self):
        session.end_session()

    def tearDown(self):
        session.end_session()


class RolePolicyTest(AccessBase):
    def test_admin_all_permitted(self):
        start("ADMINISTRADOR")
        for p in perms.ALL_PERMISSIONS:
            self.assertTrue(access.can(p), p)
            access.require(p)
        self.assertFalse(access.is_read_only())

    def test_operator(self):
        start("OPERADOR")
        for p in perms.ALL_PERMISSIONS:
            self.assertEqual(access.can(p), p in OPERATOR_ALLOWED, p)
        for p in (perms.PERMISSION_MANAGE_USERS, perms.PERMISSION_MANAGE_CATALOGS,
                  perms.PERMISSION_VIEW_AUDIT):
            with self.assertRaises(AuthorizationDenied):
                access.require(p)
        self.assertFalse(access.is_read_only())

    def test_consulta_only_view(self):
        start("CONSULTA")
        for p in perms.ALL_PERMISSIONS:
            self.assertEqual(access.can(p), p in VIEW_ONLY, p)
        for p in perms.WRITE_PERMISSIONS | {perms.PERMISSION_VIEW_AUDIT}:
            with self.assertRaises(AuthorizationDenied):
                access.require(p)
        self.assertTrue(access.is_read_only())

    def test_direct_require(self):
        start("OPERADOR")
        with self.assertRaises(AuthorizationDenied):
            access.require("manage_users")
        start("ADMINISTRADOR")
        access.require("manage_users")
        start("CONSULTA")
        with self.assertRaises(AuthorizationDenied):
            access.require("operate_incidents")
        with self.assertRaises(AuthorizationDenied):
            access.require("operate_dispatches")

    def test_exceptions_are_distinct(self):
        for other in (AccessDenied, AuthServiceError, NotAuthenticated):
            self.assertFalse(issubclass(AuthorizationDenied, other))
        for other in (AccessDenied, AuthServiceError, AuthorizationDenied):
            self.assertFalse(issubclass(NotAuthenticated, other))

    def test_read_only_only_for_consulta(self):
        for role, expected in (("ADMINISTRADOR", False), ("OPERADOR", False), ("CONSULTA", True)):
            start(role)
            self.assertEqual(access.is_read_only(), expected, role)

    def test_get_permissions_returns_copy(self):
        start("CONSULTA")
        got = access.get_permissions()
        self.assertEqual(got, frozenset(VIEW_ONLY))
        self.assertIsInstance(got, frozenset)

    def test_policy_subsets_of_constants(self):
        self.assertEqual(set(perms.ROLE_PERMISSIONS),
                         {"ADMINISTRADOR", "OPERADOR", "CONSULTA"})
        for role, granted in perms.ROLE_PERMISSIONS.items():
            self.assertTrue(granted <= perms.ALL_PERMISSIONS, role)
        self.assertTrue(perms.WRITE_PERMISSIONS <= perms.ALL_PERMISSIONS)
        self.assertFalse(perms.WRITE_PERMISSIONS & VIEW_ONLY)


class FailSafeTest(AccessBase):
    def test_no_session(self):
        for p in perms.ALL_PERMISSIONS:
            self.assertFalse(access.can(p))
            with self.assertRaises(NotAuthenticated):
                access.require(p)
        self.assertEqual(access.get_permissions(), frozenset())
        self.assertTrue(access.is_read_only())

    def test_unknown_role(self):
        for role in ("SUPERUSUARIO", "", None, "administrador", 5):
            start(role)
            self.assertEqual(access.get_permissions(), frozenset(), repr(role))
            for p in perms.ALL_PERMISSIONS:
                self.assertFalse(access.can(p), (role, p))
            self.assertTrue(access.is_read_only())

    def test_missing_role_code_key(self):
        session.start_session({"user_id": 1, "username": "x"})
        self.assertFalse(access.can(perms.PERMISSION_VIEW_REPORTS))
        with self.assertRaises(NotAuthenticated):
            access.require(perms.PERMISSION_VIEW_REPORTS)

    def test_unknown_permission_denied_even_for_admin(self):
        start("ADMINISTRADOR")
        for p in ("delete_everything", "", None, 123):
            self.assertFalse(access.can(p), repr(p))
            with self.assertRaises(AuthorizationDenied):
                access.require(p)


class DemoUsersIntegrationTest(AccessBase):
    def login(self, username):
        session.start_session(authenticate(username, DEMO_PASSWORD))

    def test_admin_demo(self):
        self.login("admin.demo")
        for p in perms.ALL_PERMISSIONS:
            self.assertTrue(access.can(p), p)
        access.require("manage_users")
        self.assertFalse(access.is_read_only())

    def test_operador_central(self):
        self.login("operador.central")
        access.require("operate_incidents")
        access.require("view_reports")
        for p in ("manage_users", "manage_catalogs", "view_audit"):
            self.assertFalse(access.can(p), p)
            with self.assertRaises(AuthorizationDenied):
                access.require(p)

    def test_consulta_sur(self):
        self.login("consulta.sur")
        access.require("view_operational_data")
        access.require("view_reports")
        for p in perms.WRITE_PERMISSIONS | {"view_audit"}:
            with self.assertRaises(AuthorizationDenied):
                access.require(p)
        self.assertTrue(access.is_read_only())


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest import mock
from uuid import uuid4

import bcrypt

from config.database import get_connection
from controllers import user_controller as uc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import AccessDenied, authenticate
from models import user_model
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
PASSWORD = "Clave.Temp2026!"


def mysql_error():
    import mysql.connector
    return mysql.connector.Error("fallo simulado")


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


def _db_one(query, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        cursor.close()
        return row
    finally:
        connection.close()


def _cleanup(user_ids):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        for user_id in user_ids:
            cursor.execute("DELETE FROM usuarios WHERE id_usuario = %s", (user_id,))
        connection.commit()
        cursor.close()
    finally:
        connection.close()


class UserControllerTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.created = []
        _login("admin.demo")
        self.roles = uc.list_roles()
        self.role_id = self.roles[0]["role_id"]
        self.admin_role_id = self._role_id("ADMINISTRADOR")
        self.viewer_role_id = self._role_id("CONSULTA")

    def tearDown(self):
        session.end_session()
        # Garantiza que admin.demo termine activo y administrador pase lo que pase
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE usuarios SET activo = 1, id_rol = %s WHERE nombre_usuario = 'admin.demo'",
                (self.admin_role_id,),
            )
            connection.commit()
            cursor.close()
        finally:
            connection.close()
        _cleanup(self.created)

    def _role_id(self, code):
        return next(r["role_id"] for r in self.roles if r["role_code"] == code)

    def _admin_demo(self):
        return _db_one(
            "SELECT id_usuario, activo, id_rol FROM usuarios WHERE nombre_usuario = 'admin.demo'"
        )

    def _make_inactive_admin_session(self):
        """Crea un admin temporal, lo desactiva y abre sesión como él sin autenticar."""
        temp = self._create(role_id=self.admin_role_id)
        uc.deactivate_user(temp["user_id"])
        session.start_session({
            "user_id": temp["user_id"], "username": temp["username"],
            "role_code": "ADMINISTRADOR", "first_name": "x", "last_name": "y", "active": True,
        })
        return temp

    def _data(self, **overrides):
        tag = uuid4().hex[:8]
        data = {
            "username": f"test.obj04.{tag}",
            "first_name": "Prueba",
            "last_name": "Temporal",
            "email": f"test.obj04.{tag}@example.com",
            "role_id": self.role_id,
            "password": PASSWORD,
        }
        data.update(overrides)
        return data

    def _create(self, **overrides):
        user = uc.create_user(self._data(**overrides))
        self.created.append(user["user_id"])
        return user

    def _hash(self, user_id):
        return _db_one("SELECT password_hash FROM usuarios WHERE id_usuario=%s", (user_id,))[0]

    def _count(self, username):
        return _db_one("SELECT COUNT(*) FROM usuarios WHERE nombre_usuario=%s", (username,))[0]

    def _count_prefix(self):
        return _db_one(
            "SELECT COUNT(*) FROM usuarios WHERE nombre_usuario LIKE 'test.obj04.%'"
        )[0]

    def test_list_contains_seed_and_no_hash(self):
        users = uc.list_users()
        names = {u["username"] for u in users}
        self.assertIn("admin.demo", names)
        self.assertIn("operador.inactivo", names)
        for key in ("user_id", "username", "first_name", "last_name", "email",
                    "role_id", "role_code", "role_name", "active"):
            self.assertIn(key, users[0])
        for user in users:
            self.assertNotIn("password_hash", user)
            self.assertIsInstance(user["active"], bool)

    def test_search_partial_and_wildcards(self):
        user = self._create(first_name="Zuleimar", last_name="Quintanilla")
        for term in (user["username"][-8:], "uleima", "intanil", "Zuleimar Quin"):
            ids = [u["user_id"] for u in uc.search_users(term)]
            self.assertIn(user["user_id"], ids, term)
        total = len(uc.list_users())
        self.assertLess(len(uc.search_users("%")), total)
        self.assertLess(len(uc.search_users("_")), total)
        self.assertEqual(uc.search_users("\\"), [])
        self.assertEqual(len(uc.search_users("  ")), total)

    def test_create_valid(self):
        user = self._create()
        self.assertIn(user["user_id"], [u["user_id"] for u in uc.list_users()])
        self.assertEqual(user["role_id"], self.role_id)
        self.assertTrue(user["active"])
        self.assertNotIn("password_hash", user)
        stored = self._hash(user["user_id"])
        self.assertTrue(stored.startswith("$2"))
        self.assertTrue(bcrypt.checkpw(PASSWORD.encode(), stored.encode()))
        self.assertNotEqual(stored, PASSWORD)

    def test_empty_email_stored_as_null_twice(self):
        a = self._create(email="  ")
        b = self._create(email=None)
        self.assertIsNone(a["email"])
        self.assertIsNone(b["email"])

    def test_duplicate_username(self):
        user = self._create()
        with self.assertRaises(uc.DuplicateUserError):
            uc.create_user(self._data(username=user["username"]))
        self.assertEqual(self._count(user["username"]), 1)

    def test_duplicate_email(self):
        user = self._create()
        data = self._data(email=user["email"])
        with self.assertRaises(uc.DuplicateUserError):
            uc.create_user(data)
        self.assertEqual(self._count(data["username"]), 0)

    def test_update_persists_and_keeps_hash(self):
        user = self._create()
        before = self._hash(user["user_id"])
        data = self._data(username=user["username"], first_name="Cambiado",
                          last_name="Nuevo", email=user["email"], password="")
        updated = uc.update_user(user["user_id"], data)
        self.assertEqual(updated["first_name"], "Cambiado")
        self.assertEqual(uc.get_user(user["user_id"])["last_name"], "Nuevo")
        self.assertEqual(self._hash(user["user_id"]), before)
        # Sin cambios reales no debe confundirse con usuario inexistente
        uc.update_user(user["user_id"], data)

    def test_update_new_password(self):
        user = self._create()
        before = self._hash(user["user_id"])
        data = self._data(username=user["username"], email=user["email"],
                          password="OtraClave2026!")
        uc.update_user(user["user_id"], data)
        self.assertNotEqual(self._hash(user["user_id"]), before)
        self.assertEqual(authenticate(user["username"], "OtraClave2026!")["username"],
                         user["username"])
        with self.assertRaises(AccessDenied):
            authenticate(user["username"], PASSWORD)

    def test_update_duplicate_username(self):
        a = self._create()
        b = self._create()
        with self.assertRaises(uc.DuplicateUserError):
            uc.update_user(b["user_id"], self._data(username=a["username"], email=b["email"]))

    def test_deactivate_and_reactivate(self):
        user = self._create()
        self.assertEqual(authenticate(user["username"], PASSWORD)["user_id"], user["user_id"])
        self.assertFalse(uc.deactivate_user(user["user_id"])["active"])
        with self.assertRaises(AccessDenied):
            authenticate(user["username"], PASSWORD)
        self.assertTrue(uc.activate_user(user["user_id"])["active"])
        self.assertEqual(authenticate(user["username"], PASSWORD)["user_id"], user["user_id"])
        uc.activate_user(user["user_id"])

    def test_role_validation(self):
        before = self._count_prefix()
        for bad in (None, "", 999999, 0, -1, "abc", True):
            with self.assertRaises(uc.UserValidationError, msg=repr(bad)):
                uc.create_user(self._data(role_id=bad))
        self.assertEqual(self._count_prefix(), before)

    def test_role_by_string_digit_accepted(self):
        user = self._create(role_id=str(self.role_id))
        self.assertEqual(user["role_id"], self.role_id)

    def test_inactive_role_rejected(self):
        row = _db_one("SELECT id_rol FROM roles WHERE activo = 0 LIMIT 1")
        if row is None:
            self.skipTest("No hay roles inactivos en la base; no se modifican roles.")
        with self.assertRaises(uc.UserValidationError):
            uc.create_user(self._data(role_id=row[0]))

    def test_field_validation(self):
        before = self._count_prefix()
        cases = [
            {"email": "no-es-correo"},
            {"email": "a@b"},
            {"email": "a" * 120 + "@x.com"},
            {"username": "   "},
            {"username": None},
            {"first_name": ""},
            {"last_name": "  "},
            {"username": "u" * 51},
            {"first_name": "n" * 81},
            {"last_name": "a" * 81},
            {"password": ""},
            {"password": None},
            {"password": "x" * 73},
            {"password": "ñ" * 37},
        ]
        for overrides in cases:
            with self.assertRaises(uc.UserValidationError, msg=str(overrides)[:60]):
                uc.create_user(self._data(**overrides))
        self.assertEqual(self._count_prefix(), before)

    def test_password_72_bytes_ok(self):
        user = self._create(password="x" * 72)
        self.assertTrue(user["user_id"])

    def test_update_password_too_long(self):
        user = self._create()
        with self.assertRaises(uc.UserValidationError):
            uc.update_user(user["user_id"], self._data(
                username=user["username"], email=user["email"], password="x" * 73))

    def test_not_found(self):
        with self.assertRaises(uc.UserNotFoundError):
            uc.get_user(99999999)
        with self.assertRaises(uc.UserNotFoundError):
            uc.update_user(99999999, self._data())
        with self.assertRaises(uc.UserNotFoundError):
            uc.activate_user(99999999)
        with self.assertRaises(uc.UserNotFoundError):
            uc.deactivate_user(99999999)

    def _all_calls(self):
        data = self._data()
        return [
            lambda: uc.list_users(),
            lambda: uc.search_users("admin"),
            lambda: uc.get_user(1),
            lambda: uc.create_user(data),
            lambda: uc.update_user(1, data),
            lambda: uc.activate_user(1),
            lambda: uc.deactivate_user(1),
            lambda: uc.list_roles(),
        ], data

    def test_denied_roles(self):
        for username in ("operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            calls, data = self._all_calls()
            for call in calls:
                with self.assertRaises(AuthorizationDenied, msg=username):
                    call()
            self.assertEqual(self._count(data["username"]), 0)

    def test_denied_deactivate_leaves_active(self):
        user = self._create()
        _login("operador.central")
        with self.assertRaises(AuthorizationDenied):
            uc.deactivate_user(user["user_id"])
        row = _db_one("SELECT activo FROM usuarios WHERE id_usuario=%s", (user["user_id"],))
        self.assertEqual(row[0], 1)

    def test_no_session(self):
        session.end_session()
        calls, data = self._all_calls()
        for call in calls:
            with self.assertRaises(NotAuthenticated):
                call()
        self.assertEqual(self._count(data["username"]), 0)

    def test_self_deactivation_rejected(self):
        temp = self._create(role_id=self.admin_role_id)
        session.end_session()
        session.start_session(authenticate(temp["username"], PASSWORD))
        with self.assertRaises(uc.SelfDeactivationError) as ctx:
            uc.deactivate_user(temp["user_id"])
        self.assertIsInstance(ctx.exception, uc.UserValidationError)
        self.assertTrue(uc.get_user(temp["user_id"])["active"])

    def test_deactivate_other_admin_allowed_and_reactivate(self):
        temp = self._create(role_id=self.admin_role_id)
        self.assertFalse(uc.deactivate_user(temp["user_id"])["active"])
        self.assertTrue(uc.activate_user(temp["user_id"])["active"])

    def test_degrade_non_last_admin_allowed(self):
        temp = self._create(role_id=self.admin_role_id)
        data = self._data(username=temp["username"], email=temp["email"],
                          role_id=self.viewer_role_id, password="")
        self.assertEqual(uc.update_user(temp["user_id"], data)["role_code"], "CONSULTA")

    def test_edit_admin_keeping_role_allowed(self):
        temp = self._create(role_id=self.admin_role_id)
        data = self._data(username=temp["username"], email=temp["email"],
                          first_name="Editado", role_id=self.admin_role_id)
        self.assertEqual(uc.update_user(temp["user_id"], data)["first_name"], "Editado")

    def test_inactive_admin_change_role_allowed(self):
        temp = self._create(role_id=self.admin_role_id)
        uc.deactivate_user(temp["user_id"])
        data = self._data(username=temp["username"], email=temp["email"],
                          role_id=self.viewer_role_id)
        self.assertEqual(uc.update_user(temp["user_id"], data)["role_code"], "CONSULTA")

    def _require_demo_sole_admin(self):
        count = _db_one(
            "SELECT COUNT(*) FROM usuarios u JOIN roles r ON r.id_rol = u.id_rol "
            "WHERE r.codigo = 'ADMINISTRADOR' AND u.activo = 1"
        )[0]
        if count != 1:
            self.skipTest("admin.demo no es el unico administrador activo.")

    def test_last_admin_deactivation_rejected_real_db(self):
        self._make_inactive_admin_session()
        self._require_demo_sole_admin()
        before = self._admin_demo()
        with self.assertRaises(uc.LastAdministratorError) as ctx:
            uc.deactivate_user(before[0])
        self.assertNotIsInstance(ctx.exception, uc.SelfDeactivationError)
        self.assertEqual(self._admin_demo(), before)

    def test_last_admin_role_change_rejected_real_db(self):
        self._make_inactive_admin_session()
        self._require_demo_sole_admin()
        before = self._admin_demo()
        data = self._data(username="admin.demo", email=None, role_id=self.viewer_role_id)
        with self.assertRaises(uc.LastAdministratorError):
            uc.update_user(before[0], data)
        self.assertEqual(self._admin_demo(), before)

    def test_last_admin_keep_role_allowed_real_db(self):
        self._make_inactive_admin_session()
        self._require_demo_sole_admin()
        row = _db_one(
            "SELECT nombres, apellidos, email FROM usuarios WHERE nombre_usuario='admin.demo'"
        )
        before = self._admin_demo()
        data = {"username": "admin.demo", "first_name": row[0], "last_name": row[1],
                "email": row[2], "role_id": self.admin_role_id, "password": ""}
        uc.update_user(before[0], data)
        self.assertEqual(self._admin_demo(), before)

    def test_controller_maps_model_last_admin_error(self):
        temp = self._create(role_id=self.admin_role_id)
        error = user_model.LastAdministratorError("x")
        with mock.patch.object(user_model, "set_user_active", side_effect=error):
            with self.assertRaises(uc.LastAdministratorError):
                uc.deactivate_user(temp["user_id"])
        with mock.patch.object(user_model, "update_user", side_effect=error):
            with self.assertRaises(uc.LastAdministratorError):
                uc.update_user(temp["user_id"], self._data(
                    username=temp["username"], email=temp["email"]))

    def _login_temp_admin(self):
        temp = self._create(role_id=self.admin_role_id)
        session.end_session()
        session.start_session(authenticate(temp["username"], PASSWORD))
        return temp

    def _own_data(self, temp, **overrides):
        return self._data(username=temp["username"], email=temp["email"],
                          role_id=self.admin_role_id, password="", **overrides)

    def test_self_demotion_ends_session(self):
        for code in ("OPERADOR", "CONSULTA"):
            session.end_session()
            _login("admin.demo")
            temp = self._login_temp_admin()
            data = self._own_data(temp, first_name="Propio")
            data["role_id"] = self._role_id(code)
            updated = uc.update_user(temp["user_id"], data)
            self.assertEqual(updated["role_code"], code)
            self.assertIsNone(session.get_session())
            self.assertFalse(session.is_active())
            row = _db_one(
                "SELECT r.codigo FROM usuarios u JOIN roles r ON r.id_rol = u.id_rol "
                "WHERE u.id_usuario = %s", (temp["user_id"],))
            self.assertEqual(row[0], code)
            for call in (lambda: uc.list_users(),
                         lambda: uc.create_user(self._data()),
                         lambda: uc.update_user(temp["user_id"], data)):
                with self.assertRaises(NotAuthenticated):
                    call()
            session.start_session(authenticate(temp["username"], PASSWORD))
            self.assertEqual(session.get_session()["role_code"], code)
            with self.assertRaises(AuthorizationDenied):
                uc.list_users()
            with self.assertRaises(AuthorizationDenied):
                uc.create_user(self._data())

    def test_own_edit_same_role_keeps_session(self):
        temp = self._login_temp_admin()
        data = self._own_data(temp, first_name="Nuevo")
        uc.update_user(temp["user_id"], data)
        self.assertTrue(session.is_active())
        self.assertEqual(session.get_session()["role_code"], "ADMINISTRADOR")

    def test_editing_other_user_role_keeps_session(self):
        temp = self._login_temp_admin()
        other = self._create(role_id=self._role_id("OPERADOR"))
        data = self._data(username=other["username"], email=other["email"],
                          role_id=self.viewer_role_id)
        self.assertEqual(uc.update_user(other["user_id"], data)["role_code"], "CONSULTA")
        self.assertTrue(session.is_active())
        self.assertEqual(session.get_session()["user_id"], temp["user_id"])

    def test_failed_self_updates_keep_session(self):
        temp = self._login_temp_admin()
        other = self._create()
        operator_id = self._role_id("OPERADOR")
        # Rol inválido
        data = self._own_data(temp)
        data["role_id"] = 999999
        with self.assertRaises(uc.UserValidationError):
            uc.update_user(temp["user_id"], data)
        self.assertTrue(session.is_active())
        # Usuario duplicado junto con cambio de rol
        data = self._own_data(temp)
        data["username"] = other["username"]
        data["role_id"] = operator_id
        with self.assertRaises(uc.DuplicateUserError):
            uc.update_user(temp["user_id"], data)
        self.assertTrue(session.is_active())
        # Errores del model simulados
        data = self._own_data(temp)
        data["role_id"] = operator_id
        for error in (user_model.LastAdministratorError("x"),
                      mysql_error()):
            with mock.patch.object(user_model, "update_user", side_effect=error):
                with self.assertRaises((uc.LastAdministratorError, uc.UserServiceError)):
                    uc.update_user(temp["user_id"], data)
            self.assertTrue(session.is_active())
        self.assertEqual(uc.get_user(temp["user_id"])["role_code"], "ADMINISTRADOR")

    def test_last_admin_rejection_by_other_session_keeps_session_real_db(self):
        # Rechazo real por último admin: la sesión del que llama no cambia
        temp = self._make_inactive_admin_session()
        self._require_demo_sole_admin()
        data = self._data(username="admin.demo", email=None, role_id=self.viewer_role_id)
        with self.assertRaises(uc.LastAdministratorError):
            uc.update_user(self._admin_demo()[0], data)
        self.assertEqual(session.get_session()["user_id"], temp["user_id"])

    def test_self_rule_checked_before_model(self):
        with mock.patch.object(user_model, "set_user_active") as patched:
            uid = session.get_session()["user_id"]
            with self.assertRaises(uc.SelfDeactivationError):
                uc.deactivate_user(uid)
            patched.assert_not_called()


if __name__ == "__main__":
    unittest.main()

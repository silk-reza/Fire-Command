import unittest
from uuid import uuid4

from config.database import get_connection
from models import user_model


class UserModelTest(unittest.TestCase):
    def setUp(self):
        self.created = []

    def tearDown(self):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            for user_id in self.created:
                cursor.execute("DELETE FROM usuarios WHERE id_usuario = %s", (user_id,))
            connection.commit()
            cursor.close()
        finally:
            connection.close()

    def _create(self, tag=None, email="auto"):
        tag = tag or uuid4().hex[:8]
        role_id = user_model.list_active_roles()[0]["role_id"]
        email = f"test.obj04.{tag}@example.com" if email == "auto" else email
        user_id = user_model.create_user(
            f"test.obj04.{tag}", "Modelo", "Prueba", email, role_id, "$2b$12$" + "x" * 53
        )
        self.created.append(user_id)
        return user_id

    def test_get_has_no_hash_and_keys(self):
        user = user_model.get_user(self._create())
        self.assertNotIn("password_hash", user)
        for key in ("user_id", "username", "first_name", "last_name", "email",
                    "active", "role_id", "role_code", "role_name"):
            self.assertIn(key, user)

    def test_duplicates_translated(self):
        tag = uuid4().hex[:8]
        self._create(tag)
        with self.assertRaises(user_model.DuplicateUsernameError):
            self._create(tag, email=None)
        with self.assertRaises(user_model.DuplicateEmailError):
            user_model.create_user(
                f"test.obj04.{uuid4().hex[:8]}", "A", "B", f"test.obj04.{tag}@example.com",
                user_model.list_active_roles()[0]["role_id"], "h",
            )

    def _create_named(self, username, email=None, password_hash="h"):
        role_id = user_model.list_active_roles()[0]["role_id"]
        user_id = user_model.create_user(
            username, "Modelo", "Prueba", email, role_id, password_hash
        )
        self.created.append(user_id)
        return user_id

    def _hash(self, user_id):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT password_hash FROM usuarios WHERE id_usuario = %s", (user_id,)
            )
            row = cursor.fetchone()
            cursor.close()
            return row[0]
        finally:
            connection.close()

    def _fields(self, user, **overrides):
        fields = {
            "username": user["username"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "email": user["email"],
            "role_id": user["role_id"],
        }
        fields.update(overrides)
        return fields

    def test_search_escapes_wildcards(self):
        tag = uuid4().hex[:8]
        underscore = self._create_named(f"test.obj04.{tag}a_b")
        self._create_named(f"test.obj04.{tag}axb")
        found = [u["user_id"] for u in user_model.search_users(f"{tag}a_b")]
        self.assertEqual(found, [underscore])
        percent = self._create_named(f"test.obj04.{tag}50%x")
        self._create_named(f"test.obj04.{tag}50yx")
        found = [u["user_id"] for u in user_model.search_users(f"{tag}50%")]
        self.assertEqual(found, [percent])

    def test_username_named_like_constraint(self):
        # El nombre coincide con la clave de email, pero debe seguir siendo usuario duplicado
        self._create_named("uq_usuarios_email")
        with self.assertRaises(user_model.DuplicateUsernameError):
            self._create_named("uq_usuarios_email")

    def test_update_password_hash_handling(self):
        user_id = self._create_named(f"test.obj04.{uuid4().hex[:8]}", password_hash="hash-original")
        user = user_model.get_user(user_id)
        self.assertTrue(user_model.update_user(user_id, self._fields(user, first_name="Otro")))
        self.assertEqual(self._hash(user_id), "hash-original")
        self.assertEqual(user_model.get_user(user_id)["first_name"], "Otro")
        self.assertTrue(user_model.update_user(user_id, self._fields(user), "hash-nuevo"))
        self.assertEqual(self._hash(user_id), "hash-nuevo")

    def test_update_duplicates_leave_first_unchanged(self):
        tag_a, tag_b = uuid4().hex[:8], uuid4().hex[:8]
        a_id = self._create_named(f"test.obj04.{tag_a}", f"test.obj04.{tag_a}@example.com")
        b_id = self._create_named(f"test.obj04.{tag_b}", f"test.obj04.{tag_b}@example.com")
        a_before = user_model.get_user(a_id)
        b = user_model.get_user(b_id)
        with self.assertRaises(user_model.DuplicateUsernameError):
            user_model.update_user(b_id, self._fields(b, username=a_before["username"]), "x")
        with self.assertRaises(user_model.DuplicateEmailError):
            user_model.update_user(b_id, self._fields(b, email=a_before["email"]), "x")
        self.assertEqual(user_model.get_user(a_id), a_before)
        self.assertEqual(user_model.get_user(b_id), b)
        self.assertEqual(self._hash(b_id), "h")

    def test_last_admin_rejected_at_model_level(self):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT u.id_usuario FROM usuarios u "
                "JOIN roles r ON r.id_rol = u.id_rol "
                "WHERE r.codigo = 'ADMINISTRADOR' AND u.activo = 1"
            )
            admins = cursor.fetchall()
            cursor.execute("SELECT id_rol FROM roles WHERE codigo = 'CONSULTA'")
            viewer_id = cursor.fetchone()[0]
            cursor.close()
        finally:
            connection.close()
        if len(admins) != 1:
            self.skipTest("Hay mas de un administrador activo; no se simula el ultimo.")
        admin_id = admins[0][0]
        before = user_model.get_user(admin_id)
        with self.assertRaises(user_model.LastAdministratorError):
            user_model.set_user_active(admin_id, False)
        fields = {k: before[k] for k in ("username", "first_name", "last_name", "email")}
        fields["role_id"] = viewer_id
        with self.assertRaises(user_model.LastAdministratorError):
            user_model.update_user(admin_id, fields)
        self.assertEqual(user_model.get_user(admin_id), before)

    def test_invalid_role(self):
        with self.assertRaises(user_model.InvalidRoleError):
            user_model.create_user(f"test.obj04.{uuid4().hex[:8]}", "A", "B", None, 999999, "h")

    def test_set_active_unknown_and_unchanged(self):
        self.assertFalse(user_model.set_user_active(99999999, True))
        user_id = self._create()
        self.assertTrue(user_model.set_user_active(user_id, True))


if __name__ == "__main__":
    unittest.main()

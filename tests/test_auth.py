import unittest

from controllers.auth_controller import AccessDenied, authenticate

DEMO_PASSWORD = "FireDemo2026!"


class AuthTest(unittest.TestCase):
    def test_caso1_login_exitoso(self):
        result = authenticate("admin.demo", DEMO_PASSWORD)
        for field in ("user_id", "username", "first_name", "last_name", "role_code", "active"):
            self.assertIn(field, result, f"Falta el campo {field} en el resultado")
        self.assertEqual(result["username"], "admin.demo")
        self.assertEqual(result["role_code"], "ADMINISTRADOR")
        self.assertTrue(result["active"])
        self.assertTrue(result["first_name"])
        self.assertTrue(result["last_name"])
        self.assertIsNotNone(result["user_id"])
        self.assertNotIn("password_hash", result)

    def test_caso2_password_incorrecta(self):
        with self.assertRaises(AccessDenied, msg="Debe denegar contraseña incorrecta"):
            authenticate("admin.demo", "ClaveIncorrecta123!")

    def test_caso3_usuario_inactivo(self):
        with self.assertRaises(AccessDenied, msg="Debe denegar usuario inactivo"):
            authenticate("operador.inactivo", DEMO_PASSWORD)

    def test_caso4_usuario_inexistente(self):
        with self.assertRaises(AccessDenied, msg="Debe denegar usuario inexistente"):
            authenticate("usuario.que.no.existe", DEMO_PASSWORD)


if __name__ == "__main__":
    unittest.main()

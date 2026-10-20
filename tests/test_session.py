import unittest

from utils import session


class SessionTests(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.user = {
            "user_id": 1,
            "username": "admin.demo",
            "role_code": "ADMINISTRADOR",
            "password_hash": "secreto",
            "password": "secreto",
        }

    def tearDown(self):
        session.end_session()

    def test_start_stores_required_fields(self):
        session.start_session(self.user)
        data = session.get_session()
        self.assertEqual(data["user_id"], 1)
        self.assertEqual(data["username"], "admin.demo")
        self.assertEqual(data["role_code"], "ADMINISTRADOR")
        self.assertTrue(session.is_active())

    def test_password_data_is_not_stored(self):
        session.start_session(self.user)
        data = session.get_session()
        self.assertNotIn("password_hash", data)
        self.assertNotIn("password", data)

    def test_get_session_none_after_end(self):
        session.start_session(self.user)
        session.end_session()
        self.assertIsNone(session.get_session())
        self.assertFalse(session.is_active())

    def test_get_session_returns_copy(self):
        session.start_session(self.user)
        session.get_session()["role_code"] = "CONSULTA"
        self.assertEqual(session.get_session()["role_code"], "ADMINISTRADOR")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest import mock

import main
from controllers import auth_controller
from controllers.auth_controller import AuthServiceError
from utils import session
from views import login_view
from views.login_view import LoginView
from views.main_view import MainView

PASSWORD = "FireDemo2026!"


class LoginGuiTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()
        session.end_session()

    def make_login(self, username, password):
        view = LoginView()
        self.windows.append(view)
        view.username_entry.insert(0, username)
        view.password_entry.insert(0, password)
        view.update()
        return view

    def test_password_is_masked(self):
        view = self.make_login("", "")
        self.assertEqual(view.password_entry.cget("show"), "*")

    def test_empty_fields(self):
        for username, password in (("", ""), ("admin.demo", ""), ("  ", PASSWORD)):
            view = self.make_login(username, password)
            with mock.patch.object(auth_controller, "authenticate") as auth:
                view.attempt_login()
            auth.assert_not_called()
            self.assertEqual(view.message, login_view.MSG_EMPTY)
            self.assertIsNone(view.result)
            self.assertIsNone(session.get_session())

    def test_wrong_password(self):
        view = self.make_login("admin.demo", "incorrecta")
        view.attempt_login()
        self.assertEqual(view.message, login_view.MSG_DENIED)
        self.assertIsNone(view.result)
        self.assertIsNone(session.get_session())
        self.assertEqual(view.password_entry.get(), "")

    def test_inactive_user(self):
        view = self.make_login("operador.inactivo", PASSWORD)
        view.attempt_login()
        self.assertEqual(view.message, login_view.MSG_DENIED)
        self.assertIsNone(view.result)
        self.assertIsNone(session.get_session())

    def test_login_ok_opens_main_and_logout_returns_to_login(self):
        view = self.make_login("admin.demo", PASSWORD)
        view.attempt_login()
        self.assertIsNotNone(view.result)
        current = session.get_session()
        self.assertEqual(current["username"], "admin.demo")
        self.assertIn("user_id", current)
        self.assertIn("role_code", current)
        self.assertNotIn("password_hash", current)

        main_view = MainView(view.result)
        self.windows.append(main_view)
        main_view.update()
        self.assertIn("(admin.demo)", main_view.user_label.cget("text"))
        self.assertEqual(main_view.role_label.cget("text"), f"Rol: {current['role_code']}")

        main_view.logout_button.invoke()
        self.assertTrue(main_view.logged_out)
        self.assertIsNone(session.get_session())

    def test_technical_error_message(self):
        view = self.make_login("admin.demo", PASSWORD)
        with mock.patch.object(
            auth_controller, "authenticate", side_effect=AuthServiceError("db caída: secreto")
        ), self.assertLogs(login_view.logger, level="ERROR"):
            view.attempt_login()
        self.assertEqual(view.message, login_view.MSG_INTERNAL)
        self.assertNotIn("secreto", view.message)
        self.assertIsNone(session.get_session())

    def test_cancel_terminates_app(self):
        view = self.make_login("admin.demo", PASSWORD)
        view.cancel_button.invoke()
        self.assertIsNone(view.result)
        # Con login cancelado el bucle principal no abre ninguna ventana más
        with mock.patch.object(main, "run_login", return_value=None) as login, \
                mock.patch.object(main, "run_main") as main_window:
            main.run_app()
        login.assert_called_once()
        main_window.assert_not_called()

    def test_app_loop_logout_goes_back_to_login(self):
        user = {"user_id": 1}
        with mock.patch.object(main, "run_login", side_effect=[user, None]) as login, \
                mock.patch.object(main, "run_main", return_value=True) as main_window:
            main.run_app()
        self.assertEqual(login.call_count, 2)
        main_window.assert_called_once_with(user)


if __name__ == "__main__":
    unittest.main()

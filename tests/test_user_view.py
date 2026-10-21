import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import customtkinter as ctk

from config.database import get_connection
from controllers import user_controller
from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.user_controller import UserServiceError
from utils import session
from views import user_view
from views.main_view import MainView
from views.user_view import UserView

DEMO_PASSWORD = "FireDemo2026!"
NEW_PASSWORD = "Clave.Gui2026!"
SOURCE = Path(user_view.__file__).read_text(encoding="utf-8")


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def _db_all(query, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        connection.close()


def _cleanup(prefix):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM usuarios WHERE nombre_usuario LIKE %s", (prefix + "%",))
        connection.commit()
        cursor.close()
    finally:
        connection.close()


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


class UserViewTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.prefix = f"test.obj04.gui.{uuid4().hex[:8]}"
        self.root = ctk.CTk()
        self.view = None
        self.windows = [self.root]

    def tearDown(self):
        for window in self.windows:
            try:
                window.destroy()
            except Exception:
                pass
        session.end_session()
        # Garantiza que admin.demo termine activo y administrador pase lo que pase
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE usuarios SET activo = 1, id_rol = "
                "(SELECT id_rol FROM roles WHERE codigo = 'ADMINISTRADOR') "
                "WHERE nombre_usuario = 'admin.demo'"
            )
            connection.commit()
            cursor.close()
        finally:
            connection.close()
        _cleanup(self.prefix)

    def make_temp_admin(self, suffix="adm"):
        """Crea un administrador temporal con la sesión de admin.demo."""
        _login("admin.demo")
        roles = user_controller.list_roles()
        admin_role = next(r["role_id"] for r in roles if r["role_code"] == "ADMINISTRADOR")
        return user_controller.create_user({
            "username": f"{self.prefix}.{suffix}", "first_name": "Temp", "last_name": "Admin",
            "email": "", "role_id": admin_role, "password": NEW_PASSWORD,
        })

    def demo_row(self):
        return _db_all(
            "SELECT u.activo, r.codigo FROM usuarios u JOIN roles r ON r.id_rol = u.id_rol "
            "WHERE u.nombre_usuario = 'admin.demo'"
        )[0]

    def open_as_inactive_temp_admin(self):
        """Deja a admin.demo como único administrador activo y abre el módulo."""
        temp = self.make_temp_admin("inact")
        user_controller.deactivate_user(temp["user_id"])
        session.start_session({
            "user_id": temp["user_id"], "username": temp["username"],
            "role_code": "ADMINISTRADOR", "first_name": "x", "last_name": "y", "active": True,
        })
        active_admins = _db_all(
            "SELECT COUNT(*) FROM usuarios u JOIN roles r ON r.id_rol = u.id_rol "
            "WHERE r.codigo = 'ADMINISTRADOR' AND u.activo = 1"
        )[0][0]
        if active_admins != 1:
            self.skipTest("admin.demo no es el único administrador activo.")
        self.view = UserView(self.root)
        self.view.update()
        return self.view

    def open_users(self, username="admin.demo"):
        _login(username)
        self.view = UserView(self.root)
        self.view.update()
        return self.view

    def fill(self, view, suffix="a", **overrides):
        values = {
            "username": f"{self.prefix}.{suffix}",
            "first": "Prueba",
            "last": "Gui",
            "email": "",
            "password": NEW_PASSWORD,
        }
        values.update(overrides)
        _set(view.username_entry, values["username"])
        _set(view.first_name_entry, values["first"])
        _set(view.last_name_entry, values["last"])
        _set(view.email_entry, values["email"])
        _set(view.password_entry, values["password"])
        return values["username"]

    def rows(self, view):
        return [view.tree.item(i, "values") for i in view.tree.get_children()]

    def row_for(self, view, username):
        for row in self.rows(view):
            if row[0] == username:
                return row
        return None

    def select(self, view, username):
        for iid in view.tree.get_children():
            if view.tree.item(iid, "values")[0] == username:
                view.tree.selection_set(iid)
                view.update()
                return
        self.fail(f"No aparece {username} en la tabla")

    def create_one(self, view, suffix="a", **overrides):
        username = self.fill(view, suffix, **overrides)
        view.create_button.invoke()
        view.update()
        return username

    def test_lists_seed_users_with_four_columns_without_hash(self):
        view = self.open_users()
        self.assertEqual(
            [view.tree.heading(c, "text") for c in view.tree["columns"]],
            ["Usuario", "Nombre", "Rol", "Estado"],
        )
        rows = self.rows(view)
        self.assertIn("admin.demo", [r[0] for r in rows])
        self.assertTrue(all(len(r) == 4 for r in rows))
        self.assertTrue(all(r[3] in ("Activo", "Inactivo") for r in rows))
        self.assertNotIn("$2b$", str(rows))

    def test_search_filters_and_clear_restores(self):
        view = self.open_users()
        _set(view.search_entry, "admin.demo")
        view.search_button.invoke()
        view.update()
        filtered = self.rows(view)
        self.assertEqual([r[0] for r in filtered], ["admin.demo"])
        view.clear_search_button.invoke()
        view.update()
        self.assertGreater(len(self.rows(view)), len(filtered))
        self.assertIn("consulta.sur", [r[0] for r in self.rows(view)])

    def test_roles_come_from_database(self):
        view = self.open_users()
        db_roles = {r[0] for r in _db_all("SELECT nombre FROM roles WHERE activo = 1")}
        self.assertEqual(set(view._role_ids), db_roles)
        self.assertEqual(set(view.role_combo.cget("values")), db_roles)

    def test_create_user_from_form(self):
        view = self.open_users()
        username = self.create_one(view, email="")
        row = self.row_for(view, username)
        self.assertIsNotNone(row)
        self.assertEqual(row[3], "Activo")
        self.assertIn("creado correctamente", view.message_label.cget("text"))
        self.assertEqual(view.password_entry.get(), "")
        self.assertIsNotNone(authenticate(username, NEW_PASSWORD))
        # Tras crear no queda selección y el alta sigue disponible
        self.assertEqual(view.tree.selection(), ())
        self.assertIsNone(view.selected_id)
        self.assertEqual(str(view.create_button.cget("state")), "normal")
        self.assertEqual(str(view.save_button.cget("state")), "disabled")
        self.select(view, username)
        self.assertEqual(view.username_entry.get(), username)
        self.assertEqual(str(view.save_button.cget("state")), "normal")

    def test_create_with_empty_fields_shows_validation_message(self):
        view = self.open_users()
        before = len(self.rows(view))
        view.create_button.invoke()
        view.update()
        self.assertIn("obligatorio", view.message_label.cget("text"))
        self.assertEqual(len(self.rows(view)), before)

    def test_duplicate_username_shows_message_and_no_second_row(self):
        view = self.open_users()
        username = self.create_one(view)
        view.clear_button.invoke()
        self.fill(view, "a")
        view.create_button.invoke()
        view.update()
        self.assertIn("Ya existe", view.message_label.cget("text"))
        self.assertEqual(sum(1 for r in self.rows(view) if r[0] == username), 1)

    def test_select_loads_form_without_password_and_edit_persists(self):
        view = self.open_users()
        username = self.create_one(view)
        self.select(view, username)
        self.assertEqual(view.username_entry.get(), username)
        self.assertEqual(view.password_entry.get(), "")
        self.assertEqual(str(view.create_button.cget("state")), "disabled")
        self.assertEqual(str(view.save_button.cget("state")), "normal")
        _set(view.first_name_entry, "Editado")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizado correctamente", view.message_label.cget("text"))
        self.assertTrue(self.row_for(view, username)[1].startswith("Editado"))
        stored = _db_all(
            "SELECT nombres FROM usuarios WHERE nombre_usuario = %s", (username,)
        )
        self.assertEqual(stored[0][0], "Editado")
        # Contraseña vacía conserva la actual
        self.assertIsNotNone(authenticate(username, NEW_PASSWORD))

    def test_deactivate_and_reactivate(self):
        view = self.open_users()
        username = self.create_one(view)
        self.select(view, username)
        self.assertEqual(str(view.activate_button.cget("state")), "disabled")
        with mock.patch.object(view, "confirm", return_value=True):
            view.deactivate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, username)[3], "Inactivo")
        self.assertEqual(str(view.deactivate_button.cget("state")), "disabled")
        self.assertEqual(str(view.activate_button.cget("state")), "normal")
        # authenticate puede fallar devolviendo None o lanzando AccessDenied
        try:
            denied = authenticate(username, NEW_PASSWORD) is None
        except Exception:
            denied = True
        self.assertTrue(denied)
        _login("admin.demo")
        view.activate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, username)[3], "Activo")
        self.assertIsNotNone(authenticate(username, NEW_PASSWORD))
        _login("admin.demo")

    def test_deactivate_declined_changes_nothing(self):
        view = self.open_users()
        username = self.create_one(view)
        self.select(view, username)
        with mock.patch.object(view, "confirm", return_value=False):
            view.deactivate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, username)[3], "Activo")

    def test_clear_resets_form_and_selection(self):
        view = self.open_users()
        username = self.create_one(view)
        self.select(view, username)
        view.clear_button.invoke()
        view.update()
        self.assertEqual(view.username_entry.get(), "")
        self.assertEqual(view.tree.selection(), ())
        self.assertIsNone(view.selected_id)
        self.assertEqual(str(view.create_button.cget("state")), "normal")
        self.assertEqual(str(view.save_button.cget("state")), "disabled")

    def test_self_deactivation_message_and_user_stays_active(self):
        temp = self.make_temp_admin()
        session.end_session()
        session.start_session(authenticate(temp["username"], NEW_PASSWORD))
        view = UserView(self.root)
        view.update()
        self.select(view, temp["username"])
        with mock.patch.object(view, "confirm", return_value=True):
            view.deactivate_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "No puede desactivar su propia cuenta.")
        self.assertEqual(self.row_for(view, temp["username"])[3], "Activo")
        stored = _db_all(
            "SELECT activo FROM usuarios WHERE nombre_usuario = %s", (temp["username"],)
        )
        self.assertEqual(stored[0][0], 1)

    def test_last_admin_deactivation_message_via_gui(self):
        view = self.open_as_inactive_temp_admin()
        self.select(view, "admin.demo")
        with mock.patch.object(view, "confirm", return_value=True):
            view.deactivate_button.invoke()
        view.update()
        self.assertEqual(
            view.message_label.cget("text"), "Debe existir al menos un administrador activo."
        )
        self.assertEqual(self.demo_row(), (1, "ADMINISTRADOR"))
        self.assertEqual(self.row_for(view, "admin.demo")[3], "Activo")

    def test_last_admin_role_change_message_via_gui(self):
        view = self.open_as_inactive_temp_admin()
        self.select(view, "admin.demo")
        non_admin = next(name for name in view._role_ids if name != view.role_combo.get())
        view.role_combo.set(non_admin)
        view.save_button.invoke()
        view.update()
        self.assertEqual(
            view.message_label.cget("text"), "Debe existir al menos un administrador activo."
        )
        self.assertEqual(self.demo_row(), (1, "ADMINISTRADOR"))

    def test_deactivate_other_admin_allowed_via_gui(self):
        temp = self.make_temp_admin()
        view = self.open_users()
        self.select(view, temp["username"])
        with mock.patch.object(view, "confirm", return_value=True):
            view.deactivate_button.invoke()
        view.update()
        self.assertIn("desactivado correctamente", view.message_label.cget("text"))
        self.assertEqual(self.row_for(view, temp["username"])[3], "Inactivo")

    def login_temp_admin(self, temp):
        session.end_session()
        user = authenticate(temp["username"], NEW_PASSWORD)
        session.start_session(user)
        return user

    def role_code_of(self, username):
        return _db_all(
            "SELECT r.codigo FROM usuarios u JOIN roles r ON r.id_rol = u.id_rol "
            "WHERE u.nombre_usuario = %s", (username,)
        )[0][0]

    def test_own_role_change_ends_session_and_returns_to_login(self):
        import main
        temp = self.make_temp_admin()
        user = self.login_temp_admin(temp)
        notices = []

        def fake_mainloop(main_view):
            self.windows.append(main_view)
            main_view.update()
            view = main_view.open_users()
            view.notify = notices.append
            view.update()
            self.select(view, temp["username"])
            view.role_combo.set("Operador")
            view.save_button.invoke()
            view.update()

        with mock.patch.object(MainView, "mainloop", fake_mainloop):
            # quit() sin mainloop real es inofensivo; run_main devuelve logged_out
            result = main.run_main(user)
        self.assertTrue(result)
        self.assertEqual(self.role_code_of(temp["username"]), "OPERADOR")
        self.assertIsNone(session.get_session())
        self.assertEqual(notices, [user_view.MSG_SESSION_ENDED])
        # run_app vuelve al login cuando run_main devuelve True
        with mock.patch.object(main, "run_login", side_effect=[user, None]) as login, \
                mock.patch.object(main, "run_main", return_value=True):
            main.run_app()
        self.assertEqual(login.call_count, 2)
        fresh = authenticate(temp["username"], NEW_PASSWORD)
        self.assertEqual(fresh["role_code"], "OPERADOR")
        session.start_session(fresh)
        other = MainView(fresh)
        self.windows.append(other)
        other.update()
        self.assertNotIn("manage_users", other.access_widgets)

    def test_own_role_change_closes_user_view_and_flags_logout(self):
        temp = self.make_temp_admin()
        user = self.login_temp_admin(temp)
        main_view = MainView(user)
        self.windows.append(main_view)
        main_view.update()
        view = main_view.open_users()
        view.notify = lambda message: None
        view.update()
        self.select(view, temp["username"])
        view.role_combo.set("Operador")
        view.save_button.invoke()
        main_view.update()
        self.assertFalse(view.winfo_exists())
        self.assertTrue(main_view.logged_out)
        self.assertIsNone(session.get_session())

    def test_own_edit_without_role_change_keeps_session(self):
        temp = self.make_temp_admin()
        user = self.login_temp_admin(temp)
        main_view = MainView(user)
        self.windows.append(main_view)
        main_view.update()
        view = main_view.open_users()
        notices = []
        view.notify = notices.append
        view.update()
        self.select(view, temp["username"])
        _set(view.first_name_entry, "Renombrado")
        view.save_button.invoke()
        view.update()
        self.assertTrue(view.winfo_exists())
        self.assertTrue(session.is_active())
        self.assertEqual(notices, [])
        self.assertFalse(main_view.logged_out)
        self.assertIn("actualizado correctamente", view.message_label.cget("text"))

    def test_editing_another_user_role_keeps_session(self):
        temp = self.make_temp_admin("adm")
        other = self.make_temp_admin("adm2")
        user = self.login_temp_admin(temp)
        main_view = MainView(user)
        self.windows.append(main_view)
        main_view.update()
        view = main_view.open_users()
        notices = []
        view.notify = notices.append
        view.update()
        self.select(view, other["username"])
        view.role_combo.set("Consulta")
        view.save_button.invoke()
        view.update()
        self.assertEqual(self.role_code_of(other["username"]), "CONSULTA")
        self.assertTrue(session.is_active())
        self.assertEqual(notices, [])
        self.assertFalse(main_view.logged_out)
        self.assertTrue(view.winfo_exists())

    def test_failed_self_role_update_keeps_session(self):
        from controllers.user_controller import LastAdministratorError
        temp = self.make_temp_admin()
        user = self.login_temp_admin(temp)
        main_view = MainView(user)
        self.windows.append(main_view)
        main_view.update()
        view = main_view.open_users()
        notices = []
        view.notify = notices.append
        view.update()
        self.select(view, temp["username"])
        view.role_combo.set("Operador")
        with mock.patch.object(
            user_controller, "update_user", side_effect=LastAdministratorError("Debe existir al menos un administrador activo.")
        ):
            view.save_button.invoke()
        view.update()
        self.assertEqual(
            view.message_label.cget("text"), "Debe existir al menos un administrador activo."
        )
        self.assertTrue(session.is_active())
        self.assertEqual(notices, [])
        self.assertFalse(main_view.logged_out)
        self.assertTrue(view.winfo_exists())
        self.assertEqual(self.role_code_of(temp["username"]), "ADMINISTRADOR")

    def test_technical_error_shows_generic_message(self):
        view = self.open_users()
        self.fill(view)
        secret = "SELECT secreto FROM tabla Traceback"
        with mock.patch.object(
            user_controller, "create_user", side_effect=UserServiceError(secret)
        ), self.assertLogs(user_view.logger, level="ERROR"):
            view.create_button.invoke()
        view.update()
        text = view.message_label.cget("text")
        self.assertEqual(text, user_view.MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)

    def test_authorization_denied_shows_permission_message(self):
        view = self.open_users()
        self.fill(view)
        with mock.patch.object(
            user_controller, "create_user", side_effect=AuthorizationDenied("x")
        ):
            view.create_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), user_view.MSG_DENIED)

    def test_view_has_no_data_access_imports(self):
        for forbidden in ("models", "config.database", "mysql", "bcrypt", "ADMINISTRADOR"):
            self.assertNotIn(f"import {forbidden}", SOURCE)
            self.assertNotIn(f"from {forbidden}", SOURCE)
        self.assertNotIn("ADMINISTRADOR", SOURCE)

    def test_non_admin_main_view_has_no_users_button(self):
        for username in ("operador.central", "consulta.sur"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                self.assertNotIn("manage_users", main.access_widgets)
                texts = [
                    w.cget("text") for w in main.winfo_children()[0].winfo_children()
                    if isinstance(w, ctk.CTkButton)
                ]
                self.assertNotIn("Usuarios", texts)
                main.destroy()
                session.end_session()

    def test_forced_open_by_non_admin_is_denied_and_changes_nothing(self):
        for username in ("operador.central", "consulta.sur"):
            with self.subTest(username=username):
                view = self.open_users(username)
                self.assertEqual(view.message_label.cget("text"), user_view.MSG_DENIED)
                self.assertEqual(self.rows(view), [])
                self.fill(view, username[:3])
                view.create_button.invoke()
                view.update()
                self.assertEqual(view.message_label.cget("text"), user_view.MSG_DENIED)
                created = _db_all(
                    "SELECT COUNT(*) FROM usuarios WHERE nombre_usuario LIKE %s",
                    (self.prefix + "%",),
                )
                self.assertEqual(created[0][0], 0)
                view.destroy()
                session.end_session()

    def test_admin_main_view_opens_users_module(self):
        user = _login("admin.demo")
        main = MainView(user)
        self.windows.append(main)
        main.update()
        button = main.access_widgets["manage_users"]
        self.assertEqual(button.cget("text"), "Usuarios")
        button.invoke()
        main.update()
        self.assertIsInstance(main.user_view, UserView)
        self.assertTrue(main.user_view.winfo_exists())
        self.assertIn("admin.demo", [r[0] for r in self.rows(main.user_view)])
        # Un segundo clic reutiliza la misma ventana
        self.assertIs(main.open_users(), main.user_view)
        main.user_view.back_button.invoke()
        main.update()
        self.assertFalse(main.user_view.winfo_exists())


if __name__ == "__main__":
    unittest.main()

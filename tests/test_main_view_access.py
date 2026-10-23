import unittest
from pathlib import Path

import customtkinter as ctk

from controllers.auth_controller import authenticate
from utils import permissions as perm
from utils import session
from views.dispatch_view import DispatchView
from views.main_view import MainView

PASSWORD = "FireDemo2026!"
VIEW_SOURCE = Path(__file__).resolve().parent.parent / "views" / "main_view.py"

VIEW_ONLY = {perm.PERMISSION_VIEW_OPERATIONAL_DATA, perm.PERMISSION_VIEW_REPORTS}
ADMIN_ONLY = {
    perm.PERMISSION_MANAGE_USERS,
    perm.PERMISSION_MANAGE_CATALOGS,
    perm.PERMISSION_VIEW_AUDIT,
}


class MainViewAccessTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()
        session.end_session()

    def open_view(self, username):
        user = authenticate(username, PASSWORD)
        self.assertIsNotNone(user, f"No se pudo autenticar a {username}")
        session.start_session(user)
        view = MainView(user)
        self.windows.append(view)
        view.update()
        return view

    def test_admin_sees_everything(self):
        view = self.open_view("admin.demo")
        self.assertEqual(view.granted_permissions, perm.ALL_PERMISSIONS)
        self.assertEqual(set(view.access_widgets), set(perm.ALL_PERMISSIONS))
        self.assertIsNone(view.read_only_label)
        # Catálogos ya no es una etiqueta pendiente: abre el módulo de estaciones
        self.assertEqual(view.access_widgets[perm.PERMISSION_MANAGE_CATALOGS].cget("text"), "Estaciones")
        self.assertEqual(view.access_widgets[perm.PERMISSION_MANAGE_RESOURCES].cget("text"), "Personal")
        self.assertEqual(view.units_button.cget("text"), "Unidades")
        incidents = view.access_widgets[perm.PERMISSION_OPERATE_INCIDENTS]
        self.assertEqual(incidents.cget("text"), "Incidentes")
        self.assertIs(incidents, view.access_widgets[perm.PERMISSION_VIEW_OPERATIONAL_DATA])

    def test_operator_has_no_admin_items(self):
        view = self.open_view("operador.central")
        expected = set(perm.ALL_PERMISSIONS) - ADMIN_ONLY
        self.assertEqual(set(view.access_widgets), expected)
        for permission in ADMIN_ONLY:
            self.assertNotIn(permission, view.access_widgets)
        self.assertIsNone(view.read_only_label)
        # Recursos abre el módulo de personal; no hay botones de usuarios ni estaciones
        resources = view.access_widgets[perm.PERMISSION_MANAGE_RESOURCES]
        self.assertEqual(resources.cget("text"), "Personal")
        texts = [
            w.cget("text") for w in view.winfo_children()[0].winfo_children()
            if isinstance(w, ctk.CTkButton)
        ]
        self.assertIn("Personal", texts)
        self.assertIn("Unidades", texts)
        self.assertIn("Incidentes", texts)
        self.assertNotIn("Estaciones", texts)
        self.assertNotIn("Usuarios", texts)

    def test_read_only_user_sees_only_view_items(self):
        view = self.open_view("consulta.sur")
        self.assertEqual(set(view.access_widgets), VIEW_ONLY)
        self.assertIsNone(view.units_button)
        self.assertEqual(view.incidents_button.cget("text"), "Incidentes")
        self.assertIs(
            view.access_widgets[perm.PERMISSION_VIEW_OPERATIONAL_DATA], view.incidents_button
        )
        self.assertNotIn(perm.PERMISSION_OPERATE_INCIDENTS, view.access_widgets)
        self.assertIsNotNone(view.read_only_label)
        self.assertEqual(view.read_only_label.cget("text"), "Modo solo lectura")
        self.assertTrue(view.read_only_label.winfo_ismapped())

    def test_every_role_has_separate_query_button(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            view = self.open_view(username)
            self.assertEqual(view.query_button.cget("text"), "Consultar incidentes")
            self.assertIsNot(view.query_button, view.incidents_button)
            self.assertNotIn(view.query_button, view.access_widgets.values())

    def test_every_role_opens_dispatch_module(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            view = self.open_view(username)
            button = view.dispatches_button
            self.assertEqual(button.cget("text"), "Despachos (borradores)")
            if perm.PERMISSION_OPERATE_DISPATCHES in view.access_widgets:
                self.assertIs(view.access_widgets[perm.PERMISSION_OPERATE_DISPATCHES], button)
            button.invoke()
            view.update()
            module = view.dispatch_view
            self.assertIsInstance(module, DispatchView)
            button.invoke()
            self.assertIs(view.dispatch_view, module)
            module.back_button.invoke()
            view.update()
            self.assertFalse(module.winfo_exists())
            # El módulo de despachos ya no es una etiqueta pendiente
            labels = [
                w.cget("text") for w in view.winfo_children()[0].winfo_children()
                if isinstance(w, ctk.CTkLabel)
            ]
            self.assertFalse([t for t in labels if t.startswith("Despachos")])
            view.destroy()
            self.windows.remove(view)
            session.end_session()

    def test_without_session_nothing_is_offered(self):
        # Sin sesión el controlador falla cerrado y no se ofrece nada
        user = {"first_name": "A", "last_name": "B", "username": "x", "role_code": "X"}
        view = MainView(user)
        self.windows.append(view)
        view.update()
        self.assertEqual(view.access_widgets, {})
        self.assertIsNotNone(view.read_only_label)

    def test_view_does_not_duplicate_role_policy(self):
        source = VIEW_SOURCE.read_text(encoding="utf-8")
        for role in ("ADMINISTRADOR", "OPERADOR", "CONSULTA"):
            self.assertNotIn(role, source)


if __name__ == "__main__":
    unittest.main()

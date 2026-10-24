import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import customtkinter as ctk

from controllers import audit_controller
from controllers.access_controller import AuthorizationDenied
from controllers.audit_controller import AuditServiceError, AuditValidationError
from controllers.auth_controller import authenticate
from utils import session
from views import audit_view
from views.audit_view import AuditView, MSG_DENIED
from views.main_view import MainView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(audit_view.__file__).read_text(encoding="utf-8")
OPTIONS = {
    "actions": ["CAMBIO_ESTADO", "CREAR", "LOGIN"],
    "entities": ["DESPACHO", "INCIDENTE", "USUARIO"],
    "usernames": ["admin.demo", "operador.central"],
}


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def sample(rows=None, truncated=False):
    if rows is None:
        rows = [
            {"audit_id": 3, "occurred_at": datetime(2026, 10, 21, 8, 30, 5), "user_id": 1,
             "username": "admin.demo", "action": "CREAR", "entity": "INCIDENTE",
             "entity_id": 9, "detail": "Alta de incidente"},
            {"audit_id": 2, "occurred_at": datetime(2026, 10, 20, 7, 0, 0), "user_id": None,
             "username": None, "action": "LOGIN", "entity": "USUARIO", "entity_id": None,
             "detail": None},
        ]
    return {"rows": rows, "truncated": truncated}


class AuditViewTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.root = ctk.CTk()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        session.end_session()

    def open_view(self, data=None):
        _login("admin.demo")
        with mock.patch.object(audit_controller, "search_audit",
                               return_value=data or sample()), \
                mock.patch.object(audit_controller, "list_audit_filter_options",
                                  return_value=OPTIONS):
            view = AuditView(self.root)
        view.update()
        return view

    def rows(self, view):
        return [view.tree.item(i, "values") for i in view.tree.get_children()]

    def test_columns_rows_and_placeholders(self):
        view = self.open_view()
        self.assertEqual(
            [view.tree.heading(c, "text") for c in view.tree["columns"]],
            ["Fecha/hora", "Usuario", "Acción", "Entidad", "ID entidad", "Detalle"])
        rows = self.rows(view)
        self.assertEqual([str(v) for v in rows[0][:5]], [
            "21/10/2026 08:30:05", "admin.demo", "CREAR", "INCIDENTE", "9"])
        self.assertEqual(rows[1][:2], ("20/10/2026 07:00:00", "—"))
        self.assertEqual(rows[1][4], "—")
        self.assertNotIn("None", "".join(str(v) for r in rows for v in r))
        self.assertEqual(view.count_label.cget("text"), "2 registros")
        self.assertIsInstance(view.scrollbar, audit_view.ttk.Scrollbar)
        self.assertLessEqual(view.winfo_reqwidth(), 1366)
        self.assertLessEqual(view.winfo_reqheight(), 768)

    def test_truncated_and_empty_states(self):
        view = self.open_view(data=sample(truncated=True))
        self.assertEqual(view.message_label.cget("text"), audit_view.MSG_TRUNCATED)
        view = self.open_view(data=sample(rows=[]))
        self.assertEqual(self.rows(view)[0][0], audit_view.EMPTY_TEXT)
        self.assertEqual(view.count_label.cget("text"), audit_view.EMPTY_TEXT)

    def test_search_clear_and_refresh_query_once_each(self):
        view = self.open_view()
        self.assertEqual(
            list(view.user_filter.cget("values")), ["Todos"] + OPTIONS["usernames"])
        view.date_from_entry.insert(0, "2026-10-01")
        view.date_to_entry.insert(0, "2026-10-31")
        view.user_filter.set("admin.demo")
        view.action_filter.set("CREAR")
        view.entity_filter.set("INCIDENTE")
        expected = {"date_from": "2026-10-01", "date_to": "2026-10-31",
                    "username": "admin.demo", "action": "CREAR", "entity": "INCIDENTE"}
        with mock.patch.object(audit_controller, "search_audit",
                               return_value=sample(rows=sample()["rows"][:1])) as spy:
            view.search_button.invoke()
            self.assertEqual((spy.call_count, spy.call_args[0][0]), (1, expected))
            self.assertEqual(len(self.rows(view)), 1)
            # Refrescar repite los filtros aplicados aunque el formulario cambie
            view.action_filter.set("LOGIN")
            view.refresh_button.invoke()
            self.assertEqual((spy.call_count, spy.call_args[0][0]), (2, expected))
            view.clear_button.invoke()
            self.assertEqual((spy.call_count, spy.call_args[0][0]), (3, {}))
        self.assertEqual(view.date_from_entry.get(), "")
        self.assertEqual(view.user_filter.get(), "Todos")
        self.assertEqual(view.action_filter.get(), "Todas")

    def test_errors_keep_previous_data_without_traceback(self):
        view = self.open_view()
        for error, expected in (
            (AuditValidationError("La fecha desde no es válida."),
             "La fecha desde no es válida."),
            (AuditServiceError("No fue posible consultar la bitácora."),
             "No fue posible consultar la bitácora."),
        ):
            with mock.patch.object(audit_controller, "search_audit", side_effect=error):
                view.search_button.invoke()
            self.assertEqual(view.message_label.cget("text"), expected)
            self.assertEqual(view.message_label.cget("text_color"), audit_view.COLOR_ERROR)
            self.assertEqual(len(self.rows(view)), 2)
        boom = RuntimeError("SELECT * FROM bitacora Traceback")
        with mock.patch.object(audit_controller, "search_audit", side_effect=boom), \
                self.assertLogs("views.audit_view", level="ERROR"):
            view.refresh_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, audit_view.MSG_INTERNAL)
        self.assertNotIn("SELECT", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(self.rows(view)), 2)

    def test_without_session_shows_denied_message(self):
        session.end_session()
        view = AuditView(self.root)
        view.update()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED)
        self.assertEqual(self.rows(view), [])

    def test_back_button_closes_and_only_read_buttons_exist(self):
        view = self.open_view()
        texts = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    texts.add(child.cget("text"))
                walk(child)

        walk(view)
        self.assertEqual(texts, {"Buscar", "Limpiar", "Refrescar", "Regresar"})
        view.back_button.invoke()
        self.root.update()
        self.assertFalse(view.winfo_exists())

    def test_real_audit_matches_controller_for_admin(self):
        _login("admin.demo")
        expected = audit_controller.search_audit({})
        view = AuditView(self.root)
        view.update()
        rows = self.rows(view)
        self.assertEqual(len(rows), max(1, len(expected["rows"])))
        first = expected["rows"][0]
        self.assertEqual(rows[0][2], first["action"])
        self.assertEqual(rows[0][3], first["entity"])
        self.assertEqual(rows[0][0], first["occurred_at"].strftime("%d/%m/%Y %H:%M:%S"))

    def test_source_has_no_sql_or_data_access(self):
        self.assertIsNone(re.search(
            r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN)\b", SOURCE))
        for fragment in ("models", "config.", "mysql", "bcrypt", "cursor", "execute(",
                         "ADMINISTRADOR", "OPERADOR", "CONSULTA"):
            self.assertNotIn(fragment, SOURCE)
        for module in re.findall(r"^(?:from|import) (\S+)", SOURCE, flags=re.M):
            self.assertTrue(
                module in {"logging", "tkinter", "customtkinter"}
                or module.startswith("controllers"), module)


class MainViewAuditTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            try:
                window.destroy()
            except Exception:
                pass
        session.end_session()

    def labels(self, main):
        return [w.cget("text") for w in main.winfo_children()[0].winfo_children()
                if isinstance(w, ctk.CTkLabel)]

    def test_admin_opens_audit_from_main_view(self):
        user = _login("admin.demo")
        main = MainView(user)
        self.windows.append(main)
        main.update()
        self.assertEqual(main.audit_button.cget("text"), "Bitácora")
        self.assertEqual(str(main.audit_button.cget("state")), "normal")
        self.assertFalse([t for t in self.labels(main) if t.startswith("Bitácora")])
        main.audit_button.invoke()
        main.update()
        module = main.audit_view
        self.assertIsInstance(module, AuditView)
        main.audit_button.invoke()
        self.assertIs(main.audit_view, module)
        module.back_button.invoke()
        main.update()
        self.assertFalse(module.winfo_exists())

    def test_other_roles_do_not_get_audit_and_are_denied(self):
        for username in ("operador.central", "consulta.sur"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                self.assertIsNone(main.audit_button)
                self.assertNotIn("view_audit", main.access_widgets)
                self.assertFalse([t for t in self.labels(main) if t.startswith("Bitácora")])
                with self.assertRaises(AuthorizationDenied):
                    audit_controller.search_audit({})
                # Apertura forzada: el controlador niega y no se carga nada
                module = main.open_audit()
                module.update()
                self.assertEqual(module.message_label.cget("text"), MSG_DENIED)
                self.assertEqual(module.tree.get_children(), ())
                module.close()
                main.destroy()
                session.end_session()


if __name__ == "__main__":
    unittest.main()

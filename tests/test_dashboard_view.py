import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import customtkinter as ctk

from controllers import dashboard_controller
from controllers.auth_controller import authenticate
from controllers.dashboard_controller import DashboardServiceError
from utils import session
from views import dashboard_view
from views.dashboard_view import DashboardView, EMPTY_TEXT, MSG_DENIED, MSG_INTERNAL
from views.main_view import MainView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(dashboard_view.__file__).read_text(encoding="utf-8")


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def sample(**overrides):
    data = {
        "active_incidents": 5,
        "critical_incidents": 3,
        "available_units": 2,
        "avg_response_minutes": 8.4,
        "recent_activity": [
            {"occurred_at": datetime(2026, 10, 20, 9, 5, 7), "username": "admin.demo",
             "action": "CREAR", "entity": "INCIDENTE", "entity_id": 1, "detail": "Alta"},
            {"occurred_at": datetime(2026, 10, 19, 18, 0, 0), "username": None,
             "action": "LOGIN", "entity": "USUARIO", "entity_id": 2, "detail": None},
        ],
        "incidents_by_type": [
            {"type_id": 1, "type_name": "INCENDIO", "count": 6},
            {"type_id": 2, "type_name": "ACCIDENTE", "count": 3},
            {"type_id": 3, "type_name": "RESCATE", "count": 1},
        ],
        "incidents_by_period": [
            {"date": "2026-10-19", "count": 4},
            {"date": "2026-10-20", "count": 2},
        ],
    }
    data.update(overrides)
    return data


class DashboardViewTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.root = ctk.CTk()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        session.end_session()

    def open_view(self, username="operador.central", data=None):
        _login(username)
        with mock.patch.object(
                dashboard_controller, "get_dashboard_data", return_value=data or sample()):
            view = DashboardView(self.root)
        view.update()
        return view

    def rows(self, tree):
        return [tree.item(i, "values") for i in tree.get_children()]

    def kpis(self, view):
        return {k: label.cget("text") for k, label in view.kpi_labels.items()}

    def test_kpi_cards_show_returned_values(self):
        view = self.open_view()
        self.assertEqual(self.kpis(view), {
            "active": "5", "critical": "3", "units": "2", "response": "8.4 min"})
        view.close()
        view = self.open_view(data=sample(avg_response_minutes=None))
        self.assertEqual(view.kpi_labels["response"].cget("text"), "—")
        self.assertNotIn("None", "".join(self.kpis(view).values()))

    def test_tables_show_rows_in_given_order(self):
        view = self.open_view()
        activity = self.rows(view.activity_tree)
        self.assertEqual(activity[0], (
            "20/10/2026 09:05:07", "admin.demo", "CREAR", "INCIDENTE", "Alta"))
        self.assertEqual(activity[1][:2], ("19/10/2026 18:00:00", "—"))
        self.assertNotIn("None", "".join(str(v) for r in activity for v in r))
        types = self.rows(view.types_tree)
        self.assertEqual([(r[0], str(r[1])) for r in types],
                         [("INCENDIO", "6"), ("ACCIDENTE", "3"), ("RESCATE", "1")])
        self.assertGreater(len(types[0][2]), len(types[2][2]))
        periods = self.rows(view.period_tree)
        self.assertEqual([(r[0], str(r[1])) for r in periods],
                         [("2026-10-19", "4"), ("2026-10-20", "2")])

    def test_empty_tables_show_neutral_row(self):
        view = self.open_view(data=sample(
            recent_activity=[], incidents_by_type=[], incidents_by_period=[]))
        for tree in (view.activity_tree, view.types_tree, view.period_tree):
            rows = self.rows(tree)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], EMPTY_TEXT)

    def test_refresh_requeries_once_and_updates(self):
        view = self.open_view()
        with mock.patch.object(
                dashboard_controller, "get_dashboard_data",
                return_value=sample(active_incidents=9)) as spy:
            view.refresh_button.invoke()
            view.update()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(view.kpi_labels["active"].cget("text"), "9")

    def test_open_calls_controller_once(self):
        _login("admin.demo")
        with mock.patch.object(
                dashboard_controller, "get_dashboard_data", return_value=sample()) as spy:
            DashboardView(self.root)
        self.assertEqual(spy.call_count, 1)

    def test_back_button_closes_window(self):
        view = self.open_view()
        view.back_button.invoke()
        self.root.update()
        self.assertFalse(view.winfo_exists())

    def test_window_fits_screen(self):
        view = self.open_view()
        self.assertLessEqual(view.winfo_reqwidth(), 1366)
        self.assertLessEqual(view.winfo_reqheight(), 768)

    def test_read_only_user_sees_same_and_has_only_two_buttons(self):
        view = self.open_view("consulta.sur")
        self.assertEqual(self.kpis(view)["active"], "5")
        texts = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    texts.add(child.cget("text"))
                walk(child)

        walk(view)
        self.assertEqual(texts, {"Refrescar", "Regresar"})

    def test_service_error_keeps_previous_data(self):
        view = self.open_view()
        error = DashboardServiceError("No fue posible cargar los indicadores del panel.")
        with mock.patch.object(dashboard_controller, "get_dashboard_data", side_effect=error):
            view.refresh_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, "No fue posible cargar los indicadores del panel.")
        self.assertEqual(view.message_label.cget("text_color"), dashboard_view.COLOR_ERROR)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(view.kpi_labels["active"].cget("text"), "5")
        self.assertEqual(len(self.rows(view.types_tree)), 3)

    def test_unexpected_error_is_generic(self):
        view = self.open_view()
        error = RuntimeError("SELECT * FROM incidentes Traceback")
        with mock.patch.object(dashboard_controller, "get_dashboard_data", side_effect=error), \
                self.assertLogs("views.dashboard_view", level="ERROR"):
            view.refresh_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("SELECT", text)
        self.assertEqual(view.kpi_labels["active"].cget("text"), "5")

    def test_without_session_shows_denied_message(self):
        session.end_session()
        view = DashboardView(self.root)
        view.update()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED)
        self.assertEqual(view.kpi_labels["active"].cget("text"), "—")

    def test_real_dashboard_matches_controller(self):
        _login("admin.demo")
        expected = dashboard_controller.get_dashboard_data()
        view = DashboardView(self.root)
        view.update()
        average = expected["avg_response_minutes"]
        self.assertEqual(self.kpis(view), {
            "active": str(expected["active_incidents"]),
            "critical": str(expected["critical_incidents"]),
            "units": str(expected["available_units"]),
            "response": "—" if average is None else f"{average:.1f} min",
        })
        self.assertEqual(
            len(self.rows(view.activity_tree)), max(1, len(expected["recent_activity"])))
        self.assertEqual(
            len(self.rows(view.types_tree)), max(1, len(expected["incidents_by_type"])))

    def test_source_has_no_sql_or_data_access(self):
        self.assertIsNone(re.search(
            r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN)\b", SOURCE))
        for fragment in ("models", "config.", "mysql", "bcrypt", "cursor", "execute(",
                         "ADMINISTRADOR", "OPERADOR", "CONSULTA"):
            self.assertNotIn(fragment, SOURCE)
        for module in re.findall(r"^(?:from|import) (\S+)", SOURCE, flags=re.M):
            self.assertTrue(
                module in {"logging", "tkinter", "customtkinter"} or module.startswith("controllers"),
                module)


class MainViewDashboardTest(unittest.TestCase):
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

    def test_every_role_opens_dashboard_from_main_view(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                self.assertEqual(main.dashboard_button.cget("text"), "Dashboard")
                self.assertEqual(str(main.dashboard_button.cget("state")), "normal")
                main.dashboard_button.invoke()
                main.update()
                module = main.dashboard_view
                self.assertIsInstance(module, DashboardView)
                main.dashboard_button.invoke()
                self.assertIs(main.dashboard_view, module)
                module.back_button.invoke()
                main.update()
                self.assertFalse(module.winfo_exists())
                # Reportes ya es un módulo real; Bitácora sigue como etiqueta pendiente
                labels = [
                    w.cget("text") for w in main.winfo_children()[0].winfo_children()
                    if isinstance(w, ctk.CTkLabel)
                ]
                self.assertFalse([t for t in labels if t.startswith("Reportes")])
                main.destroy()
                session.end_session()


if __name__ == "__main__":
    unittest.main()

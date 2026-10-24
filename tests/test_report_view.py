import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import customtkinter as ctk

from controllers import report_controller
from controllers.auth_controller import authenticate
from controllers.report_controller import (
    ReportExportError, ReportServiceError, ReportValidationError,
)
from tests._incident_support import IncidentTestBase
from utils import session
from views import report_view
from views.main_view import MainView
from views.report_view import MSG_DENIED, ReportView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(report_view.__file__).read_text(encoding="utf-8")
BUTTONS = {
    "Buscar", "Limpiar", "Ver detalle", "Exportar PDF general",
    "Exportar PDF del incidente", "Regresar",
}


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def sample_general(response=8.4, count=2):
    incidents = [
        {"incident_id": 11, "code": "INC-A", "reported_at": datetime(2026, 10, 20, 9, 5),
         "type_name": "INCENDIO", "priority_name": "ALTA", "state_code": "CERRADO",
         "state_name": "Cerrado", "address": "Calle 1", "response_minutes": 8.4,
         "duration_minutes": 61.0},
        {"incident_id": 12, "code": "INC-B", "reported_at": datetime(2026, 10, 21, 10, 0),
         "type_name": "RESCATE", "priority_name": "MEDIA", "state_code": "REPORTADO",
         "state_name": "Reportado", "address": None, "response_minutes": None,
         "duration_minutes": None},
    ][:count]
    return {
        "incidents": incidents,
        "metrics": {
            "incident_count": len(incidents), "avg_response_minutes": response,
            "top_stations": [
                {"station_id": 1, "station_code": "EST-CENTRAL", "station_name": "C",
                 "activity": 3},
                {"station_id": 2, "station_code": "EST-SUR", "station_name": "S", "activity": 1},
            ],
            "units_used": 4, "top_units": [],
        },
        "filters_applied": {},
    }


def sample_incident():
    return {
        "code": "INC-A", "reported_at": datetime(2026, 10, 20, 9, 5), "type_name": "INCENDIO",
        "priority_name": "ALTA", "priority_level": 1, "state_code": "CERRADO",
        "state_name": "Cerrado", "address": "Calle 1", "reference": None, "latitude": None,
        "longitude": None, "description": "Humo", "affected_people": 0,
        "registered_by_name": "Admin Demo", "assigned_at": None, "attention_started_at": None,
        "controlled_at": None, "closed_at": None, "cancelled_at": None, "cancel_reason": None,
        "timeline": [{"key": "reported", "label": "Reportado", "at": datetime(2026, 10, 20, 9, 5)}],
        "dispatches": [{"dispatch_id": 7, "state_name": "Activo",
                        "created_at": datetime(2026, 10, 20, 9, 10), "dispatched_at": None,
                        "finished_at": None, "cancelled_at": None, "cancel_reason": None,
                        "observations": "Refuerzo"}],
        "units": [{"dispatch_id": 7, "unit_code": "A-77", "unit_name": None,
                   "unit_type_name": "AUTOBOMBA", "origin_station_code": "EST-CENTRAL",
                   "origin_station_name": "C", "assigned_at": datetime(2026, 10, 20, 9, 11),
                   "departed_at": None, "arrived_at": None, "released_at": None,
                   "cancelled_at": None, "cancel_reason": None,
                   "participation_status": "ACTIVA"}],
        "crew": [{"dispatch_id": 7, "unit_code": "A-77", "personnel_code": "P-1",
                  "full_name": "Ana Pérez", "role": "Bombero"}],
        "response_minutes": 8.4, "duration_minutes": None,
    }


class ReportViewTest(unittest.TestCase):
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
                report_controller, "search_general_report",
                return_value=data or sample_general()):
            view = ReportView(self.root)
        view.update()
        return view

    def rows(self, view):
        return [view.tree.item(i, "values") for i in view.tree.get_children()]

    def select(self, view, incident_id):
        view.tree.selection_set(str(incident_id))
        view.update()

    def metrics(self, view):
        return {k: v.cget("text") for k, v in view.metric_labels.items()}

    def test_table_and_metrics_are_formatted(self):
        view = self.open_view()
        rows = self.rows(view)
        self.assertEqual(rows[0], (
            "INC-A", "20/10/2026 09:05", "INCENDIO", "ALTA", "Cerrado", "Calle 1",
            "8.4 min", "61.0 min"))
        self.assertEqual(rows[1][5:], ("", "—", "—"))
        self.assertNotIn("None", "".join(str(v) for r in rows for v in r))
        self.assertEqual(self.metrics(view), {
            "count": "2", "response": "8.4 min", "units": "4",
            "stations": "EST-CENTRAL (3), EST-SUR (1)"})
        self.assertTrue(isinstance(view.scrollbar, report_view.ttk.Scrollbar))
        self.assertLessEqual(view.winfo_reqwidth(), 1366)
        self.assertLessEqual(view.winfo_reqheight(), 768)

    def test_metrics_without_data_show_placeholders(self):
        data = sample_general(response=None, count=0)
        data["metrics"]["top_stations"] = []
        view = self.open_view(data=data)
        self.assertEqual(self.metrics(view)["response"], "—")
        self.assertEqual(self.metrics(view)["stations"], report_view.NO_DATA)
        self.assertEqual(self.rows(view), [])
        self.assertNotIn("None", "".join(self.metrics(view).values()))

    def test_open_queries_once_with_no_filters(self):
        _login("admin.demo")
        with mock.patch.object(
                report_controller, "search_general_report",
                return_value=sample_general()) as spy:
            ReportView(self.root)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args[0][0], {})

    def test_search_passes_filters_and_refreshes(self):
        view = self.open_view()
        type_name = next(iter(view._type_ids))
        priority_name = next(iter(view._priority_ids))
        state_name = next(iter(view._state_ids))
        view.date_from_entry.insert(0, "2026-10-01")
        view.date_to_entry.insert(0, "2026-10-31")
        view.type_filter.set(type_name)
        view.priority_filter.set(priority_name)
        view.state_filter.set(state_name)
        with mock.patch.object(
                report_controller, "search_general_report",
                return_value=sample_general(response=3.0, count=1)) as spy:
            view.search_button.invoke()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args[0][0], {
            "date_from": "2026-10-01", "date_to": "2026-10-31",
            "type_id": view._type_ids[type_name], "priority_id": view._priority_ids[priority_name],
            "state_id": view._state_ids[state_name]})
        self.assertEqual(len(self.rows(view)), 1)
        self.assertEqual(self.metrics(view)["response"], "3.0 min")
        # Limpiar restablece y vuelve a consultar sin filtros
        with mock.patch.object(
                report_controller, "search_general_report",
                return_value=sample_general()) as spy:
            view.clear_button.invoke()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args[0][0], {})
        self.assertEqual(view.date_from_entry.get(), "")
        self.assertEqual(view.type_filter.get(), report_view.ALL_TYPES)
        self.assertEqual(len(self.rows(view)), 2)

    def test_invalid_dates_keep_previous_table(self):
        view = self.open_view()
        view.date_from_entry.insert(0, "no-es-fecha")
        error = ReportValidationError("La fecha desde no es válida.")
        with mock.patch.object(report_controller, "search_general_report", side_effect=error):
            view.search_button.invoke()
        self.assertEqual(view.message_label.cget("text"), "La fecha desde no es válida.")
        self.assertEqual(view.message_label.cget("text_color"), report_view.COLOR_ERROR)
        self.assertEqual(len(self.rows(view)), 2)

    def test_real_invalid_range_is_reported(self):
        _login("admin.demo")
        view = ReportView(self.root)
        view.update()
        before = self.rows(view)
        view.date_from_entry.insert(0, "2026-12-31")
        view.date_to_entry.insert(0, "2026-01-01")
        view.search_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text_color"), report_view.COLOR_ERROR)
        self.assertEqual(self.rows(view), before)

    def test_service_error_keeps_data_without_traceback(self):
        view = self.open_view()
        error = ReportServiceError("No fue posible consultar el reporte.")
        with mock.patch.object(report_controller, "search_general_report", side_effect=error):
            view.search_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, "No fue posible consultar el reporte.")
        self.assertEqual(len(self.rows(view)), 2)
        boom = RuntimeError("SELECT * FROM incidentes Traceback")
        with mock.patch.object(report_controller, "search_general_report", side_effect=boom), \
                self.assertLogs("views.report_view", level="ERROR"):
            view.search_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, report_view.MSG_INTERNAL)
        self.assertNotIn("SELECT", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(self.rows(view)), 2)

    def test_without_session_shows_denied_message(self):
        session.end_session()
        view = ReportView(self.root)
        view.update()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED)
        self.assertEqual(self.rows(view), [])

    def test_buttons_follow_selection_and_exact_set(self):
        view = self.open_view("consulta.sur")
        self.assertEqual(str(view.detail_button.cget("state")), "disabled")
        self.assertEqual(str(view.export_incident_button.cget("state")), "disabled")
        self.assertEqual(str(view.export_general_button.cget("state")), "normal")
        self.select(view, 11)
        self.assertEqual(str(view.detail_button.cget("state")), "normal")
        self.assertEqual(str(view.export_incident_button.cget("state")), "normal")
        texts = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    texts.add(child.cget("text"))
                walk(child)

        walk(view)
        self.assertEqual(texts, BUTTONS)

    def test_detail_loads_once_and_shows_sections(self):
        view = self.open_view()
        self.select(view, 11)
        with mock.patch.object(
                report_controller, "get_incident_report",
                return_value=sample_incident()) as spy:
            view.detail_button.invoke()
            view.update()
        spy.assert_called_once_with(11)
        window = view.detail_window
        text = window.text.get("1.0", "end-1c")
        for fragment in ("INC-A", "LÍNEA DE TIEMPO", "Reportado", "DESPACHOS", "Refuerzo",
                         "UNIDADES Y PARTICIPACIONES", "A-77", "TRIPULACIÓN", "Ana Pérez",
                         "8.4 min"):
            self.assertIn(fragment, text)
        self.assertNotIn("None", text)
        self.assertEqual(window.close_button.cget("text"), "Cerrar")
        self.assertEqual(window.export_button.cget("text"), "Exportar PDF")
        window.close_button.invoke()
        view.update()
        self.assertFalse(window.winfo_exists())

    def test_export_general_uses_chosen_path_and_applied_filters(self):
        view = self.open_view()
        view.date_from_entry.insert(0, "2026-10-01")
        with mock.patch.object(
                report_controller, "search_general_report", return_value=sample_general()):
            view.search_button.invoke()
        # Cambiar el formulario sin buscar no altera el PDF: usa la última búsqueda
        view.date_from_entry.delete(0, "end")
        with mock.patch.object(view, "ask_save_path", return_value="C:/tmp/r.pdf") as ask, \
                mock.patch.object(
                    report_controller, "export_general_pdf", return_value="C:/tmp/r.pdf") as export:
            view.export_general_button.invoke()
        ask.assert_called_once_with("FireCommand_Reporte_General.pdf")
        export.assert_called_once_with({"date_from": "2026-10-01"}, "C:/tmp/r.pdf")
        self.assertEqual(view.message_label.cget("text"), "PDF generado: C:/tmp/r.pdf")

    def test_export_incident_and_cancel_and_error(self):
        view = self.open_view()
        view.export_selected()
        self.assertEqual(view.message_label.cget("text_color"), report_view.COLOR_ERROR)
        self.select(view, 12)
        view.message_label.configure(text="previo", text_color=report_view.COLOR_OK)
        for cancelled in (None, ""):
            with mock.patch.object(view, "ask_save_path", return_value=cancelled), \
                    mock.patch.object(report_controller, "export_incident_pdf") as export, \
                    mock.patch.object(report_controller, "export_general_pdf") as general:
                view.export_incident_button.invoke()
                view.export_general_button.invoke()
            self.assertFalse(export.called)
            self.assertFalse(general.called)
            self.assertEqual(view.message_label.cget("text"), "previo")
        with mock.patch.object(view, "ask_save_path", return_value="C:/tmp/i.pdf") as ask, \
                mock.patch.object(
                    report_controller, "export_incident_pdf", return_value="C:/tmp/i.pdf") as export:
            view.export_incident_button.invoke()
        ask.assert_called_once_with("FireCommand_INC-B.pdf")
        export.assert_called_once_with(12, "C:/tmp/i.pdf")
        error = ReportExportError("No fue posible generar el archivo PDF.")
        with mock.patch.object(view, "ask_save_path", return_value="C:/tmp/i.pdf"), \
                mock.patch.object(report_controller, "export_incident_pdf", side_effect=error):
            view.export_incident_button.invoke()
        self.assertEqual(view.message_label.cget("text"), "No fue posible generar el archivo PDF.")
        self.assertEqual(view.message_label.cget("text_color"), report_view.COLOR_ERROR)

    def test_back_button_closes(self):
        view = self.open_view()
        view.back_button.invoke()
        self.root.update()
        self.assertFalse(view.winfo_exists())

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


class RealReportTest(IncidentTestBase):
    """Pruebas con la base real usando solo incidentes temporales."""

    def setUp(self):
        super().setUp()
        session.end_session()
        self.root = ctk.CTk()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.tmp.cleanup()
        session.end_session()
        super().tearDown()

    def test_real_search_detail_and_pdf_exports(self):
        incident_id = self.insert_incident("R")
        _login("admin.demo")
        expected = report_controller.search_general_report({})
        view = ReportView(self.root)
        view.update()
        self.assertEqual(len(view.tree.get_children()), len(expected["incidents"]))
        self.assertEqual(
            view.metric_labels["count"].cget("text"), str(expected["metrics"]["incident_count"]))
        code = f"{self.prefix}-R"
        self.assertIn(str(incident_id), view.tree.get_children())
        view.tree.selection_set(str(incident_id))
        view.update()
        view.detail_button.invoke()
        view.update()
        self.assertIn(code, view.detail_window.text.get("1.0", "end-1c"))
        view.detail_window.close_button.invoke()
        view.update()
        incident_path = str(Path(self.tmp.name) / "incidente.pdf")
        with mock.patch.object(view, "ask_save_path", return_value=incident_path):
            view.export_incident_button.invoke()
        self.assertTrue(Path(incident_path).read_bytes().startswith(b"%PDF"))
        general_path = str(Path(self.tmp.name) / "general.pdf")
        with mock.patch.object(view, "ask_save_path", return_value=general_path):
            view.export_general_button.invoke()
        self.assertTrue(Path(general_path).read_bytes().startswith(b"%PDF"))
        self.assertEqual(view.message_label.cget("text"), f"PDF generado: {general_path}")


class MainViewReportTest(unittest.TestCase):
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

    def test_every_role_opens_reports_from_main_view(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                self.assertEqual(main.reports_button.cget("text"), "Reportes")
                self.assertEqual(str(main.reports_button.cget("state")), "normal")
                main.reports_button.invoke()
                main.update()
                module = main.report_view
                self.assertIsInstance(module, ReportView)
                main.reports_button.invoke()
                self.assertIs(main.report_view, module)
                module.back_button.invoke()
                main.update()
                self.assertFalse(module.winfo_exists())
                labels = [
                    w.cget("text") for w in main.winfo_children()[0].winfo_children()
                    if isinstance(w, ctk.CTkLabel)
                ]
                self.assertFalse([t for t in labels if t.startswith("Reportes")])
                if username == "admin.demo":
                    # Bitácora ya es un botón real para quien tiene el permiso
                    self.assertFalse([t for t in labels if t.startswith("Bitácora")])
                    self.assertEqual(main.audit_button.cget("text"), "Bitácora")
                main.destroy()
                session.end_session()


if __name__ == "__main__":
    unittest.main()

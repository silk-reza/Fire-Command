import re
from pathlib import Path
from unittest import mock

import customtkinter as ctk

from controllers import incident_controller
from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.incident_controller import IncidentServiceError, IncidentValidationError
from tests._incident_support import IncidentTestBase, db_all, db_exec
from utils import session
from views import incident_query_view
from views.incident_query_view import (
    ALL_PRIORITIES, ALL_STATES, ALL_TYPES, IncidentQueryView,
)
from views.main_view import MainView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(incident_query_view.__file__).read_text(encoding="utf-8")
COLUMN_TITLES = [
    "Código", "Fecha de reporte", "Tipo", "Prioridad", "Estado", "Dirección",
    "Personas afectadas",
]
SEED_FILTER = "codigo NOT LIKE 'TIN-%' AND codigo NOT LIKE 'TUI-%'"
ORDER = "ORDER BY fecha_reporte DESC, id_incidente DESC"
INVERTED = "La fecha desde no puede ser posterior a la fecha hasta."
EMPTY = "—"


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


def _all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _all_widgets(child)


class IncidentQueryViewTest(IncidentTestBase):
    def setUp(self):
        super().setUp()
        session.end_session()
        self.root = ctk.CTk()
        self.windows = [self.root]

    def tearDown(self):
        for window in self.windows:
            try:
                window.destroy()
            except Exception:
                pass
        session.end_session()
        super().tearDown()

    def open_query(self, username="operador.central"):
        _login(username)
        view = IncidentQueryView(self.root)
        view.update()
        return view

    def rows(self, view):
        return [view.tree.item(i, "values") for i in view.tree.get_children()]

    def codes(self, view):
        return [row[0] for row in self.rows(view)]

    def sql_codes(self, where="1 = 1", params=()):
        return [r[0] for r in db_all(
            f"SELECT codigo FROM incidentes WHERE {SEED_FILTER} AND ({where}) {ORDER}", params)]

    def apply(self, view, search="", date_from="", date_to=""):
        _set(view.search_entry, search)
        _set(view.date_from_entry, date_from)
        _set(view.date_to_entry, date_to)
        view.apply_button.invoke()
        view.update()

    def select(self, view, code):
        for iid in view.tree.get_children():
            if view.tree.item(iid, "values")[0] == code:
                view.tree.selection_set(iid)
                view.update()
                return iid
        self.fail(f"No se encontró {code} en la tabla")

    def set_reported(self, incident_id, value):
        db_exec("UPDATE incidentes SET fecha_reporte = %s WHERE id_incidente = %s",
                (value, incident_id))

    def detail(self, view):
        return {k: v.cget("text") for k, v in view.detail_values.items()}

    def test_opens_for_every_role_and_lists_all_recent_first(self):
        expected = self.sql_codes()
        self.assertEqual(len(expected), 8)
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            view = self.open_query(username)
            self.assertEqual(self.codes(view), expected)
            self.assertEqual(view.count_label.cget("text"), "8 incidentes")
            titles = [view.tree.heading(c, "text") for c in view.tree["columns"]]
            self.assertEqual(titles, COLUMN_TITLES)
            first = view.tree.item(view.tree.get_children()[0], "values")
            self.assertRegex(first[1], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
            view.back_button.invoke()
            session.end_session()

    def test_text_filter_by_code_address_and_description(self):
        view = self.open_query()
        rows = db_all(f"SELECT codigo, direccion, descripcion FROM incidentes WHERE {SEED_FILTER}")
        code, address, description = rows[0]
        for fragment, column in ((code[-4:], 0), (address[:6].upper(), 1), (description[:8], 2)):
            expected = [
                r[0] for r in db_all(
                    f"SELECT codigo, direccion, descripcion FROM incidentes WHERE {SEED_FILTER} "
                    f"{ORDER}")
                if fragment.lower() in (r[column] or "").lower()
            ]
            self.apply(view, fragment)
            self.assertEqual(self.codes(view), expected)
            self.assertTrue(expected)
        self.apply(view, "zzz-no-existe-zzz")
        self.assertEqual(self.codes(view), [])
        self.assertEqual(view.count_label.cget("text"), "0 incidentes")

    def test_catalog_filters_use_real_catalogs(self):
        view = self.open_query()
        for table, id_col, combo, fk in (
            ("tipos_incidente", "id_tipo_incidente", view.type_filter, "id_tipo_incidente"),
            ("prioridades", "id_prioridad", view.priority_filter, "id_prioridad"),
            ("estados_incidente", "id_estado_incidente", view.state_filter,
             "id_estado_incidente"),
        ):
            name_col = "nombre"
            for item_id, name in db_all(f"SELECT {id_col}, {name_col} FROM {table}"):
                combo.set(name)
                self.apply(view)
                self.assertEqual(self.codes(view), self.sql_codes(f"{fk} = %s", (item_id,)))
            combo.set({"tipos_incidente": ALL_TYPES, "prioridades": ALL_PRIORITIES,
                       "estados_incidente": ALL_STATES}[table])
        self.apply(view)
        self.assertEqual(len(self.codes(view)), 8)

    def test_date_filters_and_inclusive_hasta(self):
        incident_id = self.insert_incident("F")
        self.set_reported(incident_id, "2031-05-05 23:30:00")
        view = self.open_query()
        code = f"{self.prefix}-F"
        self.apply(view, date_to="2031-05-05")
        self.assertIn(code, self.codes(view))
        self.apply(view, date_to="2031-05-04")
        self.assertNotIn(code, self.codes(view))
        self.apply(view, date_from="2031-05-05")
        self.assertEqual(self.codes(view), [code])
        self.apply(view, date_from="2031-05-05 23:31")
        self.assertEqual(self.codes(view), [])
        self.apply(view, date_from="2031-05-05", date_to="2031-05-05")
        self.assertEqual(self.codes(view), [code])
        self.apply(view, date_from="2031-05-05 23:00", date_to="2031-05-05 23:30")
        self.assertEqual(self.codes(view), [code])
        # Rango sobre las fechas reales de las semillas
        low, high = db_all(
            f"SELECT MIN(DATE(fecha_reporte)), MAX(DATE(fecha_reporte)) FROM incidentes "
            f"WHERE {SEED_FILTER}")[0]
        self.apply(view, date_from=str(low), date_to=str(high))
        self.assertEqual(self.codes(view), self.sql_codes())
        self.apply(view, date_from=str(high))
        self.assertEqual(self.codes(view), [code] + self.sql_codes(
            "DATE(fecha_reporte) >= %s", (high,)))

    def test_invalid_dates_show_message_and_keep_results(self):
        view = self.open_query()
        before = self.codes(view)
        self.apply(view, date_from="2026-12-31", date_to="2026-01-01")
        self.assertEqual(view.message_label.cget("text"), INVERTED)
        self.assertEqual(self.codes(view), before)
        with self.assertRaises(IncidentValidationError) as raised:
            incident_controller.filter_incidents(date_from="2026-13-45")
        self.apply(view, date_from="2026-13-45")
        self.assertEqual(view.message_label.cget("text"), str(raised.exception))
        self.assertEqual(self.codes(view), before)
        self.apply(view, date_from="no es fecha")
        self.assertNotIn("Traceback", view.message_label.cget("text"))
        self.assertEqual(self.codes(view), before)

    def test_combined_filters(self):
        view = self.open_query()
        type_id, type_name, priority_id, priority_name, date = db_all(
            f"SELECT i.id_tipo_incidente, t.nombre, i.id_prioridad, p.nombre, "
            f"DATE(i.fecha_reporte) FROM incidentes i "
            f"JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente "
            f"JOIN prioridades p ON p.id_prioridad = i.id_prioridad "
            f"WHERE i.{SEED_FILTER} ORDER BY i.fecha_reporte DESC LIMIT 1")[0]
        view.type_filter.set(type_name)
        view.priority_filter.set(priority_name)
        self.apply(view, date_from=str(date), date_to=str(date))
        expected = self.sql_codes(
            "id_tipo_incidente = %s AND id_prioridad = %s AND DATE(fecha_reporte) = %s",
            (type_id, priority_id, date))
        self.assertTrue(expected)
        self.assertEqual(self.codes(view), expected)

    def test_inactive_catalog_items_are_filterable(self):
        type_id = self.temp_type()
        priority_id = self.temp_priority()
        self.insert_incident("I", tipo=type_id, prioridad=priority_id)
        self.deactivate_type(type_id)
        self.deactivate_priority(priority_id)
        view = self.open_query()
        type_name = db_all("SELECT nombre FROM tipos_incidente WHERE id_tipo_incidente = %s",
                           (type_id,))[0][0]
        priority_name = db_all("SELECT nombre FROM prioridades WHERE id_prioridad = %s",
                               (priority_id,))[0][0]
        self.assertIn(f"{type_name} (inactivo/a)", view.type_filter.cget("values"))
        self.assertIn(f"{priority_name} (inactivo/a)", view.priority_filter.cget("values"))
        view.type_filter.set(f"{type_name} (inactivo/a)")
        self.apply(view)
        self.assertEqual(self.codes(view), [f"{self.prefix}-I"])
        view.type_filter.set(ALL_TYPES)
        view.priority_filter.set(f"{priority_name} (inactivo/a)")
        self.apply(view)
        self.assertEqual(self.codes(view), [f"{self.prefix}-I"])

    def test_clear_filters_resets_everything(self):
        view = self.open_query()
        name = db_all("SELECT nombre FROM tipos_incidente WHERE nombre NOT LIKE 'TIT-%' "
                      "ORDER BY id_tipo_incidente LIMIT 1")[0][0]
        view.type_filter.set(name)
        view.priority_filter.set(db_all("SELECT nombre FROM prioridades ORDER BY id_prioridad "
                                        "LIMIT 1")[0][0])
        view.state_filter.set(db_all("SELECT nombre FROM estados_incidente LIMIT 1")[0][0])
        self.apply(view, "INC", "2020-01-01", "2020-01-02")
        self.assertEqual(self.codes(view), [])
        view.clear_button.invoke()
        view.update()
        self.assertEqual(view.search_entry.get(), "")
        self.assertEqual(view.date_from_entry.get(), "")
        self.assertEqual(view.date_to_entry.get(), "")
        self.assertEqual(
            (view.type_filter.get(), view.priority_filter.get(), view.state_filter.get()),
            (ALL_TYPES, ALL_PRIORITIES, ALL_STATES))
        self.assertEqual(self.codes(view), self.sql_codes())

    def test_refresh_keeps_filters_and_rereads_mysql(self):
        incident_id = self.insert_incident("R", direccion="Calle original")
        view = self.open_query()
        self.apply(view, self.prefix)
        code = f"{self.prefix}-R"
        self.select(view, code)
        self.assertEqual(self.detail(view)["address"], "Calle original")
        db_exec("UPDATE incidentes SET direccion = 'Calle cambiada' WHERE id_incidente = %s",
                (incident_id,))
        view.refresh_button.invoke()
        view.update()
        self.assertEqual(view.search_entry.get(), self.prefix)
        self.assertEqual(self.codes(view), [code])
        self.assertEqual(self.rows(view)[0][5], "Calle cambiada")
        self.assertEqual(self.detail(view)["address"], "Calle cambiada")
        self.assertEqual(view.tree.selection(), (str(incident_id),))

    def test_selection_shows_full_read_only_detail(self):
        view = self.open_query()
        row = db_all(
            "SELECT i.codigo, i.direccion, i.descripcion, i.motivo_cancelacion, "
            "i.fecha_cancelacion, t.nombre, p.nombre, p.nivel, e.nombre, u.nombre_usuario "
            "FROM incidentes i JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente "
            "JOIN prioridades p ON p.id_prioridad = i.id_prioridad "
            "JOIN estados_incidente e ON e.id_estado_incidente = i.id_estado_incidente "
            "JOIN usuarios u ON u.id_usuario = i.id_usuario_registro "
            "WHERE e.codigo = 'CANCELADO' AND i.codigo NOT LIKE 'TIN-%' LIMIT 1")[0]
        self.select(view, row[0])
        shown = self.detail(view)
        self.assertEqual(shown["code"], row[0])
        self.assertEqual(shown["cancel_reason"], row[3])
        self.assertEqual(shown["cancelled_at"], row[4].strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(shown["type"], row[5])
        self.assertEqual(shown["priority"], f"{row[6]} (nivel {row[7]})")
        self.assertEqual(shown["state"], row[8])
        self.assertEqual(shown["registered_by"], row[9])
        self.assertEqual(shown["address"], row[1])
        self.assertEqual(shown["description"], row[2])
        self.assertEqual(shown["closed_at"], EMPTY)
        self.assertEqual(len(shown), 18)

    def test_null_fields_show_dash(self):
        self.insert_incident("N")
        view = self.open_query()
        self.select(view, f"{self.prefix}-N")
        shown = self.detail(view)
        for key in ("reference", "latitude", "longitude", "assigned_at",
                    "attention_started_at", "controlled_at", "closed_at",
                    "cancelled_at", "cancel_reason"):
            self.assertEqual(shown[key], EMPTY, key)
        self.assertEqual(shown["code"], f"{self.prefix}-N")

    def test_double_click_and_detail_button(self):
        view = self.open_query()
        code = self.sql_codes()[0]
        iid = self.select(view, code)
        view.detail_values["code"].configure(text="x")
        view.detail_button.invoke()
        self.assertEqual(self.detail(view)["code"], code)
        view.detail_values["code"].configure(text="x")
        x, y, width, height = view.tree.bbox(iid)
        for _ in range(2):
            view.tree.event_generate("<ButtonPress-1>", x=x + 5, y=y + 5)
            view.tree.event_generate("<ButtonRelease-1>", x=x + 5, y=y + 5)
        view.update()
        self.assertEqual(self.detail(view)["code"], code)
        self.assertEqual(view.tree.selection(), (iid,))

    def test_detail_button_without_selection(self):
        view = self.open_query()
        view.detail_button.invoke()
        self.assertIn("Seleccione un incidente", view.message_label.cget("text"))
        self.assertEqual(self.detail(view)["code"], EMPTY)

    def test_deleted_incident_reports_not_found(self):
        incident_id = self.insert_incident("D")
        view = self.open_query()
        code = f"{self.prefix}-D"
        self.select(view, code)
        db_exec("DELETE FROM incidentes WHERE id_incidente = %s", (incident_id,))
        view.detail_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "El incidente ya no existe.")
        self.assertNotIn(code, self.codes(view))
        self.assertEqual(self.detail(view)["code"], EMPTY)

    def test_no_write_controls_and_no_write_calls(self):
        view = self.open_query("admin.demo")
        buttons = {w.cget("text") for w in _all_widgets(view) if isinstance(w, ctk.CTkButton)}
        self.assertEqual(buttons, {
            "Aplicar filtros", "Quitar filtros", "Refrescar", "Ver detalle", "Regresar",
        })
        entries = [w for w in _all_widgets(view) if isinstance(w, ctk.CTkEntry)]
        self.assertEqual(set(entries), {
            view.search_entry, view.date_from_entry, view.date_to_entry,
        })
        self.assertEqual([w for w in _all_widgets(view) if isinstance(w, ctk.CTkTextbox)], [])
        detail_parent = view.detail_values["code"].master
        for widget in _all_widgets(detail_parent):
            self.assertNotIsInstance(widget, (ctk.CTkEntry, ctk.CTkTextbox, ctk.CTkComboBox))
        names = ("create_incident", "update_incident", "cancel_incident")
        with mock.patch.multiple(incident_controller, **{n: mock.DEFAULT for n in names}) as spies:
            self.apply(view, "x", "2026-01-01", "2026-12-31")
            view.clear_button.invoke()
            self.select(view, self.sql_codes()[0])
            view.detail_button.invoke()
            view.refresh_button.invoke()
            view.clear_button.invoke()
            view.update()
            for spy in spies.values():
                spy.assert_not_called()

    def test_wildcards_and_injection_do_not_widen_results(self):
        view = self.open_query()
        total = len(self.sql_codes())
        for text in ("' OR 1=1 --", "%", "_", "\\"):
            self.apply(view, text)
            # Oráculo de coincidencia literal independiente del LIKE de la aplicación
            expected = self.sql_codes(
                "LOCATE(LOWER(%s), LOWER(codigo)) > 0 OR "
                "LOCATE(LOWER(%s), LOWER(COALESCE(direccion, ''))) > 0 OR "
                "LOCATE(LOWER(%s), LOWER(COALESCE(descripcion, ''))) > 0",
                (text, text, text))
            self.assertEqual(self.codes(view), expected, text)
            self.assertLessEqual(len(self.codes(view)), total)
        self.assertEqual(db_all("SELECT COUNT(*) FROM incidentes "
                                f"WHERE {SEED_FILTER}")[0][0], total)

    def test_service_error_shows_generic_message(self):
        view = self.open_query()
        before = self.codes(view)
        with mock.patch.object(
            incident_controller, "filter_incidents",
            side_effect=IncidentServiceError("SELECT secreto Traceback"),
        ), self.assertLogs(incident_query_view.logger, "ERROR"):
            self.apply(view)
        text = view.message_label.cget("text")
        self.assertEqual(text, "Ocurrió un problema interno. Intente de nuevo.")
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.codes(view), before)

    def test_denied_shows_permission_message(self):
        view = self.open_query()
        with mock.patch.object(
            incident_controller, "filter_incidents", side_effect=AuthorizationDenied("x"),
        ):
            self.apply(view)
        self.assertEqual(view.message_label.cget("text"),
                         "No tiene permiso para consultar incidentes.")
        with mock.patch.object(
            incident_controller, "get_incident", side_effect=AuthorizationDenied("x"),
        ):
            self.select(view, self.sql_codes()[0])
        self.assertEqual(view.message_label.cget("text"),
                         "No tiene permiso para consultar incidentes.")

    def test_size_and_source(self):
        view = self.open_query()
        view.update_idletasks()
        self.assertLessEqual(view.winfo_reqwidth(), 1100)
        self.assertLessEqual(view.winfo_reqheight(), 650)
        for forbidden in ("models", "config.database", "mysql", "bcrypt", "ADMINISTRADOR", "OPERADOR",
                          "CONSULTA", "Crear", "Guardar", "Cambiar estado", "Despachar",
                          "Cancelar incidente", "create_incident", "update_incident",
                          "cancel_incident"):
            self.assertNotIn(forbidden, SOURCE)
        imports = re.findall(r"^(?:from|import) (\S+)", SOURCE, re.MULTILINE)
        self.assertEqual(
            sorted(imports),
            sorted(["logging", "tkinter", "customtkinter", "controllers",
                    "controllers.access_controller", "controllers.incident_controller"]))

    def test_main_view_opens_query_for_every_role(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            user = _login(username)
            main = MainView(user)
            self.windows.append(main)
            main.update()
            self.assertEqual(main.incidents_button.cget("text"), "Incidentes")
            self.assertEqual(main.query_button.cget("text"), "Consultar incidentes")
            self.assertIsNot(main.query_button, main.incidents_button)
            main.query_button.invoke()
            main.update()
            first = main.incident_query_view
            self.assertIsInstance(first, IncidentQueryView)
            main.query_button.invoke()
            self.assertIs(main.incident_query_view, first)
            first.back_button.invoke()
            main.update()
            self.assertFalse(first.winfo_exists())
            main.destroy()
            self.windows.remove(main)
            session.end_session()

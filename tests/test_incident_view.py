import unittest
from pathlib import Path
from unittest import mock

import customtkinter as ctk

from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.incident_controller import IncidentServiceError
from tests._incident_support import IncidentTestBase, db_all
from utils import session
from views import incident_view
from views.incident_view import (
    ALL_PRIORITIES, ALL_STATES, ALL_TYPES, PRIORITY_PLACEHOLDER, TYPE_PLACEHOLDER,
    IncidentView,
)
from views.main_view import MainView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(incident_view.__file__).read_text(encoding="utf-8")
COLUMN_TITLES = ["Código", "Fecha", "Tipo", "Prioridad", "Estado", "Dirección", "Descripción"]
SEED_PREFIX = "INC-2026"


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


class IncidentViewTest(IncidentTestBase):
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

    def open_incidents(self, username="operador.central"):
        _login(username)
        view = IncidentView(self.root)
        view.update()
        return view

    def code(self, suffix="A"):
        return f"{self.prefix}-{suffix}"

    def type_name(self, type_id):
        return db_all("SELECT nombre FROM tipos_incidente WHERE id_tipo_incidente = %s",
                      (type_id,))[0][0]

    def priority_name(self, priority_id):
        return db_all("SELECT nombre FROM prioridades WHERE id_prioridad = %s",
                      (priority_id,))[0][0]

    def fill(self, view, suffix="A", **overrides):
        values = {
            "code": self.code(suffix), "type": self.type_name(self.seed_type),
            "priority": self.priority_name(self.seed_priority), "address": "Calle de prueba 1",
            "reference": "Frente al parque", "latitude": "", "longitude": "",
            "affected": "", "description": "Incendio de prueba",
        }
        values.update(overrides)
        _set(view.code_entry, values["code"])
        view.type_combo.set(values["type"])
        view.priority_combo.set(values["priority"])
        _set(view.address_entry, values["address"])
        _set(view.reference_entry, values["reference"])
        _set(view.latitude_entry, values["latitude"])
        _set(view.longitude_entry, values["longitude"])
        _set(view.affected_entry, values["affected"])
        view.description_text.delete("1.0", "end")
        view.description_text.insert("1.0", values["description"])
        return values["code"]

    def create(self, view, suffix="A", **kwargs):
        code = self.fill(view, suffix, **kwargs)
        view.create_button.invoke()
        view.update()
        return code

    def rows(self, view):
        return [view.tree.item(i, "values") for i in view.tree.get_children()]

    def row_for(self, view, code):
        for row in self.rows(view):
            if row[0] == code:
                return row
        return None

    def seed_rows(self, view):
        return [r for r in self.rows(view) if r[0].startswith(SEED_PREFIX)]

    def select(self, view, code):
        for iid in view.tree.get_children():
            if view.tree.item(iid, "values")[0] == code:
                view.tree.selection_set(iid)
                view.update()
                return iid
        self.fail(f"No se encontró {code} en la tabla")

    def search(self, view, text):
        _set(view.search_entry, text)
        view.search_button.invoke()
        view.update()

    def incident_id(self, code):
        return db_all("SELECT id_incidente FROM incidentes WHERE codigo = %s", (code,))[0][0]

    def button_state(self, button):
        return str(button.cget("state"))

    def description(self, view):
        return view.description_text.get("1.0", "end-1c")

    def test_opening_lists_seed_incidents_for_every_role(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            with self.subTest(username=username):
                view = self.open_incidents(username)
                self.assertEqual(
                    [view.tree.heading(c, "text") for c in view.tree["columns"]], COLUMN_TITLES)
                self.assertEqual(len(self.seed_rows(view)), 8)
                row = self.row_for(view, "INC-2026-001")
                self.assertEqual(row[2:5], ("INCENDIO", "MEDIA", "Reportado"))
                self.assertRegex(row[1], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
                view.close()
                session.end_session()

    def test_window_fits_screen(self):
        view = self.open_incidents("admin.demo")
        view.update()
        self.assertLessEqual(view.winfo_reqwidth(), 1300)
        self.assertLessEqual(view.winfo_reqheight(), 700)

    def test_description_is_shortened_only_in_table(self):
        long_text = "Descripción larga " * 10
        self.insert_incident("L", descripcion=long_text)
        view = self.open_incidents()
        row = self.row_for(view, self.code("L"))
        self.assertTrue(row[6].endswith("…"))
        self.select(view, self.code("L"))
        self.assertEqual(self.description(view), long_text)

    def test_search_by_code_description_and_address(self):
        view = self.open_incidents()
        self.search(view, "INC-2026-003")
        self.assertEqual([r[0] for r in self.rows(view)], ["INC-2026-003"])
        self.search(view, "taller")
        self.assertIn("INC-2026-005", [r[0] for r in self.rows(view)])
        self.search(view, "Boulevard Los")
        self.assertEqual([r[0] for r in self.rows(view)], ["INC-2026-002"])
        # Enter sobre el entry interno también busca
        _set(view.search_entry, "INC-2026-004")
        view.search_entry.focus_force()
        view.search_entry._entry.event_generate("<Return>")
        view.update()
        self.assertEqual([r[0] for r in self.rows(view)], ["INC-2026-004"])

    def test_filters_and_clear(self):
        view = self.open_incidents()
        self.assertEqual(view.type_filter.get(), ALL_TYPES)
        self.assertEqual(view.priority_filter.get(), ALL_PRIORITIES)
        self.assertEqual(view.state_filter.get(), ALL_STATES)
        view.type_filter.set("INCENDIO")
        view.refresh_list()
        view.update()
        self.assertEqual(sorted(r[0] for r in self.seed_rows(view)),
                         ["INC-2026-001", "INC-2026-005"])
        view.priority_filter.set("ALTA")
        view.refresh_list()
        self.assertEqual([r[0] for r in self.seed_rows(view)], ["INC-2026-005"])
        view.state_filter.set("Reportado")
        view.refresh_list()
        self.assertEqual(self.seed_rows(view), [])
        view.state_filter.set("Cerrado")
        view.refresh_list()
        self.assertEqual([r[0] for r in self.seed_rows(view)], ["INC-2026-005"])
        self.search(view, "INC-2026-001")
        self.assertEqual(self.seed_rows(view), [])
        view.clear_search_button.invoke()
        view.update()
        self.assertEqual(len(self.seed_rows(view)), 8)
        self.assertEqual(view.search_entry.get(), "")
        self.assertEqual(view.type_filter.get(), ALL_TYPES)
        # Filtro por estado: el combo de filtro llama a refrescar
        view.state_filter.set("Cancelado")
        view.refresh_list()
        self.assertEqual(sorted(r[0] for r in self.seed_rows(view)),
                         ["INC-2026-006", "INC-2026-008"])

    def test_create_as_operator(self):
        view = self.open_incidents("operador.central")
        self.assertEqual(view.state_label.cget("text"), "Estado inicial: Reportado")
        self.assertEqual(view.registrar_label.cget("text"), "Registrado por: operador.central")
        code = self.create(
            view, latitude="14.6", longitude="-90.5", affected="3",
            description="Humo en bodega",
        )
        self.assertIn("creado correctamente", view.message_label.cget("text"))
        self.assertIsNotNone(self.row_for(view, code))
        self.assertEqual(view.tree.selection(), ())
        self.assertIsNone(view.selected_id)
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(view.type_combo.get(), TYPE_PLACEHOLDER)
        self.assertEqual(view.priority_combo.get(), PRIORITY_PLACEHOLDER)
        self.assertEqual(self.description(view), "")
        incident_id = self.incident_id(code)
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        self.assertEqual(
            [a[1] for a in self.audit_rows(incident_id)], ["CREAR"])
        self.assertEqual(self.audit_rows(incident_id)[0][0], self.user_id("operador.central"))
        self.assertEqual(self.row_for(view, code)[4], "Reportado")

    def test_create_keeps_search_and_filters(self):
        view = self.open_incidents("admin.demo")
        view.type_filter.set("INCENDIO")
        _set(view.search_entry, "INC-2026")
        self.create(view)
        self.assertEqual(view.search_entry.get(), "INC-2026")
        self.assertEqual(view.type_filter.get(), "INCENDIO")
        self.assertIsNone(self.row_for(view, self.code()))
        self.assertEqual(self.count_prefix(), 1)

    def test_duplicate_code(self):
        view = self.open_incidents()
        self.create(view)
        self.fill(view)
        view.create_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "Ya existe un incidente con ese código.")
        self.assertEqual(self.count_prefix(), 1)

    def test_invalid_input_is_rejected_and_nothing_saved(self):
        view = self.open_incidents()
        cases = [
            ({"latitude": "14.6"}, "latitud y longitud"),
            ({"latitude": "95", "longitude": "10"}, "latitud"),
            ({"latitude": "10", "longitude": "200"}, "longitud"),
            ({"affected": "-1"}, "personas afectadas"),
            ({"affected": "abc"}, "personas afectadas"),
            ({"description": "   "}, "descripción"),
            ({"address": ""}, "dirección"),
            ({"type": TYPE_PLACEHOLDER}, "tipo"),
            ({"priority": PRIORITY_PLACEHOLDER}, "prioridad"),
        ]
        for overrides, fragment in cases:
            with self.subTest(overrides=overrides):
                self.create(view, **overrides)
                text = view.message_label.cget("text").lower()
                self.assertIn(fragment, text)
                self.assertNotIn("Traceback", text)
                self.assertEqual(self.count_prefix(), 0)

    def test_select_loads_form_with_read_only_fields(self):
        incident_id = self.insert_incident(
            "S", direccion="Av. Reforma 10", descripcion="Texto completo")
        view = self.open_incidents("operador.central")
        self.select(view, self.code("S"))
        self.assertEqual(view.selected_id, incident_id)
        self.assertEqual(view.code_entry.get(), self.code("S"))
        self.assertEqual(str(view.code_entry.cget("state")), "disabled")
        self.assertEqual(view.type_combo.get(), self.type_name(self.seed_type))
        self.assertEqual(view.priority_combo.get(), self.priority_name(self.seed_priority))
        self.assertEqual(view.address_entry.get(), "Av. Reforma 10")
        self.assertEqual(self.description(view), "Texto completo")
        self.assertEqual(view.state_label.cget("text"), "Estado actual: Reportado")
        self.assertEqual(view.registrar_label.cget("text"), "Registrado por: admin.demo")
        self.assertTrue(view.reported_label.cget("text").startswith("Fecha de reporte: 20"))
        self.assertEqual(self.button_state(view.create_button), "disabled")
        self.assertEqual(self.button_state(view.save_button), "normal")
        # Ningún widget editable está ligado al estado
        for name in ("state_combo", "state_entry", "registrar_entry"):
            self.assertFalse(hasattr(view, name))

    def test_edit_persists_allowed_fields_only(self):
        incident_id = self.insert_incident("E")
        before = self.fixed_row(incident_id)
        view = self.open_incidents("operador.central")
        self.select(view, self.code("E"))
        _set(view.address_entry, "Nueva dirección 5")
        _set(view.reference_entry, "Esquina")
        _set(view.latitude_entry, "14.5")
        _set(view.longitude_entry, "-90.4")
        _set(view.affected_entry, "7")
        view.description_text.delete("1.0", "end")
        view.description_text.insert("1.0", "Descripción editada")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizado correctamente", view.message_label.cget("text"))
        row = self.row(incident_id)[0]
        self.assertEqual(self.fixed_row(incident_id), before)
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        self.assertIn("Nueva dirección 5", row)
        self.assertIn("Descripción editada", row)
        self.assertEqual(self.row_for(view, self.code("E"))[5], "Nueva dirección 5")
        self.assertEqual(view.selected_id, incident_id)
        self.assertEqual([a[1] for a in self.audit_rows(incident_id)], ["MODIFICAR"])

    def test_edit_validation_error_keeps_selection(self):
        incident_id = self.insert_incident("V")
        view = self.open_incidents()
        self.select(view, self.code("V"))
        _set(view.latitude_entry, "10")
        view.save_button.invoke()
        view.update()
        self.assertIn("latitud y longitud", view.message_label.cget("text"))
        self.assertEqual(view.selected_id, incident_id)
        self.assertEqual(self.audit_rows(incident_id), [])

    def test_selectors_by_mode(self):
        inactive_type = self.temp_type(active=False)
        inactive_priority = self.temp_priority(active=False)
        other_inactive_type = self.temp_type(active=False)
        self.insert_incident("I", tipo=inactive_type, prioridad=inactive_priority)
        view = self.open_incidents()
        inactive_type_name = self.type_name(inactive_type)
        inactive_priority_name = self.priority_name(inactive_priority)
        create_types = view.type_combo.cget("values")
        create_priorities = view.priority_combo.cget("values")
        self.assertFalse([v for v in create_types if "(inactiva)" in v])
        self.assertFalse([v for v in create_priorities if "(inactiva)" in v])
        self.select(view, self.code("I"))
        self.assertEqual(view.type_combo.get(), f"{inactive_type_name} (inactiva)")
        self.assertEqual(view.priority_combo.get(), f"{inactive_priority_name} (inactiva)")
        inactive_options = [v for v in view.type_combo.cget("values") if "(inactiva)" in v]
        self.assertEqual(inactive_options, [f"{inactive_type_name} (inactiva)"])
        self.assertNotIn(f"{self.type_name(other_inactive_type)} (inactiva)",
                         view.type_combo.cget("values"))
        self.assertEqual(
            [v for v in view.priority_combo.cget("values") if "(inactiva)" in v],
            [f"{inactive_priority_name} (inactiva)"])
        # Los filtros conservan todo el catálogo
        self.assertIn(f"{inactive_type_name} (inactiva)", view.type_filter.cget("values"))
        self.assertIn(f"{self.type_name(other_inactive_type)} (inactiva)",
                      view.type_filter.cget("values"))
        view.clear_button.invoke()
        view.update()
        self.assertFalse([v for v in view.type_combo.cget("values") if "(inactiva)" in v])
        # Editar conservando lo inactivo funciona
        self.select(view, self.code("I"))
        _set(view.address_entry, "Otra dirección")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizado correctamente", view.message_label.cget("text"))

    def test_terminal_incident_is_view_only(self):
        for state in ("CERRADO", "CANCELADO"):
            with self.subTest(state=state):
                suffix = state[:3]
                incident_id = self.insert_incident(suffix, state_code=state)
                view = self.open_incidents("operador.central")
                self.select(view, self.code(suffix))
                self.assertEqual(view.selected_id, incident_id)
                self.assertEqual(view.address_entry.get(), "Dirección temporal")
                self.assertEqual(self.button_state(view.save_button), "disabled")
                self.assertEqual(self.button_state(view.cancel_button), "disabled")
                self.assertEqual(self.button_state(view.create_button), "disabled")
                self.assertIn("cerrado o cancelado", view.hint_label.cget("text"))
                view.close()

    def test_cancel_button_only_for_reported(self):
        for state in ("ASIGNADO", "EN_ATENCION", "CONTROLADO", "CERRADO"):
            self.insert_incident(state[:4], state_code=state)
        self.insert_incident("REP")
        view = self.open_incidents("operador.central")
        for state in ("ASIGNADO", "EN_ATENCION", "CONTROLADO", "CERRADO"):
            self.select(view, self.code(state[:4]))
            self.assertEqual(self.button_state(view.cancel_button), "disabled", state)
        for state in ("ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            self.select(view, self.code(state[:4]))
            self.assertEqual(self.button_state(view.save_button), "normal", state)
        self.select(view, self.code("REP"))
        self.assertEqual(self.button_state(view.cancel_button), "normal")

    def test_cancel_flow(self):
        incident_id = self.insert_incident("C")
        dispatch_id = self.add_dispatch(incident_id, "CREADO")
        view = self.open_incidents("operador.central")
        self.select(view, self.code("C"))
        view.confirm = mock.Mock(return_value=True)
        view.ask_reason = mock.Mock(return_value="Falsa alarma")
        view.cancel_button.invoke()
        view.update()
        self.assertIn("despachos en borrador",
                      view.ask_reason.call_args[0][0])
        self.assertIn("despachos en borrador", view.confirm.call_args[0][0])
        self.assertEqual(self.state_of(incident_id), "CANCELADO")
        self.assertEqual(view.state_label.cget("text"), "Estado actual: Cancelado")
        self.assertEqual(self.row_for(view, self.code("C"))[4], "Cancelado")
        row = db_all("SELECT motivo_cancelacion, fecha_cancelacion FROM incidentes "
                     "WHERE id_incidente = %s", (incident_id,))[0]
        self.assertEqual(row[0], "Falsa alarma")
        self.assertIsNotNone(row[1])
        self.assertIn("cancelado correctamente", view.message_label.cget("text"))
        self.assertEqual(self.button_state(view.cancel_button), "disabled")
        self.assertEqual(self.button_state(view.save_button), "disabled")
        self.assertIn("CAMBIO_ESTADO", [a[1] for a in self.audit_rows(incident_id)])
        self.assertEqual(self.dispatch_state(dispatch_id), "CANCELADO")

    def test_cancel_without_reason_changes_nothing(self):
        incident_id = self.insert_incident("N")
        view = self.open_incidents("operador.central")
        self.select(view, self.code("N"))
        view.confirm = mock.Mock(return_value=True)
        for reason in (None, ""):
            view.ask_reason = mock.Mock(return_value=reason)
            view.cancel_button.invoke()
            view.update()
            self.assertIn("motivo", view.message_label.cget("text"))
            self.assertEqual(self.state_of(incident_id), "REPORTADO")
        # Solo espacios: lo rechaza el controlador
        view.ask_reason = mock.Mock(return_value="   ")
        view.cancel_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "El motivo de cancelación es obligatorio.")
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        # Si no confirma, tampoco cambia
        view.confirm = mock.Mock(return_value=False)
        view.ask_reason = mock.Mock(return_value="Motivo")
        view.cancel_button.invoke()
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        self.assertEqual(self.audit_rows(incident_id), [])

    def test_filtering_keeps_unsaved_edits(self):
        self.insert_incident("K")
        view = self.open_incidents("operador.central")
        self.select(view, self.code("K"))
        _set(view.address_entry, "Texto sin guardar")
        self.search(view, self.prefix)
        view.update()
        view.update_idletasks()
        self.assertEqual(view.address_entry.get(), "Texto sin guardar")
        view.type_filter.set(self.type_name(self.seed_type))
        view.refresh_list()
        view.update()
        self.assertEqual(view.address_entry.get(), "Texto sin guardar")
        self.assertEqual(view.tree.selection(), (str(view.selected_id),))
        # Otra fila distinta sí se carga
        self.insert_incident("K2", direccion="Otra fila")
        self.search(view, self.prefix)
        self.select(view, self.code("K2"))
        self.assertEqual(view.address_entry.get(), "Otra fila")

    def test_ask_reason_restores_modal_grab(self):
        view = self.open_incidents("operador.central")
        roots_before = len(self.windows)
        for answer in ("Motivo", None):
            dialog = mock.Mock()
            dialog.get_input.return_value = answer
            with mock.patch.object(incident_view.ctk, "CTkInputDialog", return_value=dialog),                     mock.patch.object(view, "grab_set", wraps=view.grab_set) as spy:
                self.assertEqual(view.ask_reason("Motivo?"), answer)
            spy.assert_called_once()
        self.assertEqual(len(self.windows), roots_before)
        self.assertTrue(view.winfo_exists())

    def test_clear_resets_form(self):
        self.insert_incident("R")
        view = self.open_incidents()
        self.select(view, self.code("R"))
        view.clear_button.invoke()
        view.update()
        self.assertIsNone(view.selected_id)
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(str(view.code_entry.cget("state")), "normal")
        self.assertEqual(view.state_label.cget("text"), "Estado inicial: Reportado")
        self.assertEqual(self.button_state(view.create_button), "normal")
        self.assertEqual(self.button_state(view.save_button), "disabled")
        self.assertEqual(self.button_state(view.cancel_button), "disabled")

    def test_technical_error_shows_generic_message(self):
        view = self.open_incidents()
        with mock.patch.object(
            incident_view.incident_controller, "filter_incidents",
            side_effect=IncidentServiceError("SELECT * FROM incidentes"),
        ), self.assertLogs(incident_view.logger, level="ERROR"):
            view.search_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, "Ocurrió un problema interno. Intente de nuevo.")
        self.assertNotIn("SELECT", text)
        self.assertNotIn("Traceback", text)

    def test_authorization_denied_shows_permission_message(self):
        view = self.open_incidents()
        self.fill(view)
        with mock.patch.object(
            incident_view.incident_controller, "create_incident",
            side_effect=AuthorizationDenied("x"),
        ):
            view.create_button.invoke()
        self.assertEqual(view.message_label.cget("text"), "No tiene permiso para modificar incidentes.")
        with mock.patch.object(
            incident_view.incident_controller, "filter_incidents",
            side_effect=AuthorizationDenied("x"),
        ):
            view.search_button.invoke()
        self.assertEqual(view.message_label.cget("text"), "No tiene permiso para consultar incidentes.")

    def test_not_found_resets_and_refreshes(self):
        self.insert_incident("F")
        view = self.open_incidents()
        self.select(view, self.code("F"))
        # El incidente se elimina por fuera (solo harness) mientras la ventana está abierta
        self.run_cleanup()
        view.save_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "El incidente ya no existe.")
        self.assertIsNone(view.selected_id)
        self.assertIsNone(self.row_for(view, self.code("F")))

    def test_read_only_mode_for_consulta(self):
        self.insert_incident("Q")
        view = self.open_incidents("consulta.sur")
        self.assertFalse(view.can_write)
        self.assertEqual(view.read_only_label.cget("text"), "Modo solo lectura")
        self.assertEqual(view.read_only_label.winfo_manager(), "pack")
        for button in (view.create_button, view.save_button, view.cancel_button):
            self.assertEqual(self.button_state(button), "disabled")
        for widget in (
            view.code_entry, view.address_entry, view.reference_entry, view.latitude_entry,
            view.longitude_entry, view.affected_entry, view.description_text,
            view.type_combo, view.priority_combo,
        ):
            self.assertEqual(str(widget.cget("state")), "disabled")
        # Búsqueda, filtros y detalle siguen funcionando
        self.search(view, "INC-2026-001")
        self.assertEqual([r[0] for r in self.rows(view)], ["INC-2026-001"])
        view.clear_search()
        view.type_filter.set("RESCATE")
        view.refresh_list()
        self.assertEqual([r[0] for r in self.seed_rows(view)], ["INC-2026-006"])
        view.clear_search()
        self.select(view, self.code("Q"))
        self.assertEqual(view.address_entry.get(), "Dirección temporal")
        for button in (view.create_button, view.save_button, view.cancel_button):
            self.assertEqual(self.button_state(button), "disabled")

    def test_forced_writes_as_consulta_are_denied(self):
        incident_id = self.insert_incident("W")
        view = self.open_incidents("consulta.sur")
        view.create()
        self.assertEqual(view.message_label.cget("text"), "No tiene permiso para modificar incidentes.")
        self.select(view, self.code("W"))
        view.message_label.configure(text="")
        view.save()
        self.assertEqual(view.message_label.cget("text"), "No tiene permiso para modificar incidentes.")
        view.message_label.configure(text="")
        view.confirm = mock.Mock(return_value=True)
        view.ask_reason = mock.Mock(return_value="Intento")
        view.cancel_incident()
        self.assertEqual(view.message_label.cget("text"), "No tiene permiso para modificar incidentes.")
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        self.assertEqual(self.audit_rows(incident_id), [])
        self.assertEqual(self.count_prefix(), 1)

    def test_main_view_opens_module_for_every_role(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                self.assertEqual(main.incidents_button.cget("text"), "Incidentes")
                main.incidents_button.invoke()
                main.update()
                first = main.incident_view
                self.assertTrue(first.winfo_exists())
                self.assertEqual(len(self.seed_rows(first)), 8)
                main.incidents_button.invoke()
                self.assertIs(main.incident_view, first)
                first.back_button.invoke()
                main.update()
                self.assertFalse(first.winfo_exists())
                main.destroy()
                session.end_session()

    def test_source_has_no_data_access_roles_or_state_controls(self):
        for forbidden in (
            "import mysql", "from mysql", "config.database", "from models", "import bcrypt",
            "ADMINISTRADOR", "OPERADOR", "CONSULTA", "cursor", "SELECT ",
            "delete_incident", "dispatch_controller", "reopen",
        ):
            self.assertNotIn(forbidden, SOURCE, forbidden)
        for text in ("Eliminar", "Despachar", "Asignar", "Reabrir", "Cerrar incidente", "Controlar"):
            self.assertNotIn(f'text="{text}', SOURCE)
        self.assertNotIn("state_combo", SOURCE)


if __name__ == "__main__":
    unittest.main()

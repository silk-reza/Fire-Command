import re
import unittest
from pathlib import Path
from unittest import mock

import customtkinter as ctk

from controllers import dispatch_controller
from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.dispatch_controller import DispatchServiceError
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from utils import session
from views import dispatch_view
from views.dispatch_view import (
    ALL_STATIONS, ALL_UNIT_TYPES, INCIDENT_PLACEHOLDER, MSG_DENIED_READ, MSG_DENIED_WRITE,
    MSG_INTERNAL, SHOW_ALL, SHOW_DRAFTS, DispatchView,
)

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(dispatch_view.__file__).read_text(encoding="utf-8")
COLUMN_TITLES = ["N.º", "Incidente", "Estado", "Unidades", "Creado por", "Observaciones"]
UNIT_TITLES = ["Código", "Nombre", "Tipo", "Estación", "Estado real", "Disponibilidad"]
AVAILABLE_TITLES = ["Código", "Nombre", "Tipo", "Estación", "Estado"]
BUTTONS = {
    "Nuevo borrador", "Guardar cambios", "Agregar unidad", "Quitar unidad",
    "Cancelar borrador", "Activar despacho", "Refrescar", "Marcar en ruta",
    "Marcar en escena", "Iniciar regreso", "Marcar disponible", "Limpiar selección", "Regresar",
    "Quitar filtros", "Buscar", "Limpiar filtros",
}
ACTION_BUTTONS = ("en_route_button", "on_scene_button", "return_button", "available_button")
WRITE_BUTTONS = (
    "create_button", "save_button", "add_button", "remove_button", "cancel_button",
    "activate_button",
)


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


class DispatchViewTest(DispatchTestBase):
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

    def open_view(self, username="operador.central"):
        _login(username)
        view = DispatchView(self.root)
        view.update()
        return view

    def state(self, button):
        return str(button.cget("state"))

    def rows(self, view):
        return [view.tree.item(i, "values") for i in view.tree.get_children()]

    def seed_rows(self, view):
        return [r for r in self.rows(view) if str(r[1]).startswith("INC-2026")]

    def select_dispatch(self, view, dispatch_id):
        view.tree.selection_set(str(dispatch_id))
        view.update()

    def obs(self, view):
        return view.observations_text.get("1.0", "end-1c")

    def unit_codes(self, tree):
        return [tree.item(i, "values")[0] for i in tree.get_children()]

    def incident_text(self, code):
        return f"{code} — Dirección temporal (Reportado)"

    def new_draft(self, view, suffix="A", observations=""):
        """Crea un borrador desde la pantalla para un incidente temporal ya insertado."""
        view.load_incident_options()
        view.incident_combo.set(self.incident_text(f"{self.prefix}-{suffix}"))
        view.observations_text.delete("1.0", "end")
        view.observations_text.insert("1.0", observations)
        view.create_button.invoke()
        view.update()
        return view.selected_id

    def pick_available(self, view, unit_id):
        view.refresh_available()
        view.available_tree.selection_set(str(unit_id))
        view.update()

    def unit_code(self, n):
        return f"{self.unit_prefix}-{n}"

    def unit_state_name(self, state_code):
        return db_all("SELECT nombre FROM estados_unidad WHERE codigo = %s",
                      (state_code,))[0][0]

    def test_opens_for_every_role_and_lists_seed(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            with self.subTest(username=username):
                view = self.open_view(username)
                self.assertEqual(
                    [view.tree.heading(c, "text") for c in view.tree["columns"]], COLUMN_TITLES)
                self.assertEqual(
                    [view.units_tree.heading(c, "text") for c in view.units_tree["columns"]],
                    UNIT_TITLES)
                self.assertEqual(
                    [view.available_tree.heading(c, "text")
                     for c in view.available_tree["columns"]], AVAILABLE_TITLES)
                self.assertEqual(len(self.seed_rows(view)), 9)
                first = view.tree.item("1", "values")
                self.assertEqual(
                    (str(first[0]), first[1], first[2], str(first[3])),
                    ("1", "INC-2026-001", "Creado", "1"))
                view.close()
                session.end_session()

    def test_window_fits_screen(self):
        view = self.open_view("admin.demo")
        view.update()
        self.assertLessEqual(view.winfo_reqwidth(), 1150)
        self.assertLessEqual(view.winfo_reqheight(), 650)

    def test_seed_non_draft_dispatches_are_read_only(self):
        view = self.open_view("admin.demo")
        for dispatch_id in range(2, 10):
            self.select_dispatch(view, dispatch_id)
            self.assertEqual(view.selected_id, dispatch_id)
            self.assertNotEqual(view.selected_state_code, "CREADO")
            for name in WRITE_BUTTONS:
                self.assertEqual(self.state(getattr(view, name)), "disabled", name)
            self.assertEqual(self.state(view.observations_text), "disabled")
            self.assertNotEqual(view.hint_label.cget("text"), "")
            self.assertNotEqual(self.state(view.refresh_button), "disabled")
        self.select_dispatch(view, 7)
        self.assertIn("Motivo de cancelación", view.cancel_label.cget("text"))
        self.assertIn("Cancelado", view.state_label.cget("text"))
        self.select_dispatch(view, 1)
        self.assertEqual(view.state_label.cget("text"), "Estado del despacho: Creado")
        for name in ("save_button", "add_button", "remove_button", "cancel_button"):
            self.assertEqual(self.state(getattr(view, name)), "normal", name)
        self.assertEqual(self.state(view.create_button), "disabled")

    def test_show_and_incident_filters(self):
        view = self.open_view("admin.demo")
        view.show_filter.set(SHOW_DRAFTS)
        view.refresh_list()
        self.assertEqual([str(r[0]) for r in self.seed_rows(view)], ["1"])
        view.show_filter.set(SHOW_ALL)
        view.incident_filter.set("INC-2026-005")
        view.refresh_list()
        self.assertEqual(sorted(str(r[0]) for r in self.rows(view)), ["5", "6"])
        view.clear_filters_button.invoke()
        view.update()
        self.assertEqual(len(self.seed_rows(view)), 9)
        self.assertEqual(view.show_filter.get(), SHOW_ALL)

    def test_create_draft_as_operator(self):
        a = self.insert_incident("A")
        self.insert_incident("B", state_code="CERRADO")
        self.insert_incident("C", state_code="CANCELADO")
        d = self.insert_incident("D", state_code="ASIGNADO")
        before = self.fixed_row(a)
        view = self.open_view("operador.central")
        options = list(view.incident_combo.cget("values"))
        joined = "\n".join(options)
        self.assertIn(self.incident_text(f"{self.prefix}-A"), options)
        self.assertIn(f"{self.prefix}-D — Dirección temporal (Asignado)", options)
        self.assertNotIn(f"{self.prefix}-B", joined)
        self.assertNotIn(f"{self.prefix}-C", joined)

        # Sin elegir incidente no se crea nada
        view.create_button.invoke()
        view.update()
        self.assertEqual(view.incident_combo.get(), INCIDENT_PLACEHOLDER)
        self.assertEqual(view.message_label.cget("text"), "El incidente es obligatorio.")
        self.assertEqual(self.dispatch_rows(a), [])

        dispatch_id = self.new_draft(view, "A", "  Refuerzo de agua  ")
        self.assertIsNotNone(dispatch_id)
        self.assertIn("creado correctamente", view.message_label.cget("text"))
        self.assertEqual(view.state_label.cget("text"), "Estado del despacho: Creado")
        self.assertEqual(view.tree.selection(), (str(dispatch_id),))
        self.assertEqual(self.obs(view), "Refuerzo de agua")
        self.assertEqual(self.state(view.incident_combo), "disabled")
        self.assertEqual(self.state(view.create_button), "disabled")
        self.assertEqual(self.state(view.add_button), "normal")
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertIsNone(db_all(
            "SELECT fecha_despacho FROM despachos WHERE id_despacho = %s",
            (dispatch_id,))[0][0])
        self.assertEqual(self.fixed_row(a), before)
        self.assertEqual(self.state_of(a), "REPORTADO")
        audit = self.dispatch_audit(dispatch_id)
        self.assertEqual([x[1] for x in audit], ["CREAR"])
        self.assertEqual(audit[0][0], self.user_id("operador.central"))
        self.assertEqual(
            [str(v[0]) for v in self.rows(view) if v[1] == f"{self.prefix}-A"],
            [str(dispatch_id)])

        # Un segundo borrador del mismo incidente (refuerzo) es válido
        view.clear_button.invoke()
        view.update()
        second = self.new_draft(view, "A")
        self.assertNotEqual(second, dispatch_id)
        self.assertEqual(len(self.dispatch_rows(a)), 2)
        view.clear_button.invoke()
        view.update()
        view.load_incident_options()
        view.incident_combo.set(f"{self.prefix}-D — Dirección temporal (Asignado)")
        view.create_button.invoke()
        view.update()
        self.assertIsNotNone(view.selected_id)
        self.assertEqual(len(self.dispatch_rows(d)), 1)

    def test_add_unit_and_reuse_in_other_draft(self):
        a = self.insert_incident("A")
        b = self.insert_incident("B")
        unit_id = self.temp_unit(name="Alfa")
        unit_before = self.unit_row(unit_id)
        view = self.open_view("operador.central")
        first = self.new_draft(view, "A")
        self.assertIn(self.unit_code(1), self.unit_codes(view.available_tree))
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "Unidad agregada al borrador.")
        values = view.units_tree.item(str(unit_id), "values")
        self.assertEqual(values[0], self.unit_code(1))
        self.assertEqual(values[4], self.unit_state_name("DISPONIBLE"))
        self.assertEqual(values[5], "Disponible")
        self.assertNotIn(self.unit_code(1), self.unit_codes(view.available_tree))
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.dispatch_state(first), "CREADO")
        self.assertEqual(str(view.tree.item(str(first), "values")[3]), "1")
        self.assertEqual(self.state_of(a), "REPORTADO")

        # La misma unidad puede preseleccionarse en otro borrador
        view.clear_button.invoke()
        view.update()
        second = self.new_draft(view, "B")
        self.assertIn(self.unit_code(1), self.unit_codes(view.available_tree))
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        self.assertEqual(len(self.preselections(first)), 1)
        self.assertEqual(len(self.preselections(second)), 1)
        self.assertEqual(self.state_of(b), "REPORTADO")

    def test_duplicate_and_ineligible_are_reported(self):
        self.insert_incident("A")
        unit_id = self.temp_unit()
        broken = self.temp_unit(state_code="MANTENIMIENTO")
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A")
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        # Duplicado forzado: la fila no se ofrece, pero se inserta a mano en la tabla
        view.available_tree.insert("", "end", iid=str(unit_id), values=("x",) * 5)
        view.available_tree.selection_set(str(unit_id))
        view.add_button.invoke()
        view.update()
        self.assertEqual(
            view.message_label.cget("text"), "La unidad ya está preseleccionada en este despacho.")
        self.assertEqual(len(self.preselections(draft)), 1)
        # Inelegible forzada
        view.available_tree.insert("", "end", iid=str(broken), values=("y",) * 5)
        view.available_tree.selection_set(str(broken))
        view.add_button.invoke()
        view.update()
        text = view.message_label.cget("text")
        self.assertNotEqual(text, "")
        self.assertNotEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(self.preselections(draft)), 1)
        self.assertNotIn(self.unit_code(2), self.unit_codes(view.available_tree))
        # Sin selección se pide elegir una unidad
        view.available_tree.selection_remove(*view.available_tree.selection())
        view.add_button.invoke()
        view.update()
        self.assertIn("Seleccione una unidad", view.message_label.cget("text"))

    def test_remove_unit_with_confirmation(self):
        self.insert_incident("A")
        unit_id = self.temp_unit()
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A")
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        view.remove_button.invoke()
        view.update()
        self.assertIn("Seleccione una unidad", view.message_label.cget("text"))
        view.units_tree.selection_set(str(unit_id))
        with mock.patch.object(view, "confirm", return_value=False) as confirm:
            view.remove_button.invoke()
        self.assertTrue(confirm.called)
        self.assertEqual(len(self.preselections(draft)), 1)
        with mock.patch.object(view, "confirm", return_value=True):
            view.remove_button.invoke()
        view.update()
        self.assertEqual(self.preselections(draft), [])
        self.assertEqual(view.units_tree.get_children(), ())
        self.assertIn(self.unit_code(1), self.unit_codes(view.available_tree))
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")

    def test_stale_unit_stays_visible_with_real_state(self):
        self.insert_incident("A")
        unit_id = self.temp_unit()
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A")
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        self.set_unit_state(unit_id, "MANTENIMIENTO")
        view.refresh_button.invoke()
        view.update()
        values = view.units_tree.item(str(unit_id), "values")
        self.assertEqual(values[4], self.unit_state_name("MANTENIMIENTO"))
        self.assertEqual(values[5], "No disponible ahora")
        self.assertEqual(len(self.preselections(draft)), 1)
        self.assertNotIn(self.unit_code(1), self.unit_codes(view.available_tree))
        self.assertEqual(self.state(view.remove_button), "normal")

    def test_edit_observations(self):
        a = self.insert_incident("A")
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A", "Inicial")
        view.observations_text.delete("1.0", "end")
        view.observations_text.insert("1.0", "Nueva observación")
        view.save_button.invoke()
        view.update()
        self.assertIn("guardadas", view.message_label.cget("text"))
        self.assertEqual(db_all(
            "SELECT observaciones FROM despachos WHERE id_despacho = %s", (draft,))[0][0],
            "Nueva observación")
        self.assertIn("Nueva observación", view.tree.item(str(draft), "values")[5])
        # Sin cambios el guardado no altera nada
        before = self.dispatch_row(draft)
        view.save_button.invoke()
        view.update()
        self.assertEqual(self.dispatch_row(draft), before)
        self.assertEqual(self.state_of(a), "REPORTADO")
        # Vaciar las observaciones las deja en NULL
        view.observations_text.delete("1.0", "end")
        view.save_button.invoke()
        view.update()
        self.assertIsNone(db_all(
            "SELECT observaciones FROM despachos WHERE id_despacho = %s", (draft,))[0][0])

    def test_cancel_flow(self):
        a = self.insert_incident("A")
        unit_id = self.temp_unit()
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A")
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        incident_before = self.row(a)
        unit_before = self.unit_row(unit_id)

        for reason in (None, "", "   "):
            with mock.patch.object(view, "ask_reason", return_value=reason), \
                    mock.patch.object(view, "confirm", return_value=True):
                view.cancel_button.invoke()
            self.assertIn("motivo es obligatorio", view.message_label.cget("text"))
            self.assertEqual(self.dispatch_state(draft), "CREADO")
        with mock.patch.object(view, "ask_reason", return_value="x" * 251), \
                mock.patch.object(view, "confirm", return_value=True):
            view.cancel_button.invoke()
        self.assertIn("250", view.message_label.cget("text"))
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        prompts = []
        with mock.patch.object(view, "ask_reason", side_effect=lambda p: prompts.append(p) or "ok"), \
                mock.patch.object(view, "confirm", return_value=False) as confirm:
            view.cancel_button.invoke()
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        self.assertIn("no se modifican", confirm.call_args[0][0])
        self.assertIn("no se modifican", prompts[0])

        reason = "m" * 250
        with mock.patch.object(view, "ask_reason", return_value=reason), \
                mock.patch.object(view, "confirm", return_value=True):
            view.cancel_button.invoke()
        view.update()
        self.assertEqual(self.dispatch_state(draft), "CANCELADO")
        self.assertEqual(db_all(
            "SELECT motivo_cancelacion FROM despachos WHERE id_despacho = %s", (draft,))[0][0],
            reason)
        self.assertIn("Cancelado", view.state_label.cget("text"))
        self.assertIn(reason, view.cancel_label.cget("text"))
        for name in WRITE_BUTTONS:
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)
        self.assertEqual(self.state(view.observations_text), "disabled")
        self.assertEqual(self.row(a), incident_before)
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertIn("CAMBIO_ESTADO", [x[1] for x in self.dispatch_audit(draft)])
        # Tras cancelar la unidad vuelve a ofrecerse en otros borradores
        self.assertEqual(view.units_tree.get_children(), (str(unit_id),))

    def test_cancel_with_drafts_filter_resets_to_new_mode(self):
        self.insert_incident("A")
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A")
        view.show_filter.set(SHOW_DRAFTS)
        view.refresh_list()
        with mock.patch.object(view, "ask_reason", return_value="motivo"),                 mock.patch.object(view, "confirm", return_value=True):
            view.cancel_button.invoke()
        view.update()
        self.assertEqual(self.dispatch_state(draft), "CANCELADO")
        self.assertIn("cancelado correctamente", view.message_label.cget("text"))
        self.assertIsNone(view.selected_id)
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(self.state(view.create_button), "normal")

    def draft_with_unit(self, view, suffix="A"):
        incident_id = self.insert_incident(suffix)
        unit_id = self.temp_unit()
        draft = self.new_draft(view, suffix)
        self.pick_available(view, unit_id)
        view.add_button.invoke()
        view.update()
        return incident_id, unit_id, draft

    def test_activation_flow(self):
        view = self.open_view("operador.central")
        incident_id, unit_id, draft = self.draft_with_unit(view)
        self.assertEqual(self.state(view.activate_button), "normal")
        with mock.patch.object(view, "confirm", return_value=False) as confirm:
            view.activate_button.invoke()
        self.assertIn("no se puede deshacer", confirm.call_args[0][0])
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        with mock.patch.object(view, "confirm", return_value=True):
            view.activate_button.invoke()
        view.update()
        self.assertIn("activado correctamente", view.message_label.cget("text"))
        self.assertEqual(self.dispatch_state(draft), "ACTIVO")
        self.assertEqual(self.unit_state(unit_id), "DESPACHADA")
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        self.assertIn("Activo", view.state_label.cget("text"))
        self.assertEqual(view.tree.item(str(draft), "values")[2], "Activo")
        self.assertEqual(view.tree.selection(), (str(draft),))
        mark = view.units_tree.item(str(unit_id), "values")
        self.assertEqual(mark[4], self.unit_state_name("DESPACHADA"))
        self.assertNotIn(self.unit_code(1), self.unit_codes(view.available_tree))
        for name in WRITE_BUTTONS:
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)
        self.assertEqual(self.state(view.observations_text), "disabled")

    def test_activate_disabled_without_units_or_for_non_drafts(self):
        self.insert_incident("A")
        view = self.open_view("operador.central")
        self.new_draft(view, "A")
        self.assertEqual(self.state(view.activate_button), "disabled")
        draft = view.selected_id
        view.activate()
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        for dispatch_id in range(2, 10):
            self.select_dispatch(view, dispatch_id)
            self.assertEqual(self.state(view.activate_button), "disabled")
        self.select_dispatch(view, 1)
        self.assertEqual(self.state(view.activate_button), "normal")

    def test_activation_with_stale_unit_is_rejected(self):
        view = self.open_view("operador.central")
        incident_id, unit_id, draft = self.draft_with_unit(view)
        self.set_unit_state(unit_id, "MANTENIMIENTO")
        before = self.unit_row(unit_id)
        with mock.patch.object(view, "confirm", return_value=True):
            view.activate_button.invoke()
        view.update()
        text = view.message_label.cget("text")
        self.assertIn(self.unit_code(1), text)
        self.assertNotEqual(text, MSG_INTERNAL)
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        self.assertEqual(self.unit_row(unit_id), before)
        values = view.units_tree.item(str(unit_id), "values")
        self.assertEqual(values[4], self.unit_state_name("MANTENIMIENTO"))
        self.assertEqual(values[5], "No disponible ahora")

    def test_activation_service_error_is_generic(self):
        view = self.open_view("operador.central")
        incident_id, unit_id, draft = self.draft_with_unit(view)
        error = DispatchServiceError("SELECT * FROM despachos Traceback")
        with mock.patch.object(dispatch_controller, "activate_dispatch", side_effect=error), \
                mock.patch.object(view, "confirm", return_value=True), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            view.activate_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.dispatch_state(draft), "CREADO")

    def active_dispatch(self, suffix="A"):
        """Despacho ACTIVO temporal creado y activado por el controlador."""
        _login("operador.central")
        incident_id = self.insert_incident(suffix)
        unit_id = self.temp_unit()
        draft = dispatch_controller.create_dispatch_draft({"incident_id": incident_id})
        dispatch_id = draft["dispatch_id"]
        dispatch_controller.add_unit_to_draft(dispatch_id, unit_id)
        dispatch_controller.activate_dispatch(dispatch_id)
        return incident_id, unit_id, dispatch_id

    def open_unit(self, view, dispatch_id, unit_id):
        view.refresh_all()
        self.select_dispatch(view, dispatch_id)
        view.units_tree.selection_set(str(unit_id))
        view.update()

    def enabled_actions(self, view):
        return [n for n in ACTION_BUTTONS if self.state(getattr(view, n)) == "normal"]

    def test_unit_action_enablement_matrix(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        self.assertEqual(self.enabled_actions(view), ["en_route_button"])
        expected = {
            "DESPACHADA": ["en_route_button"], "EN_RUTA": ["on_scene_button"],
            "EN_ESCENA": ["return_button"], "REGRESANDO": ["available_button"],
            "DISPONIBLE": [],
        }
        for state_code, buttons in expected.items():
            self.set_unit_state(unit_id, state_code)
            self.open_unit(view, dispatch_id, unit_id)
            self.assertEqual(self.enabled_actions(view), buttons, state_code)
        # Sin unidad seleccionada no hay acciones
        view.units_tree.selection_remove(*view.units_tree.selection())
        view.update()
        self.assertEqual(self.enabled_actions(view), [])
        self.assertEqual(view.unit_times_label.cget("text"), "")

        # Despacho completado: solo la liberación de una unidad que regresa
        done_incident = self.insert_incident("B")
        done = self.add_dispatch(done_incident, "COMPLETADO")
        done_unit = self.temp_unit()
        self.add_assigned_participation(done, done_unit)
        for state_code, buttons in (("EN_RUTA", []), ("REGRESANDO", ["available_button"])):
            self.set_unit_state(done_unit, state_code)
            self.open_unit(view, done, done_unit)
            self.assertEqual(self.enabled_actions(view), buttons, state_code)

        # Borrador y cancelado: nunca hay acciones aunque el estado de la unidad las permita
        for suffix, state_code in (("C", "CREADO"), ("D", "CANCELADO")):
            other = self.insert_incident(suffix)
            dispatch = self.add_dispatch(other, state_code)
            unit = self.temp_unit(state_code="REGRESANDO")
            self.add_preselection(dispatch, unit)
            self.open_unit(view, dispatch, unit)
            self.assertEqual(self.enabled_actions(view), [], state_code)

    def test_unit_action_full_flow(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        steps = (
            ("en_route_button", "EN_RUTA", "on_scene_button", "Salida: 20"),
            ("on_scene_button", "EN_ESCENA", "return_button", "Llegada: 20"),
            ("return_button", "REGRESANDO", "available_button", "Llegada: 20"),
            ("available_button", "DISPONIBLE", None, "Liberación: 20"),
        )
        for clicked, new_state, next_button, times in steps:
            self.assertEqual(self.enabled_actions(view), [clicked])
            with mock.patch.object(view, "confirm", return_value=True) as confirm:
                getattr(view, clicked).invoke()
            view.update()
            self.assertIn(self.unit_code(1), confirm.call_args[0][0])
            self.assertIn(self.unit_code(1), view.message_label.cget("text"))
            self.assertEqual(self.unit_state(unit_id), new_state)
            self.assertEqual(view.units_tree.selection(), (str(unit_id),))
            self.assertEqual(
                view.units_tree.item(str(unit_id), "values")[4], self.unit_state_name(new_state))
            self.assertIn(times, view.unit_times_label.cget("text"))
            self.assertEqual(
                self.enabled_actions(view), [next_button] if next_button else [])
            self.assertEqual(view.selected_id, dispatch_id)
        self.assertIn("atención", view.incident_state_label.cget("text"))
        self.assertNotEqual(self.state_of(incident_id), "ASIGNADO")
        self.assertIn("Liberación: 20", view.unit_times_label.cget("text"))
        for name in ("save_button", "add_button", "remove_button", "cancel_button",
                     "activate_button"):
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)

    def test_unit_action_confirm_false_changes_nothing(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        before = (self.preselections(dispatch_id), self.unit_row(unit_id),
                  self.row(incident_id))
        with mock.patch.object(view, "confirm", return_value=False):
            view.en_route_button.invoke()
        self.assertEqual((self.preselections(dispatch_id), self.unit_row(unit_id),
                          self.row(incident_id)), before)
        self.assertEqual(self.enabled_actions(view), ["en_route_button"])

    def test_unit_action_read_only_user(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        session.end_session()
        view = self.open_view("consulta.sur")
        self.open_unit(view, dispatch_id, unit_id)
        self.assertEqual(self.enabled_actions(view), [])
        before = (self.preselections(dispatch_id), self.unit_row(unit_id),
                  self.row(incident_id))
        view.en_route_button.configure(state="normal")
        with mock.patch.object(view, "confirm", return_value=True):
            view.run_unit_action("en_route_button")
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual((self.preselections(dispatch_id), self.unit_row(unit_id),
                          self.row(incident_id)), before)

    def test_unit_action_with_stale_state_reloads(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        self.set_unit_state(unit_id, "EN_ESCENA")
        before = (self.preselections(dispatch_id), self.unit_row(unit_id),
                  self.row(incident_id))
        with mock.patch.object(view, "confirm", return_value=True):
            view.en_route_button.invoke()
        view.update()
        text = view.message_label.cget("text")
        self.assertNotEqual(text, "")
        self.assertNotEqual(text, MSG_INTERNAL)
        self.assertEqual((self.preselections(dispatch_id), self.unit_row(unit_id),
                          self.row(incident_id)), before)
        self.assertEqual(self.enabled_actions(view), ["return_button"])

    def test_unit_action_service_error_is_generic(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        error = DispatchServiceError("SELECT * FROM unidades Traceback")
        with mock.patch.object(dispatch_controller, "mark_unit_en_route", side_effect=error), \
                mock.patch.object(view, "confirm", return_value=True), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            view.en_route_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.unit_state(unit_id), "DESPACHADA")

    def test_refresh_keeps_selection_filters_and_typed_text(self):
        self.insert_incident("A")
        self.temp_unit(name="Alfa")
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A", "Guardado")
        view.show_filter.set(SHOW_DRAFTS)
        view.refresh_list()
        _set(view.unit_search_entry, "Alfa")
        view.refresh_available()
        view.observations_text.insert("end", " y sin guardar")
        view.refresh_button.invoke()
        view.update()
        view.update()
        self.assertEqual(view.selected_id, draft)
        self.assertEqual(view.tree.selection(), (str(draft),))
        self.assertEqual(view.show_filter.get(), SHOW_DRAFTS)
        self.assertEqual(view.unit_search_entry.get(), "Alfa")
        self.assertEqual(self.obs(view), "Guardado y sin guardar")
        self.assertEqual(self.unit_codes(view.available_tree), [self.unit_code(1)])
        self.assertEqual(db_all(
            "SELECT observaciones FROM despachos WHERE id_despacho = %s", (draft,))[0][0],
            "Guardado")
        # Sin texto pendiente, el refresco trae lo que cambió en la base
        view.save_button.invoke()
        view.update()
        db_exec("UPDATE despachos SET observaciones = 'Externo' WHERE id_despacho = %s", (draft,))
        view.refresh_button.invoke()
        view.update()
        self.assertEqual(self.obs(view), "Externo")
        self.assertEqual(view.selected_id, draft)

    def test_unit_filters(self):
        type_id = self.temp_unit_type()
        station_id = self.temp_station()
        self.temp_unit(type_id=type_id, station_id=station_id, name="Zeta")
        other = self.temp_unit(name="Otra")
        self.assertIsNotNone(other)
        view = self.open_view("operador.central")
        everything = self.unit_codes(view.available_tree)
        self.assertIn(self.unit_code(1), everything)
        self.assertGreater(len(everything), 2)
        type_name = db_all("SELECT nombre FROM tipos_unidad WHERE id_tipo_unidad = %s",
                           (type_id,))[0][0]
        view.unit_type_filter.set(type_name)
        view.refresh_available()
        self.assertEqual(self.unit_codes(view.available_tree), [self.unit_code(1)])
        view.unit_type_filter.set(ALL_UNIT_TYPES)
        station_code = db_all("SELECT codigo FROM estaciones WHERE id_estacion = %s",
                              (station_id,))[0][0]
        option = next(v for v in view.unit_station_filter.cget("values")
                      if v.startswith(station_code))
        view.unit_station_filter.set(option)
        view.refresh_available()
        self.assertEqual(self.unit_codes(view.available_tree), [self.unit_code(1)])
        _set(view.unit_search_entry, "no-existe-xyz")
        view.search_units_button.invoke()
        self.assertEqual(self.unit_codes(view.available_tree), [])
        view.clear_units_button.invoke()
        view.update()
        self.assertEqual(self.unit_codes(view.available_tree), everything)
        self.assertEqual(view.unit_type_filter.get(), ALL_UNIT_TYPES)
        self.assertEqual(view.unit_station_filter.get(), ALL_STATIONS)

    def test_service_error_shows_generic_message(self):
        view = self.open_view("operador.central")
        error = DispatchServiceError("SELECT * FROM despachos Traceback")
        with mock.patch.object(dispatch_controller, "get_dispatch", side_effect=error), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            self.select_dispatch(view, 1)
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)

    def test_permission_errors_use_permission_messages(self):
        self.insert_incident("A")
        view = self.open_view("operador.central")
        view.incident_combo.set(self.incident_text(f"{self.prefix}-A"))
        with mock.patch.object(
                dispatch_controller, "create_dispatch_draft", side_effect=AuthorizationDenied()):
            view.create_button.invoke()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual(MSG_DENIED_WRITE, "No tiene permiso para modificar despachos.")
        with mock.patch.object(
                dispatch_controller, "list_dispatches", side_effect=AuthorizationDenied()):
            view.refresh_list()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_READ)
        self.assertEqual(MSG_DENIED_READ, "No tiene permiso para consultar despachos.")

    def test_dispatch_vanishing_resets_form(self):
        self.insert_incident("A")
        view = self.open_view("operador.central")
        draft = self.new_draft(view, "A")
        db_exec("DELETE FROM bitacora WHERE entidad = 'DESPACHO' AND id_entidad = %s", (draft,))
        db_exec("DELETE FROM despachos WHERE id_despacho = %s", (draft,))
        view.refresh_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "El despacho ya no existe.")
        self.assertIsNone(view.selected_id)
        self.assertEqual(self.state(view.create_button), "normal")

    def test_read_only_user_can_browse_but_not_write(self):
        view = self.open_view("consulta.sur")
        self.assertEqual(view.read_only_label.winfo_manager(), "pack")
        self.assertEqual(view.read_only_label.cget("text"), "Modo solo lectura")
        for name in WRITE_BUTTONS:
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)
        self.assertEqual(self.state(view.observations_text), "disabled")
        self.assertEqual(len(self.seed_rows(view)), 9)
        view.show_filter.set(SHOW_DRAFTS)
        view.refresh_list()
        self.assertEqual([str(r[0]) for r in self.seed_rows(view)], ["1"])
        self.select_dispatch(view, 1)
        self.assertEqual(view.selected_id, 1)
        self.assertGreater(len(view.units_tree.get_children()), 0)
        for name in WRITE_BUTTONS:
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)
        self.assertEqual(self.state(view.refresh_button), "normal")
        self.assertEqual(self.state(view.back_button), "normal")
        view.refresh_button.invoke()
        view.update()
        self.assertEqual(view.selected_id, 1)

        # Escrituras forzadas: el controlador las rechaza y no cambia nada
        available = view.available_tree.get_children()
        self.assertTrue(available)
        view.available_tree.selection_set(available[0])
        view.add_button.configure(state="normal")
        view.add_unit()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        view.units_tree.selection_set(view.units_tree.get_children()[0])
        with mock.patch.object(view, "confirm", return_value=True):
            view.remove_unit()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        view.save()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        with mock.patch.object(view, "ask_reason", return_value="motivo"), \
                mock.patch.object(view, "confirm", return_value=True):
            view.cancel_draft()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        view.activate_button.configure(state="normal")
        with mock.patch.object(view, "confirm", return_value=True):
            view.activate()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual(self.dispatch_state(1), "CREADO")
        view.clear_form()
        self.insert_incident("A")
        view.load_incident_options()
        view.incident_combo.set(self.incident_text(f"{self.prefix}-A"))
        view.create()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual(self.count_prefix_dispatches(), 0)

    def count_prefix_dispatches(self):
        return db_all(
            "SELECT COUNT(*) FROM despachos WHERE id_incidente IN "
            "(SELECT id_incidente FROM incidentes WHERE codigo LIKE %s)",
            (self.prefix + "%",))[0][0]

    def test_button_set_and_source_have_no_state_controls(self):
        view = self.open_view("admin.demo")
        texts = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    texts.add(child.cget("text"))
                walk(child)

        walk(view)
        self.assertEqual(texts, BUTTONS)
        self.assertEqual(SOURCE.count('text="Activar despacho"'), 1)
        for forbidden in ("Liberar", "tripulaci", "Falla", "Mantenimiento", "Eliminar", "Completar"):
            self.assertNotIn(forbidden, SOURCE)

    def test_source_has_no_data_access_or_role_names(self):
        imports = re.findall(r"^(?:from|import) (\S+)", SOURCE, flags=re.M)
        allowed = {"logging", "tkinter", "customtkinter", "controllers", "utils.permissions"}
        for module in imports:
            self.assertTrue(
                module in allowed or module.startswith("controllers"), module)
        for fragment in ("models", "config.", "from config", "mysql", "bcrypt", "cursor", "execute(", "SELECT ",
                         "ADMINISTRADOR", "OPERADOR", "CONSULTA"):
            self.assertNotIn(fragment, SOURCE)


if __name__ == "__main__":
    unittest.main()

import re
from uuid import uuid4
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
from views.incident_view import IncidentView
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
    "Cancelar borrador", "Cancelar despacho", "Activar despacho", "Refrescar", "Marcar en ruta",
    "Marcar en escena", "Iniciar regreso", "Marcar disponible", "Reportar falla",
    "Agregar personal", "Quitar personal", "Limpiar selección", "Regresar",
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
        self.person_prefix = f"TDP-{self.prefix[-8:]}"
        self._person_counter = 0
        self.crew_before = db_all("SELECT * FROM despacho_personal ORDER BY id_despacho_personal")
        self.root = ctk.CTk()
        self.windows = [self.root]

    def cleanup_steps(self):
        steps = [
            ("DELETE FROM despacho_personal WHERE id_personal IN "
             "(SELECT id_personal FROM personal WHERE codigo LIKE %s)",
             (self.person_prefix + "%",)),
            ("DELETE FROM despacho_personal WHERE id_despacho_unidad IN "
             "(SELECT id_despacho_unidad FROM despacho_unidades WHERE id_unidad IN "
             "(SELECT id_unidad FROM unidades WHERE codigo LIKE %s))",
             (self.unit_prefix + "%",)),
        ]
        steps += [
            ("DELETE FROM compatibilidad_incidente_unidad WHERE id_tipo_incidente = %s", (t,))
            for t in self.type_ids
        ] + [
            ("DELETE FROM compatibilidad_incidente_unidad WHERE id_tipo_unidad = %s", (t,))
            for t in self.unit_type_ids
        ]
        steps += super().cleanup_steps()
        steps.append(("DELETE FROM personal WHERE codigo LIKE %s", (self.person_prefix + "%",)))
        return steps

    def tearDown(self):
        for window in self.windows:
            try:
                window.destroy()
            except Exception:
                pass
        session.end_session()
        super().tearDown()
        self.assertEqual(
            db_all("SELECT * FROM despacho_personal ORDER BY id_despacho_personal"),
            self.crew_before)

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
        self.assertEqual(self.state(view.cancel_dispatch_button), "disabled")

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

    def test_cancel_dispatch_button_enablement(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.select_dispatch(view, dispatch_id)
        self.assertEqual(self.state(view.cancel_dispatch_button), "normal")
        self.assertEqual(self.state(view.cancel_button), "disabled")
        for seed_id, expected in ((1, "disabled"), (2, "normal"), (5, "disabled"),
                                  (7, "disabled")):
            self.select_dispatch(view, seed_id)
            self.assertEqual(self.state(view.cancel_dispatch_button), expected, seed_id)
        self.select_dispatch(view, 1)
        self.assertEqual(self.state(view.cancel_button), "normal")
        view.close()
        session.end_session()
        reader = self.open_view("consulta.sur")
        self.select_dispatch(reader, dispatch_id)
        self.assertEqual(self.state(reader.cancel_dispatch_button), "disabled")

    def test_cancel_active_dispatch_flow(self):
        incident_id, first, dispatch_id = self.active_dispatch("A")
        second = self.temp_unit()
        # Segunda unidad en el mismo despacho: se agrega por un despacho de refuerzo temporal
        extra = dispatch_controller.create_dispatch_draft({"incident_id": incident_id})
        dispatch_controller.add_unit_to_draft(extra["dispatch_id"], second)
        dispatch_controller.activate_dispatch(extra["dispatch_id"])
        dispatch_controller.mark_unit_en_route(extra["dispatch_id"], second)
        view = self.open_view("operador.central")
        self.open_unit(view, extra["dispatch_id"], second)
        incident_state = self.state_of(incident_id)
        self.assertEqual(self.state(view.cancel_dispatch_button), "normal")

        for reason in (None, ""):
            with mock.patch.object(view, "ask_reason", return_value=reason), \
                    mock.patch.object(view, "confirm", return_value=True):
                view.cancel_dispatch_button.invoke()
            self.assertIn("motivo es obligatorio", view.message_label.cget("text"))
            self.assertEqual(self.dispatch_state(extra["dispatch_id"]), "ACTIVO")
        with mock.patch.object(view, "ask_reason", return_value="   "), \
                mock.patch.object(view, "confirm", return_value=True):
            view.cancel_dispatch_button.invoke()
        self.assertIn("motivo", view.message_label.cget("text"))
        self.assertEqual(self.dispatch_state(extra["dispatch_id"]), "ACTIVO")
        with mock.patch.object(view, "ask_reason", return_value="Motivo"), \
                mock.patch.object(view, "confirm", return_value=False):
            view.cancel_dispatch_button.invoke()
        self.assertEqual(self.dispatch_state(extra["dispatch_id"]), "ACTIVO")
        self.assertEqual(self.unit_state(second), "EN_RUTA")

        reason = "r" * 250
        with mock.patch.object(view, "ask_reason", return_value=reason), \
                mock.patch.object(view, "confirm", return_value=True) as confirm:
            view.cancel_dispatch_button.invoke()
        view.update()
        self.assertIn("incidente conserva su estado", confirm.call_args[0][0])
        self.assertEqual(self.dispatch_state(extra["dispatch_id"]), "CANCELADO")
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertEqual(self.unit_state(second), "REGRESANDO")
        self.assertEqual(self.unit_state(first), "DESPACHADA")
        self.assertEqual(self.state_of(incident_id), incident_state)
        self.assertIn("Cancelado", view.state_label.cget("text"))
        self.assertEqual(
            view.units_tree.item(str(second), "values")[4], self.unit_state_name("REGRESANDO"))
        self.assertEqual(view.tree.item(str(extra["dispatch_id"]), "values")[2], "Cancelado")
        for name in ("cancel_dispatch_button", "cancel_button", "activate_button",
                     "save_button", "add_button", "remove_button"):
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)
        # La unidad que regresa se libera aunque el despacho esté cancelado
        view.units_tree.selection_set(str(second))
        view.update()
        self.assertEqual(self.enabled_actions(view), ["available_button"])
        with mock.patch.object(view, "confirm", return_value=True):
            view.available_button.invoke()
        view.update()
        self.assertEqual(self.unit_state(second), "DISPONIBLE")
        self.assertEqual(self.enabled_actions(view), [])

        # Cancelar el despacho con la unidad DESPACHADA la deja Disponible
        self.open_unit(view, dispatch_id, first)
        with mock.patch.object(view, "ask_reason", return_value="Otro motivo"), \
                mock.patch.object(view, "confirm", return_value=True):
            view.cancel_dispatch_button.invoke()
        view.update()
        self.assertEqual(self.unit_state(first), "DISPONIBLE")
        self.assertEqual(
            view.units_tree.item(str(first), "values")[4], self.unit_state_name("DISPONIBLE"))
        self.assertEqual(self.state_of(incident_id), incident_state)

    def test_cancel_active_dispatch_read_only_and_errors(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        error = DispatchServiceError("SELECT * FROM despachos Traceback")
        with mock.patch.object(dispatch_controller, "cancel_active_dispatch", side_effect=error), \
                mock.patch.object(view, "ask_reason", return_value="Motivo"), \
                mock.patch.object(view, "confirm", return_value=True), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            view.cancel_dispatch_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        view.close()
        session.end_session()
        reader = self.open_view("consulta.sur")
        self.open_unit(reader, dispatch_id, unit_id)
        self.assertEqual(self.state(reader.cancel_dispatch_button), "disabled")
        before = (self.dispatch_row(dispatch_id), self.unit_row(unit_id))
        reader.cancel_dispatch_button.configure(state="normal")
        with mock.patch.object(reader, "ask_reason", return_value="Motivo"), \
                mock.patch.object(reader, "confirm", return_value=True):
            reader.cancel_active()
        self.assertEqual(reader.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual((self.dispatch_row(dispatch_id), self.unit_row(unit_id)), before)

    def test_incident_view_cancel_cascade(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        dispatch_controller.mark_unit_en_route(dispatch_id, unit_id)
        draft_unit = self.temp_unit()
        draft = dispatch_controller.create_dispatch_draft({"incident_id": incident_id})
        dispatch_controller.add_unit_to_draft(draft["dispatch_id"], draft_unit)
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        view = IncidentView(self.root)
        view.update()
        code = f"{self.prefix}-A"
        for iid in view.tree.get_children():
            if view.tree.item(iid, "values")[0] == code:
                view.tree.selection_set(iid)
        view.update()
        self.assertEqual(str(view.cancel_button.cget("state")), "normal")
        with mock.patch.object(view, "ask_reason", return_value="Falsa alarma") as ask, \
                mock.patch.object(view, "confirm", return_value=True) as confirm:
            view.cancel_button.invoke()
        view.update()
        for text in (ask.call_args[0][0], confirm.call_args[0][0]):
            self.assertIn("activos", text)
        self.assertIn("Regresando", confirm.call_args[0][0])
        self.assertEqual(self.state_of(incident_id), "CANCELADO")
        self.assertEqual(self.dispatch_state(dispatch_id), "CANCELADO")
        self.assertEqual(self.dispatch_state(draft["dispatch_id"]), "CANCELADO")
        self.assertEqual(self.unit_state(unit_id), "REGRESANDO")
        self.assertEqual(self.unit_state(draft_unit), "DISPONIBLE")
        self.assertEqual(view.state_label.cget("text"), "Estado actual: Cancelado")
        self.assertEqual(str(view.cancel_button.cget("state")), "disabled")
        self.assertEqual(str(view.save_button.cget("state")), "disabled")

    def active_dispatch_units(self, count=2, suffix="A"):
        """Despacho ACTIVO con varias unidades DESPACHADA, creado por el controlador."""
        _login("operador.central")
        incident_id = self.insert_incident(suffix)
        units = [self.temp_unit() for _ in range(count)]
        draft = dispatch_controller.create_dispatch_draft({"incident_id": incident_id})
        for unit in units:
            dispatch_controller.add_unit_to_draft(draft["dispatch_id"], unit)
        dispatch_controller.activate_dispatch(draft["dispatch_id"])
        return incident_id, units, draft["dispatch_id"]

    def failure_enabled(self, view):
        return self.state(view.failure_button) == "normal"

    def test_failure_button_enablement_matrix(self):
        incident_id, units, dispatch_id = self.active_dispatch_units(1)
        unit_id = units[0]
        view = self.open_view("operador.central")
        self.assertFalse(self.failure_enabled(view))
        expected = {
            "DESPACHADA": True, "EN_RUTA": True, "EN_ESCENA": True, "REGRESANDO": False,
            "DISPONIBLE": False, "MANTENIMIENTO": False, "FUERA_DE_SERVICIO": False,
        }
        for state_code, enabled in expected.items():
            self.set_unit_state(unit_id, state_code)
            self.open_unit(view, dispatch_id, unit_id)
            self.assertEqual(self.failure_enabled(view), enabled, state_code)
        view.units_tree.selection_remove(*view.units_tree.selection())
        view.update()
        self.assertFalse(self.failure_enabled(view))

        done_incident = self.insert_incident("B")
        done = self.add_dispatch(done_incident, "COMPLETADO")
        done_unit = self.temp_unit(state_code="EN_RUTA")
        self.add_assigned_participation(done, done_unit)
        self.open_unit(view, done, done_unit)
        self.assertFalse(self.failure_enabled(view))
        for suffix, state_code in (("C", "CREADO"), ("D", "CANCELADO")):
            other = self.insert_incident(suffix)
            dispatch = self.add_dispatch(other, state_code)
            unit = self.temp_unit(state_code="EN_RUTA")
            self.add_preselection(dispatch, unit)
            self.open_unit(view, dispatch, unit)
            self.assertFalse(self.failure_enabled(view), state_code)
        view.close()
        session.end_session()
        reader = self.open_view("consulta.sur")
        self.open_unit(reader, dispatch_id, unit_id)
        self.set_unit_state(unit_id, "DESPACHADA")
        self.open_unit(reader, dispatch_id, unit_id)
        self.assertFalse(self.failure_enabled(reader))

    def test_failure_flow_completes_dispatch_on_last_unit(self):
        incident_id, (first, second), dispatch_id = self.active_dispatch_units(2)
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, first)
        incident_label = view.incident_state_label.cget("text")
        incident_state = self.state_of(incident_id)
        out = self.unit_state_name("FUERA_DE_SERVICIO")
        with mock.patch.object(view, "ask_reason", return_value="Avería de motor"), \
                mock.patch.object(view, "confirm", return_value=True) as confirm:
            view.failure_button.invoke()
        view.update()
        self.assertIn(self.unit_code(1), confirm.call_args[0][0])
        self.assertIn("Fuera de servicio", confirm.call_args[0][0])
        self.assertEqual(self.unit_state(first), "FUERA_DE_SERVICIO")
        self.assertEqual(view.units_tree.item(str(first), "values")[4], out)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertIn("Activo", view.state_label.cget("text"))
        self.assertEqual(view.incident_state_label.cget("text"), incident_label)
        self.assertEqual(view.units_tree.selection(), (str(first),))
        self.assertFalse(self.failure_enabled(view))
        self.assertIn("Falla", view.message_label.cget("text"))

        view.units_tree.selection_set(str(second))
        view.update()
        self.assertTrue(self.failure_enabled(view))
        with mock.patch.object(view, "ask_reason", return_value="Otra avería"), \
                mock.patch.object(view, "confirm", return_value=True):
            view.failure_button.invoke()
        view.update()
        self.assertEqual(self.unit_state(second), "FUERA_DE_SERVICIO")
        self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO")
        self.assertIn("Completado", view.state_label.cget("text"))
        self.assertEqual(view.tree.item(str(dispatch_id), "values")[2], "Completado")
        self.assertEqual(view.incident_state_label.cget("text"), incident_label)
        self.assertEqual(self.state_of(incident_id), incident_state)
        self.assertFalse(self.failure_enabled(view))
        self.assertEqual(self.state(view.cancel_dispatch_button), "disabled")

    def test_failure_without_reason_or_confirmation_changes_nothing(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        before = (self.preselections(dispatch_id), self.unit_row(unit_id),
                  self.dispatch_row(dispatch_id), self.row(incident_id))
        for reason in (None, ""):
            with mock.patch.object(view, "ask_reason", return_value=reason), \
                    mock.patch.object(view, "confirm", return_value=True):
                view.failure_button.invoke()
            self.assertIn("motivo es obligatorio", view.message_label.cget("text"))
        with mock.patch.object(view, "ask_reason", return_value="   "), \
                mock.patch.object(view, "confirm", return_value=True):
            view.failure_button.invoke()
        self.assertIn("motivo", view.message_label.cget("text"))
        with mock.patch.object(view, "ask_reason", return_value="Avería"), \
                mock.patch.object(view, "confirm", return_value=False):
            view.failure_button.invoke()
        self.assertEqual((self.preselections(dispatch_id), self.unit_row(unit_id),
                          self.dispatch_row(dispatch_id), self.row(incident_id)), before)

    def test_failure_read_only_user_is_denied(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        session.end_session()
        view = self.open_view("consulta.sur")
        self.open_unit(view, dispatch_id, unit_id)
        before = (self.preselections(dispatch_id), self.unit_row(unit_id),
                  self.dispatch_row(dispatch_id))
        view.failure_button.configure(state="normal")
        with mock.patch.object(view, "ask_reason", return_value="Avería"), \
                mock.patch.object(view, "confirm", return_value=True):
            view.report_failure()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual((self.preselections(dispatch_id), self.unit_row(unit_id),
                          self.dispatch_row(dispatch_id)), before)

    def test_failure_with_stale_state_reloads(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        self.set_unit_state(unit_id, "REGRESANDO")
        before = (self.preselections(dispatch_id), self.unit_row(unit_id),
                  self.dispatch_row(dispatch_id))
        with mock.patch.object(view, "ask_reason", return_value="Avería"), \
                mock.patch.object(view, "confirm", return_value=True):
            view.failure_button.invoke()
        view.update()
        text = view.message_label.cget("text")
        self.assertNotEqual(text, "")
        self.assertNotEqual(text, MSG_INTERNAL)
        self.assertEqual((self.preselections(dispatch_id), self.unit_row(unit_id),
                          self.dispatch_row(dispatch_id)), before)
        self.assertFalse(self.failure_enabled(view))

    def test_failure_service_error_is_generic(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        error = DispatchServiceError("SELECT * FROM unidades Traceback")
        with mock.patch.object(dispatch_controller, "report_unit_failure", side_effect=error), \
                mock.patch.object(view, "ask_reason", return_value="Avería"), \
                mock.patch.object(view, "confirm", return_value=True), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            view.failure_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.unit_state(unit_id), "DESPACHADA")

    def person(self, active=True):
        self._person_counter += 1
        n = self._person_counter
        return db_exec(
            "INSERT INTO personal (id_estacion, codigo, nombres, apellidos, cargo, activo) "
            "VALUES (%s, %s, %s, %s, 'Bombero', %s)",
            (self.seed_station, f"{self.person_prefix}-{n}", f"Nombre{n}", f"Apellido{n}",
             1 if active else 0))

    def temp_links(self):
        return db_all(
            "SELECT dp.id_despacho_personal, dp.id_despacho_unidad, dp.id_personal "
            "FROM despacho_personal dp JOIN personal p ON p.id_personal = dp.id_personal "
            "WHERE p.codigo LIKE %s ORDER BY dp.id_despacho_personal",
            (self.person_prefix + "%",))

    def crew_codes(self, view):
        return [view.crew_tree.item(i, "values")[0] for i in view.crew_tree.get_children()]

    def crew_state(self, view):
        return (str(view.add_crew_button.cget("state")),
                str(view.remove_crew_button.cget("state")))

    def test_crew_buttons_enablement_matrix(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        first = self.person()
        dispatch_controller.assign_crew_member(dispatch_id, unit_id, first)
        view = self.open_view("operador.central")
        self.assertEqual(self.crew_state(view), ("disabled", "disabled"))
        self.open_unit(view, dispatch_id, unit_id)
        self.assertEqual(self.crew_codes(view), [f"{self.person_prefix}-1"])
        self.assertEqual(view.crew_label.cget("text"), "Tripulación: 1 / 4")
        self.assertEqual(self.crew_state(view), ("normal", "disabled"))
        view.crew_tree.selection_set(str(first))
        view.update()
        self.assertEqual(self.crew_state(view), ("normal", "normal"))
        # Capacidad completa: no se puede agregar
        db_exec("UPDATE unidades SET capacidad_personal = 1 WHERE id_unidad = %s", (unit_id,))
        self.open_unit(view, dispatch_id, unit_id)
        self.assertEqual(view.crew_label.cget("text"), "Tripulación: 1 / 1")
        self.assertEqual(str(view.add_crew_button.cget("state")), "disabled")
        # Sin capacidad definida siempre hay lugar
        db_exec("UPDATE unidades SET capacidad_personal = NULL WHERE id_unidad = %s", (unit_id,))
        self.open_unit(view, dispatch_id, unit_id)
        self.assertEqual(view.crew_label.cget("text"), "Tripulación: 1")
        self.assertEqual(str(view.add_crew_button.cget("state")), "normal")
        # Congelada cuando la unidad ya salió
        for state_code in ("EN_RUTA", "EN_ESCENA", "REGRESANDO"):
            self.set_unit_state(unit_id, state_code)
            self.open_unit(view, dispatch_id, unit_id)
            view.crew_tree.selection_set(str(first))
            view.update()
            self.assertEqual(self.crew_state(view), ("disabled", "disabled"), state_code)
            self.assertEqual(self.crew_codes(view), [f"{self.person_prefix}-1"])

        # Despachos completado, cancelado y borrador: visible pero sin edición
        for suffix, dispatch_state in (("B", "COMPLETADO"), ("C", "CANCELADO")):
            other = self.insert_incident(suffix)
            dispatch = self.add_dispatch(other, dispatch_state)
            unit = self.temp_unit(state_code="DESPACHADA")
            link = self.add_assigned_participation(dispatch, unit)
            member = self.person()
            db_exec("INSERT INTO despacho_personal (id_despacho_unidad, id_personal) "
                    "VALUES (%s, %s)", (link, member))
            self.open_unit(view, dispatch, unit)
            self.assertEqual(len(view.crew_tree.get_children()), 1, dispatch_state)
            view.crew_tree.selection_set(str(member))
            view.update()
            self.assertEqual(self.crew_state(view), ("disabled", "disabled"), dispatch_state)
        draft_incident = self.insert_incident("D")
        draft = self.add_dispatch(draft_incident, "CREADO")
        draft_unit = self.temp_unit()
        self.add_preselection(draft, draft_unit)
        self.open_unit(view, draft, draft_unit)
        self.assertEqual(self.crew_state(view), ("disabled", "disabled"))
        view.close()
        session.end_session()

        # Solo lectura: la lista se ve pero no hay edición
        self.set_unit_state(unit_id, "DESPACHADA")
        reader = self.open_view("consulta.sur")
        self.open_unit(reader, dispatch_id, unit_id)
        self.assertEqual(self.crew_codes(reader), [f"{self.person_prefix}-1"])
        reader.crew_tree.selection_set(str(first))
        reader.update()
        self.assertEqual(self.crew_state(reader), ("disabled", "disabled"))

    def test_crew_add_and_remove_flow(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        first, second = self.person(), self.person()
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        seen = []

        def pick(person_id):
            def chooser(candidates):
                seen.append([c["code"] for c in candidates])
                return person_id
            return chooser

        with mock.patch.object(view, "choose_personnel", side_effect=pick(None)) as chooser:
            view.add_crew_button.invoke()
        self.assertTrue(chooser.called)
        self.assertEqual(self.temp_links(), [])
        for person_id in (first, second):
            with mock.patch.object(view, "choose_personnel", side_effect=pick(person_id)):
                view.add_crew_button.invoke()
            view.update()
            self.assertIn("Personal asignado", view.message_label.cget("text"))
        self.assertIn(f"{self.person_prefix}-1", seen[0])
        self.assertNotIn(f"{self.person_prefix}-1", seen[-1])
        self.assertEqual(len(self.temp_links()), 2)
        self.assertEqual(self.crew_codes(view), [f"{self.person_prefix}-1", f"{self.person_prefix}-2"])
        self.assertEqual(view.crew_label.cget("text"), "Tripulación: 2 / 4")
        self.assertEqual(view.units_tree.selection(), (str(unit_id),))

        view.crew_tree.selection_set(str(first))
        view.update()
        with mock.patch.object(view, "confirm", return_value=False):
            view.remove_crew_button.invoke()
        self.assertEqual(len(self.temp_links()), 2)
        with mock.patch.object(view, "confirm", return_value=True) as confirm:
            view.remove_crew_button.invoke()
        view.update()
        self.assertIn(self.unit_code(1), confirm.call_args[0][0])
        self.assertIn("Nombre1", confirm.call_args[0][0])
        self.assertEqual(len(self.temp_links()), 1)
        self.assertEqual(self.crew_codes(view), [f"{self.person_prefix}-2"])
        self.assertEqual(view.crew_label.cget("text"), "Tripulación: 1 / 4")
        self.assertEqual(self.state(view.remove_crew_button), "disabled")

    def test_crew_candidates_empty_message(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        with mock.patch.object(dispatch_controller, "list_available_crew_candidates",
                               return_value=[]), \
                mock.patch.object(view, "choose_personnel") as chooser:
            view.add_crew_button.invoke()
        self.assertFalse(chooser.called)
        self.assertEqual(view.message_label.cget("text"), "No hay personal disponible para asignar.")

    def test_crew_raced_candidates_show_message_and_write_nothing(self):
        incident_id, (unit_id, other_unit), dispatch_id = self.active_dispatch_units(2)
        gone, busy = self.person(), self.person()
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)

        def inactive_chooser(candidates):
            db_exec("UPDATE personal SET activo = 0 WHERE id_personal = %s", (gone,))
            return gone

        def busy_chooser(candidates):
            dispatch_controller.assign_crew_member(dispatch_id, other_unit, busy)
            return busy

        for chooser in (inactive_chooser, busy_chooser):
            before = len(self.temp_links())
            with mock.patch.object(view, "choose_personnel", side_effect=chooser):
                view.add_crew_button.invoke()
            view.update()
            text = view.message_label.cget("text")
            self.assertNotEqual(text, "")
            self.assertNotEqual(text, MSG_INTERNAL)
            self.assertNotIn("Traceback", text)
            self.assertEqual(view.message_label.cget("text_color"), dispatch_view.COLOR_ERROR)
            self.assertEqual(
                len(self.temp_links()), before + (1 if chooser is busy_chooser else 0))
            self.assertEqual(self.crew_codes(view), [])
        self.assertEqual(self.state(view.add_crew_button), "normal")

    def test_crew_read_only_user_is_denied(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        member, candidate = self.person(), self.person()
        dispatch_controller.assign_crew_member(dispatch_id, unit_id, member)
        session.end_session()
        view = self.open_view("consulta.sur")
        self.open_unit(view, dispatch_id, unit_id)
        view.crew_tree.selection_set(str(member))
        view.update()
        before = self.temp_links()
        with mock.patch.object(view, "choose_personnel", return_value=candidate):
            view.add_crew()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        with mock.patch.object(view, "confirm", return_value=True):
            view.remove_crew()
        self.assertEqual(view.message_label.cget("text"), MSG_DENIED_WRITE)
        self.assertEqual(self.temp_links(), before)

    def test_crew_service_error_is_generic(self):
        incident_id, (unit_id,), dispatch_id = self.active_dispatch_units(1)
        person = self.person()
        view = self.open_view("operador.central")
        self.open_unit(view, dispatch_id, unit_id)
        error = DispatchServiceError("SELECT * FROM personal Traceback")
        with mock.patch.object(dispatch_controller, "assign_crew_member", side_effect=error), \
                mock.patch.object(view, "choose_personnel", return_value=person), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            view.add_crew_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.temp_links(), [])

    def test_incident_lifecycle_flow_through_incident_view(self):
        incident_id, unit_id, dispatch_id = self.active_dispatch("A")
        self.set_state(incident_id, "EN_ATENCION")
        view = IncidentView(self.root)
        view.update()
        for iid in view.tree.get_children():
            if view.tree.item(iid, "values")[0] == f"{self.prefix}-A":
                view.tree.selection_set(iid)
        view.update()

        def states():
            return tuple(str(b.cget("state")) for b in (
                view.control_button, view.reactivate_button, view.close_incident_button))

        self.assertEqual(states(), ("normal", "disabled", "disabled"))
        with mock.patch.object(view, "confirm", return_value=False):
            view.control_button.invoke()
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        with mock.patch.object(view, "confirm", return_value=True) as confirm:
            view.control_button.invoke()
        view.update()
        self.assertIn("no cierra el incidente", confirm.call_args[0][0])
        self.assertEqual(self.state_of(incident_id), "CONTROLADO")
        self.assertEqual(view.state_label.cget("text"), "Estado actual: Controlado")
        self.assertIn("Fecha de control", view.control_label.cget("text"))
        self.assertEqual(view.closed_label.cget("text"), "")
        control_date = db_all("SELECT fecha_control FROM incidentes WHERE id_incidente = %s",
                              (incident_id,))[0][0]
        self.assertEqual(states(), ("disabled", "normal", "normal"))

        # Con una unidad operando se puede reactivar, pero no cerrar
        with mock.patch.object(view, "confirm", return_value=True) as confirm:
            view.close_incident_button.invoke()
        self.assertIn("será definitivo", confirm.call_args[0][0])
        self.assertEqual(self.state_of(incident_id), "CONTROLADO")
        self.assertEqual(view.message_label.cget("text_color"), dispatch_view.COLOR_ERROR)
        with mock.patch.object(view, "ask_reason", return_value=None), \
                mock.patch.object(view, "confirm", return_value=True):
            view.reactivate_button.invoke()
        self.assertEqual(self.state_of(incident_id), "CONTROLADO")
        with mock.patch.object(view, "ask_reason", return_value="Rebrote"), \
                mock.patch.object(view, "confirm", return_value=True):
            view.reactivate_button.invoke()
        view.update()
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        self.assertEqual(view.state_label.cget("text"), "Estado actual: En atención")
        self.assertEqual(
            db_all("SELECT fecha_control FROM incidentes WHERE id_incidente = %s",
                   (incident_id,))[0][0], control_date)
        with mock.patch.object(view, "confirm", return_value=True):
            view.control_button.invoke()
        view.update()
        self.assertEqual(self.state_of(incident_id), "CONTROLADO")

        # Sin operaciones pendientes el cierre procede y es definitivo
        dispatch_controller.report_unit_failure(dispatch_id, unit_id, "Avería")
        with mock.patch.object(view, "confirm", return_value=True):
            view.close_incident_button.invoke()
        view.update()
        self.assertEqual(self.state_of(incident_id), "CERRADO")
        self.assertEqual(view.state_label.cget("text"), "Estado actual: Cerrado")
        self.assertIn("Fecha de cierre", view.closed_label.cget("text"))
        self.assertEqual(states(), ("disabled", "disabled", "disabled"))
        self.assertEqual(str(view.cancel_button.cget("state")), "disabled")
        self.assertEqual(str(view.save_button.cget("state")), "disabled")

    def recommendations_text(self, view):
        return view.recommendations_text.get("1.0", "end-1c")

    def seed_incident_types(self):
        """Tipos de incidente de la semilla con recomendaciones distintas entre sí."""
        _login("admin.demo")
        found = {}
        for (type_id,) in db_all(
                "SELECT id_tipo_incidente FROM tipos_incidente WHERE nombre NOT LIKE 'TIT-%' "
                "AND activo = 1 ORDER BY id_tipo_incidente"):
            incident_id = self.insert_incident(f"Q{type_id}", tipo=type_id)
            rows = dispatch_controller.get_unit_recommendations(incident_id)
            if rows:
                found.setdefault(tuple(r["unit_type_name"] for r in rows), (type_id, incident_id, rows))
        session.end_session()
        return list(found.values())

    def draft_for(self, incident_id):
        return self.add_dispatch(incident_id, "CREADO")

    def test_recommendations_follow_selected_dispatch(self):
        options = self.seed_incident_types()
        self.assertGreaterEqual(len(options), 2)
        (first_type, first_incident, first_rows), (second_type, second_incident, second_rows) = (
            options[0], options[1])
        first = self.draft_for(first_incident)
        second = self.draft_for(second_incident)
        view = self.open_view("operador.central")
        self.assertEqual(self.recommendations_text(view), dispatch_view.RECOMMENDATIONS_PLACEHOLDER)
        self.select_dispatch(view, first)
        first_text = self.recommendations_text(view)
        self.assertTrue(first_text.startswith("1. "))
        self.assertIn(first_rows[0]["unit_type_name"], first_text.splitlines()[0])
        for index, row in enumerate(first_rows, start=1):
            self.assertIn(f"{index}. {row['unit_type_name']}", first_text)
        self.select_dispatch(view, second)
        second_text = self.recommendations_text(view)
        self.assertIn(second_rows[0]["unit_type_name"], second_text.splitlines()[0])
        self.assertNotEqual(first_text, second_text)
        # La pestaña no cambia sola a Recomendaciones
        self.assertNotEqual(view.side_tabs.get(), dispatch_view.TAB_RECOMMENDATIONS)
        # El texto es de solo lectura
        self.assertEqual(str(view.recommendations_text.cget("state")), "disabled")
        # Un despacho terminal también muestra la guía del incidente
        done = self.add_dispatch(first_incident, "CANCELADO")
        view.refresh_list()
        self.select_dispatch(view, done)
        self.assertEqual(self.recommendations_text(view), first_text)

    def test_recommendations_empty_zero_and_notes_formats(self):
        incident_id = self.insert_incident("E")
        dispatch_id = self.draft_for(incident_id)
        view = self.open_view("operador.central")
        self.select_dispatch(view, dispatch_id)
        rows = [
            {"unit_type_id": 1, "unit_type_name": "Alfa", "priority": 1, "note": "Con nota",
             "available_count": 0, "available_units": []},
            {"unit_type_id": 2, "unit_type_name": "Beta", "priority": 2, "note": None,
             "available_count": 1, "available_units": []},
            {"unit_type_id": 3, "unit_type_name": "Gama", "priority": 3, "note": None,
             "available_count": 2, "available_units": []},
        ]
        with mock.patch.object(dispatch_controller, "get_unit_recommendations",
                               return_value=rows), \
                mock.patch.object(dispatch_controller, "get_unit_candidates", return_value=[]):
            view.refresh_button.invoke()
        self.assertEqual(self.recommendations_text(view), (
            "1. Alfa — 0 disponibles\n    Con nota\n2. Beta — 1 disponible\n"
            "3. Gama — 2 disponibles\n\nUnidades candidatas\n"
            "No hay unidades disponibles compatibles."))
        with mock.patch.object(dispatch_controller, "get_unit_recommendations",
                               return_value=[]), \
                mock.patch.object(dispatch_controller, "get_unit_candidates", return_value=[]):
            view.refresh_button.invoke()
        self.assertEqual(self.recommendations_text(view), dispatch_view.RECOMMENDATIONS_EMPTY)
        self.assertEqual(
            dispatch_view.RECOMMENDATIONS_EMPTY,
            "Sin recomendaciones configuradas para este tipo de incidente.")

    def test_recommendations_for_type_without_rules(self):
        _login("admin.demo")
        type_id = self.temp_type()
        session.end_session()
        incident_id = self.insert_incident("N", tipo=type_id)
        dispatch_id = self.draft_for(incident_id)
        view = self.open_view("operador.central")
        self.select_dispatch(view, dispatch_id)
        self.assertEqual(self.recommendations_text(view), dispatch_view.RECOMMENDATIONS_EMPTY)

    def test_recommendations_read_only_user_and_refresh_requery(self):
        options = self.seed_incident_types()
        incident_id = options[0][1]
        dispatch_id = self.draft_for(incident_id)
        view = self.open_view("consulta.sur")
        with mock.patch.object(
                dispatch_controller, "get_unit_recommendations",
                wraps=dispatch_controller.get_unit_recommendations) as spy, \
                mock.patch.object(
                    dispatch_controller, "get_unit_candidates",
                    wraps=dispatch_controller.get_unit_candidates) as spy_units:
            self.select_dispatch(view, dispatch_id)
            self.assertEqual((spy.call_count, spy_units.call_count), (1, 1))
            self.assertTrue(self.recommendations_text(view).startswith("1. "))
            view.refresh_button.invoke()
            view.update()
            self.assertEqual((spy.call_count, spy_units.call_count), (2, 2))
        texts = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    texts.add(child.cget("text"))
                walk(child)

        walk(view)
        self.assertEqual(texts, BUTTONS)
        for name in WRITE_BUTTONS + ("cancel_dispatch_button", "failure_button",
                                     "add_crew_button", "remove_crew_button"):
            self.assertEqual(self.state(getattr(view, name)), "disabled", name)

    def test_recommendations_service_error_is_generic(self):
        incident_id = self.insert_incident("F")
        dispatch_id = self.draft_for(incident_id)
        view = self.open_view("operador.central")
        error = DispatchServiceError("SELECT * FROM compatibilidad Traceback")
        with mock.patch.object(dispatch_controller, "get_unit_recommendations",
                               side_effect=error), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            self.select_dispatch(view, dispatch_id)
        text = view.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.recommendations_text(view), dispatch_view.RECOMMENDATIONS_ERROR)
        self.assertEqual(view.selected_id, dispatch_id)

    def compat_setup(self, with_coordinates=True):
        """Tipos temporales con prioridad 1 y 2 y un incidente con coordenadas."""
        incident_type, near_type, far_type = self.temp_type(), self.temp_unit_type(), self.temp_unit_type()
        for unit_type, priority, note in ((far_type, 1, "Principal"), (near_type, 2, None)):
            db_exec(
                "INSERT INTO compatibilidad_incidente_unidad (id_tipo_incidente, "
                "id_tipo_unidad, prioridad_recomendacion, observacion, activo) "
                "VALUES (%s, %s, %s, %s, 1)", (incident_type, unit_type, priority, note))
        incident_id = self.insert_incident("K", tipo=incident_type)
        db_exec("UPDATE incidentes SET latitud = 14.0, longitud = -90.0 WHERE id_incidente = %s",
                (incident_id,))
        return incident_id, near_type, far_type

    def coordinate_station(self, lat, lon):
        station_id = db_exec(
            "INSERT INTO estaciones (codigo, nombre, direccion, latitud, longitud) "
            "VALUES (%s, 'Estación temporal', 'Dir', %s, %s)",
            (f"TUS-{uuid4().hex[:8]}", lat, lon))
        self.station_ids.append(station_id)
        return station_id

    def test_candidate_units_listed_in_controller_order(self):
        incident_id, near_type, far_type = self.compat_setup()
        far_station = self.coordinate_station(14.08, -90.0)
        near_station = self.coordinate_station(14.01, -90.0)
        blind_station = self.coordinate_station(None, None)
        self.temp_unit(station_id=near_station, type_id=near_type, name="Cercana")
        self.temp_unit(station_id=far_station, type_id=far_type, name="Lejana")
        self.temp_unit(station_id=blind_station, type_id=far_type)
        dispatch_id = self.draft_for(incident_id)
        view = self.open_view("operador.central")
        self.select_dispatch(view, dispatch_id)
        expected = dispatch_controller.get_unit_candidates(incident_id)
        text = self.recommendations_text(view)
        self.assertEqual(len(expected), 3)
        positions = []
        for candidate in expected:
            self.assertIn(candidate["unit_code"], text)
            self.assertIn(candidate["unit_type_name"], text)
            self.assertIn(candidate["station_code"], text)
            positions.append(text.index(candidate["unit_code"] + " "
                                        if candidate["unit_name"] else candidate["unit_code"]))
        self.assertEqual(positions, sorted(positions))
        # Prioridad 1 lejana antes que prioridad 2 cercana
        self.assertEqual([c["recommendation_priority"] for c in expected], [1, 1, 2])
        self.assertIn("Lejana", text.split("Unidades candidatas")[1].splitlines()[1])
        self.assertIn("Prioridad 1 · Principal", text)
        self.assertIn("Prioridad 2", text)
        self.assertIn(" km", text)
        self.assertIn(dispatch_view.NO_DISTANCE_TEXT, text)
        self.assertNotIn("None", text)

    def test_candidates_empty_still_shows_types_with_zero(self):
        incident_id, near_type, far_type = self.compat_setup()
        dispatch_id = self.draft_for(incident_id)
        view = self.open_view("operador.central")
        self.select_dispatch(view, dispatch_id)
        text = self.recommendations_text(view)
        self.assertEqual(text.count("0 disponibles"), 2)
        self.assertIn("Unidades candidatas\n" + dispatch_view.NO_CANDIDATES_TEXT, text)

    def test_candidates_visible_to_read_only_user_and_errors_are_generic(self):
        incident_id, near_type, far_type = self.compat_setup()
        station = self.coordinate_station(14.01, -90.0)
        unit_id = self.temp_unit(station_id=station, type_id=far_type, name="Visible")
        dispatch_id = self.draft_for(incident_id)
        reader = self.open_view("consulta.sur")
        self.select_dispatch(reader, dispatch_id)
        self.assertIn(self.unit_code(1) + " — Visible", self.recommendations_text(reader))
        for name in WRITE_BUTTONS + ("add_crew_button", "remove_crew_button"):
            self.assertEqual(self.state(getattr(reader, name)), "disabled", name)
        error = DispatchServiceError("SELECT * FROM unidades Traceback")
        with mock.patch.object(dispatch_controller, "get_unit_candidates", side_effect=error), \
                self.assertLogs("views.dispatch_view", level="ERROR"):
            reader.refresh_button.invoke()
        text = reader.message_label.cget("text")
        self.assertEqual(text, MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)
        self.assertEqual(self.recommendations_text(reader), dispatch_view.RECOMMENDATIONS_ERROR)
        self.assertIsNotNone(unit_id)

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
        for forbidden in ("Liberar", "Mantenimiento", "Eliminar", "Completar"):
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

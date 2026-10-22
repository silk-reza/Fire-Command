import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import customtkinter as ctk

from config.database import get_connection
from controllers import personnel_controller
from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.personnel_controller import PersonnelServiceError
from models import station_model
from utils import session
from views import personnel_view
from views.main_view import MainView
from views.personnel_view import ALL_STATIONS, STATION_PLACEHOLDER, PersonnelView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(personnel_view.__file__).read_text(encoding="utf-8")
CENTRAL = "EST-CENTRAL — Estación Central"
NORTE = "EST-NORTE — Estación Norte"
SUR = "EST-SUR — Estación Sur"


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


def _cleanup(person_prefix, station_prefix):
    """Borra solo filas propias: primero personal y luego estaciones."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM personal WHERE codigo LIKE %s", (person_prefix + "%",))
        cursor.execute("DELETE FROM estaciones WHERE codigo LIKE %s", (station_prefix + "%",))
        connection.commit()
        cursor.close()
    finally:
        connection.close()


def _seed_snapshot():
    stations = _db_all("SELECT * FROM estaciones WHERE codigo NOT LIKE 'TPS-%' ORDER BY id_estacion")
    people = _db_all("SELECT * FROM personal WHERE codigo NOT LIKE 'TPG-%' ORDER BY id_personal")
    return stations, people


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


class PersonnelViewTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        token = uuid4().hex[:8]
        self.prefix = f"TPG-{token}"
        self.station_prefix = f"TPS-{token}"
        self.seed_before = _seed_snapshot()
        self.root = ctk.CTk()
        self.windows = [self.root]

    def tearDown(self):
        for window in self.windows:
            try:
                window.destroy()
            except Exception:
                pass
        session.end_session()
        _cleanup(self.prefix, self.station_prefix)
        self.assertEqual(_seed_snapshot(), self.seed_before)

    def open_personnel(self, username="operador.central"):
        _login(username)
        view = PersonnelView(self.root)
        view.update()
        return view

    def code(self, suffix="A"):
        return f"{self.prefix}-{suffix}"

    def fill(self, view, suffix="A", station=CENTRAL, **overrides):
        values = {
            "code": self.code(suffix), "first_name": "Nombre", "last_name": "Apellido",
            "position": "Bombero", "phone": "",
        }
        values.update(overrides)
        _set(view.code_entry, values["code"])
        _set(view.first_name_entry, values["first_name"])
        _set(view.last_name_entry, values["last_name"])
        _set(view.position_entry, values["position"])
        _set(view.phone_entry, values["phone"])
        view.station_combo.set(station)
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

    def db_count(self):
        return _db_all("SELECT COUNT(*) FROM personal WHERE codigo LIKE %s", (self.prefix + "%",))[0][0]

    def make_station(self, suffix="I", active=False):
        code = f"{self.station_prefix}-{suffix}"
        station_id = station_model.create_station(code, "Estación temporal", "Calle 9", None, None, None, None)
        if not active:
            station_model.set_station_active(station_id, False)
        return code, station_id

    def person_in_inactive_station(self, suffix="P"):
        """Crea la persona mientras la estación está activa y luego la desactiva."""
        station_code, station_id = self.make_station("I", active=True)
        _login("admin.demo")
        person = personnel_controller.create_person({
            "code": self.code(suffix), "first_name": "N", "last_name": "A",
            "position": "Bombero", "phone": "", "station_id": station_id,
        })
        station_model.set_station_active(station_id, False)
        session.end_session()
        return person, f"{station_code} — Estación temporal (inactiva)"

    def test_opening_lists_seed_people_for_admin_and_operator(self):
        for username in ("admin.demo", "operador.central"):
            with self.subTest(username=username):
                view = self.open_personnel(username)
                self.assertEqual(
                    [view.tree.heading(c, "text") for c in view.tree["columns"]],
                    ["Código", "Nombres", "Apellidos", "Cargo", "Estación", "Estado"],
                )
                rows = self.rows(view)
                self.assertEqual(len(rows), 15)
                self.assertEqual(self.row_for(view, "PER-001")[4], CENTRAL)
                self.assertEqual(self.row_for(view, "PER-011")[5], "Inactivo")
                self.assertEqual(self.row_for(view, "PER-001")[5], "Activo")
                for row in rows:
                    self.assertFalse(row[4].isdigit())
                self.assertEqual(view.station_filter.get(), ALL_STATIONS)
                self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)
                view.close()
                session.end_session()

    def test_search_by_code_first_name_last_name_and_unknown(self):
        view = self.open_personnel()
        self.assertTrue(view.search_entry._entry.bind("<Return>"))
        self.search(view, "PER-005")
        self.assertEqual([r[0] for r in self.rows(view)], ["PER-005"])
        self.search(view, "Valeria")
        self.assertEqual([r[0] for r in self.rows(view)], ["PER-008"])
        self.search(view, "Vega")
        self.assertEqual([r[0] for r in self.rows(view)], ["PER-011"])
        self.search(view, "zzz-no-existe-xyz")
        self.assertEqual(self.rows(view), [])
        self.assertEqual(view.message_label.cget("text"), "")

    def test_station_filter_and_combined_search(self):
        view = self.open_personnel()
        self.assertEqual(
            list(view.station_filter.cget("values")), [ALL_STATIONS, CENTRAL, NORTE, SUR]
        )
        view.station_filter.set(CENTRAL)
        view.refresh_list()
        view.update()
        rows = self.rows(view)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r[4] == CENTRAL for r in rows))
        # Texto de otra estación combinado con este filtro no devuelve nada
        self.search(view, "Valeria")
        self.assertEqual(self.rows(view), [])
        self.search(view, "Ana")
        self.assertEqual([r[0] for r in self.rows(view)], ["PER-002"])
        view.clear_search_button.invoke()
        view.update()
        self.assertEqual(view.search_entry.get(), "")
        self.assertEqual(view.station_filter.get(), ALL_STATIONS)
        self.assertEqual(len(self.rows(view)), 15)

    def test_create_as_operator_resets_form_without_selection(self):
        view = self.open_personnel()
        code = self.create(view, station=NORTE)
        self.assertIn("creada correctamente", view.message_label.cget("text"))
        row = self.row_for(view, code)
        self.assertEqual(row[4], NORTE)
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)
        self.assertIsNone(view.selected_id)
        self.assertEqual(str(view.create_button.cget("state")), "normal")

    def test_create_keeps_search_text_and_station_filter(self):
        view = self.open_personnel()
        _set(view.search_entry, self.prefix)
        view.station_filter.set(NORTE)
        self.create(view, station=NORTE)
        self.assertEqual(view.search_entry.get(), self.prefix)
        self.assertEqual(view.station_filter.get(), NORTE)
        self.assertEqual(len(self.rows(view)), 1)

    def test_duplicate_code_shows_friendly_message(self):
        view = self.open_personnel()
        self.create(view)
        self.create(view)
        self.assertEqual(view.message_label.cget("text"), "Ya existe una persona con ese código.")
        self.assertEqual(self.db_count(), 1)

    def test_create_without_station_is_rejected_by_controller(self):
        view = self.open_personnel()
        code = self.fill(view)
        view.station_combo.set(STATION_PLACEHOLDER)
        view.create_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "La estación es obligatoria.")
        self.assertEqual(view.message_label.cget("text_color"), personnel_view.COLOR_ERROR)
        self.assertEqual(self.db_count(), 0)
        self.assertIsNone(self.row_for(view, code))

    def test_empty_required_field_shows_controller_message(self):
        view = self.open_personnel()
        self.create(view, first_name="")
        self.assertEqual(view.message_label.cget("text"), "El nombre es obligatorio.")
        self.assertEqual(self.db_count(), 0)

    def test_select_loads_form_with_station(self):
        view = self.open_personnel()
        code = self.create(view, station=SUR, phone="555-0100")
        self.select(view, code)
        self.assertEqual(view.code_entry.get(), code)
        self.assertEqual(view.phone_entry.get(), "555-0100")
        self.assertEqual(view.station_combo.get(), SUR)
        self.assertEqual(str(view.create_button.cget("state")), "disabled")
        self.assertEqual(str(view.save_button.cget("state")), "normal")
        self.assertEqual(str(view.deactivate_button.cget("state")), "normal")
        self.assertEqual(str(view.activate_button.cget("state")), "disabled")

    def test_create_mode_offers_only_active_stations(self):
        station_code, _ = self.make_station("I", active=False)
        view = self.open_personnel()
        options = list(view.station_combo.cget("values"))
        self.assertEqual(options, [CENTRAL, NORTE, SUR])
        self.assertFalse(any(station_code in o for o in options))
        # El filtro sí conserva la estación inactiva, marcada
        self.assertIn(
            f"{station_code} — Estación temporal (inactiva)", list(view.station_filter.cget("values"))
        )

    def test_edit_mode_keeps_own_inactive_station_only(self):
        person, own_text = self.person_in_inactive_station()
        other_code, _ = self.make_station("J", active=False)
        view = self.open_personnel("admin.demo")
        self.select(view, person["code"])
        self.assertEqual(view.station_combo.get(), own_text)
        options = list(view.station_combo.cget("values"))
        self.assertEqual(options, [CENTRAL, NORTE, SUR, own_text])
        self.assertFalse(any(other_code in o for o in options))
        # Al limpiar vuelve a solo activas
        view.clear_button.invoke()
        view.update()
        self.assertEqual(list(view.station_combo.cget("values")), [CENTRAL, NORTE, SUR])
        self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)

    def test_edit_other_field_keeping_inactive_station_and_move_to_active(self):
        person, own_text = self.person_in_inactive_station()
        view = self.open_personnel("admin.demo")
        self.select(view, person["code"])
        _set(view.position_entry, "Cargo nuevo")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        self.assertEqual(self.row_for(view, person["code"])[3], "Cargo nuevo")
        view.station_combo.set(SUR)
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        self.assertEqual(self.row_for(view, person["code"])[4], SUR)
        self.assertEqual(list(view.station_combo.cget("values")), [CENTRAL, NORTE, SUR])

    def test_forced_save_to_inactive_destination_shows_backend_message(self):
        _, inactive_id = self.make_station("K", active=False)
        view = self.open_personnel()
        code = self.create(view, station=CENTRAL)
        self.select(view, code)
        inactive_text = next(t for t, i in view._station_ids.items() if i == inactive_id)
        view.station_combo.set(inactive_text)
        view.save_button.invoke()
        view.update()
        self.assertEqual(
            view.message_label.cget("text"), "No se puede asignar personal a una estación inactiva."
        )
        self.assertEqual(view.message_label.cget("text_color"), personnel_view.COLOR_ERROR)
        self.assertEqual(self.row_for(view, code)[4], CENTRAL)
        db = _db_all(
            "SELECT e.codigo FROM personal p JOIN estaciones e USING (id_estacion) "
            "WHERE p.codigo = %s", (code,)
        )[0][0]
        self.assertEqual(db, "EST-CENTRAL")

    def test_station_deactivated_after_opening_shows_inactive_on_select(self):
        station_code, station_id = self.make_station("I", active=True)
        other_code, _ = self.make_station("J", active=False)
        _login("admin.demo")
        person = personnel_controller.create_person({
            "code": self.code(), "first_name": "N", "last_name": "A",
            "position": "Bombero", "phone": "", "station_id": station_id,
        })
        view = self.open_personnel("admin.demo")
        own_active = f"{station_code} — Estación temporal"
        self.assertIn(own_active, list(view.station_filter.cget("values")))
        station_model.set_station_active(station_id, False)
        view.station_filter.set(own_active)
        self.select(view, person["code"])
        own_text = f"{own_active} (inactiva)"
        self.assertEqual(view.station_combo.get(), own_text)
        options = list(view.station_combo.cget("values"))
        self.assertEqual(options, [CENTRAL, NORTE, SUR, own_text])
        self.assertFalse(any(other_code in o for o in options))
        # El filtro se conserva y ahora muestra la estación como inactiva
        self.assertEqual(view.station_filter.get(), own_text)

    def test_reloading_stations_in_edit_mode_keeps_inactive_selection(self):
        person, own_text = self.person_in_inactive_station()
        view = self.open_personnel("admin.demo")
        self.select(view, person["code"])
        view.load_stations()
        view.update()
        self.assertEqual(view.station_combo.get(), own_text)
        self.assertIn(own_text, list(view.station_combo.cget("values")))

    def test_edit_persists_including_station_change(self):
        view = self.open_personnel()
        code = self.create(view, station=CENTRAL)
        self.select(view, code)
        _set(view.position_entry, "Cargo editado")
        view.station_combo.set(NORTE)
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        row = self.row_for(view, code)
        self.assertEqual(row[3], "Cargo editado")
        self.assertEqual(row[4], NORTE)
        db = _db_all(
            "SELECT p.cargo, e.codigo FROM personal p JOIN estaciones e USING (id_estacion) "
            "WHERE p.codigo = %s", (code,)
        )[0]
        self.assertEqual(db, ("Cargo editado", "EST-NORTE"))

    def test_deactivate_confirm_true_and_reactivate(self):
        view = self.open_personnel()
        code = self.create(view)
        self.select(view, code)
        person_id = view.selected_id
        asked = []
        view.confirm = lambda message: asked.append(message) or True
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.row_for(view, code)[5], "Inactivo")
        active_ids = [p["person_id"] for p in personnel_controller.list_active_personnel()]
        self.assertNotIn(person_id, active_ids)
        self.assertEqual(str(view.activate_button.cget("state")), "normal")
        self.assertEqual(str(view.deactivate_button.cget("state")), "disabled")
        view.activate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, code)[5], "Activo")
        active_ids = [p["person_id"] for p in personnel_controller.list_active_personnel()]
        self.assertIn(person_id, active_ids)

    def test_deactivate_confirm_false_changes_nothing(self):
        view = self.open_personnel()
        code = self.create(view)
        self.select(view, code)
        view.confirm = lambda message: False
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, code)[5], "Activo")

    def test_clear_resets_form(self):
        view = self.open_personnel()
        code = self.create(view)
        self.select(view, code)
        view.clear_button.invoke()
        view.update()
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)
        self.assertEqual(view.message_label.cget("text"), "")
        self.assertEqual(str(view.create_button.cget("state")), "normal")

    def test_missing_person_resets_and_refreshes(self):
        view = self.open_personnel()
        code = self.create(view)
        self.select(view, code)
        _cleanup(self.prefix, self.station_prefix)
        view.confirm = lambda message: True
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "La persona ya no existe.")
        self.assertIsNone(view.selected_id)
        self.assertIsNone(self.row_for(view, code))

    def test_unexpected_error_shows_generic_message(self):
        view = self.open_personnel()
        self.fill(view)
        with mock.patch.object(
            personnel_controller, "create_person", side_effect=PersonnelServiceError("SELECT x")
        ), self.assertLogs("views.personnel_view", level="ERROR"):
            view.create_button.invoke()
            view.update()
        text = view.message_label.cget("text")
        self.assertEqual(text, personnel_view.MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)

    def test_authorization_denied_shows_permission_message(self):
        view = self.open_personnel()
        self.fill(view)
        with mock.patch.object(
            personnel_controller, "create_person", side_effect=AuthorizationDenied("x")
        ):
            view.create_button.invoke()
            view.update()
        self.assertEqual(view.message_label.cget("text"), personnel_view.MSG_DENIED)

    def test_forced_open_as_read_only_user_is_denied(self):
        view = self.open_personnel("consulta.sur")
        self.assertEqual(self.rows(view), [])
        self.assertEqual(view.message_label.cget("text"), personnel_view.MSG_DENIED)
        self.fill(view)
        view.create_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), personnel_view.MSG_DENIED)
        self.assertEqual(self.db_count(), 0)

    def test_window_fits_screen_and_source_is_clean(self):
        view = self.open_personnel()
        self.assertLessEqual(view.winfo_reqwidth(), 1200)
        self.assertLessEqual(view.winfo_reqheight(), 800)
        for forbidden in (
            "ADMINISTRADOR", "OPERADOR", "CONSULTA", "import mysql", "import bcrypt",
            "config.database", "from models", "station_controller",
        ):
            self.assertNotIn(forbidden, SOURCE)

    def test_read_only_main_view_has_no_personnel_button(self):
        user = _login("consulta.sur")
        main = MainView(user)
        self.windows.append(main)
        main.update()
        self.assertNotIn("manage_resources", main.access_widgets)
        texts = [
            w.cget("text") for w in main.winfo_children()[0].winfo_children()
            if isinstance(w, ctk.CTkButton)
        ]
        self.assertNotIn("Personal", texts)

    def test_admin_and_operator_main_view_open_reuse_and_close_module(self):
        for username in ("admin.demo", "operador.central"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                button = main.access_widgets["manage_resources"]
                self.assertEqual(button.cget("text"), "Personal")
                button.invoke()
                main.update()
                first = main.personnel_view
                self.assertIsInstance(first, PersonnelView)
                self.assertEqual(len(self.rows(first)), 15)
                button.invoke()
                main.update()
                self.assertIs(main.personnel_view, first)
                first.back_button.invoke()
                main.update()
                self.assertFalse(first.winfo_exists())
                main.destroy()
                session.end_session()


if __name__ == "__main__":
    unittest.main()

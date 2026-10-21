import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import customtkinter as ctk

from config.database import get_connection
from controllers import station_controller
from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.station_controller import StationServiceError
from utils import session
from views import station_view
from views.main_view import MainView
from views.station_view import StationView

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(station_view.__file__).read_text(encoding="utf-8")
SEED_CODES = ["EST-CENTRAL", "EST-NORTE", "EST-SUR"]


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
        cursor.execute("DELETE FROM estaciones WHERE codigo LIKE %s", (prefix + "%",))
        connection.commit()
        cursor.close()
    finally:
        connection.close()


def _seed_snapshot():
    return _db_all("SELECT * FROM estaciones WHERE codigo NOT LIKE 'TST-%' ORDER BY id_estacion")


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


class StationViewTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.prefix = f"TST-GUI-{uuid4().hex[:8]}"
        self.seed_before = _seed_snapshot()
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
        _cleanup(self.prefix)
        self.assertEqual(_seed_snapshot(), self.seed_before)

    def open_stations(self, username="admin.demo"):
        _login(username)
        self.view = StationView(self.root)
        self.view.update()
        return self.view

    def code(self, suffix="A"):
        return f"{self.prefix}-{suffix}"

    def fill(self, view, suffix="A", **overrides):
        values = {
            "code": self.code(suffix), "name": "Estación de prueba", "address": "Calle 1",
            "reference": "", "phone": "", "latitude": "", "longitude": "",
        }
        values.update(overrides)
        _set(view.code_entry, values["code"])
        _set(view.name_entry, values["name"])
        _set(view.address_entry, values["address"])
        _set(view.reference_entry, values["reference"])
        _set(view.phone_entry, values["phone"])
        _set(view.latitude_entry, values["latitude"])
        _set(view.longitude_entry, values["longitude"])
        return values["code"]

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

    def create(self, view, suffix="A", **overrides):
        code = self.fill(view, suffix, **overrides)
        view.create_button.invoke()
        view.update()
        return code

    def db_count(self):
        return _db_all(
            "SELECT COUNT(*) FROM estaciones WHERE codigo LIKE %s", (self.prefix + "%",)
        )[0][0]

    def test_opening_lists_seed_stations_with_four_columns(self):
        view = self.open_stations()
        self.assertEqual(
            [view.tree.heading(c, "text") for c in view.tree["columns"]],
            ["Código", "Nombre", "Dirección", "Estado"],
        )
        codes = [r[0] for r in self.rows(view)]
        for code in SEED_CODES:
            self.assertIn(code, codes)
        self.assertEqual(self.row_for(view, "EST-CENTRAL")[3], "Activa")

    def test_search_by_code_name_and_unknown(self):
        view = self.open_stations()
        _set(view.search_entry, "EST-NORTE")
        # Enter queda enlazado en el Entry interno (el evento real no es fiable sin foco de teclado)
        self.assertTrue(view.search_entry._entry.bind("<Return>"))
        view.search_button.invoke()
        view.update()
        self.assertEqual([r[0] for r in self.rows(view)], ["EST-NORTE"])
        _set(view.search_entry, "Central")
        view.search_button.invoke()
        view.update()
        self.assertEqual([r[0] for r in self.rows(view)], ["EST-CENTRAL"])
        _set(view.search_entry, "zzz-no-existe-xyz")
        view.search_button.invoke()
        view.update()
        self.assertEqual(self.rows(view), [])
        self.assertEqual(view.message_label.cget("text"), "")
        view.clear_search_button.invoke()
        view.update()
        self.assertEqual(view.search_entry.get(), "")
        self.assertGreaterEqual(len(self.rows(view)), 3)

    def test_create_success_resets_form_without_selection(self):
        view = self.open_stations()
        code = self.create(view)
        self.assertIn("creada correctamente", view.message_label.cget("text"))
        self.assertIsNotNone(self.row_for(view, code))
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertIsNone(view.selected_id)
        self.assertEqual(str(view.create_button.cget("state")), "normal")

    def test_create_keeps_search_text(self):
        view = self.open_stations()
        _set(view.search_entry, self.prefix)
        self.create(view)
        self.assertEqual(view.search_entry.get(), self.prefix)
        self.assertEqual(len(self.rows(view)), 1)

    def test_duplicate_code_shows_friendly_message(self):
        view = self.open_stations()
        self.create(view)
        self.create(view)
        self.assertEqual(
            view.message_label.cget("text"), "Ya existe una estación con ese código."
        )
        self.assertEqual(self.db_count(), 1)

    def test_valid_coordinates_saved_and_shown(self):
        view = self.open_stations()
        code = self.create(view, latitude="19.4326", longitude="-99.1332")
        self.assertIn("creada correctamente", view.message_label.cget("text"))
        self.select(view, code)
        self.assertEqual(view.latitude_entry.get(), "19.4326")
        self.assertEqual(view.longitude_entry.get(), "-99.1332")

    def test_invalid_coordinates_show_message_and_save_nothing(self):
        cases = (
            {"latitude": "91", "longitude": "10"},
            {"latitude": "10", "longitude": "181"},
            {"latitude": "10", "longitude": ""},
            {"latitude": "", "longitude": "10"},
        )
        view = self.open_stations()
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                self.create(view, suffix=f"C{index}", **case)
                self.assertTrue(view.message_label.cget("text"))
                self.assertNotIn("creada correctamente", view.message_label.cget("text"))
                self.assertEqual(view.message_label.cget("text_color"), station_view.COLOR_ERROR)
        self.assertEqual(self.db_count(), 0)

    def test_empty_coordinates_accepted_and_form_loads_empty(self):
        view = self.open_stations()
        code = self.create(view)
        self.assertIn("creada correctamente", view.message_label.cget("text"))
        self.select(view, code)
        self.assertEqual(view.latitude_entry.get(), "")
        self.assertEqual(view.longitude_entry.get(), "")
        self.assertEqual(view.code_entry.get(), code)

    def test_buttons_follow_selection(self):
        view = self.open_stations()
        code = self.create(view)
        self.assertEqual(str(view.save_button.cget("state")), "disabled")
        self.assertEqual(str(view.activate_button.cget("state")), "disabled")
        self.assertEqual(str(view.deactivate_button.cget("state")), "disabled")
        self.select(view, code)
        self.assertEqual(str(view.create_button.cget("state")), "disabled")
        self.assertEqual(str(view.save_button.cget("state")), "normal")
        self.assertEqual(str(view.deactivate_button.cget("state")), "normal")
        self.assertEqual(str(view.activate_button.cget("state")), "disabled")

    def test_edit_persists(self):
        view = self.open_stations()
        code = self.create(view)
        self.select(view, code)
        _set(view.name_entry, "Nombre editado")
        _set(view.phone_entry, "555-0100")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        self.assertEqual(self.row_for(view, code)[1], "Nombre editado")
        row = _db_all(
            "SELECT nombre, telefono FROM estaciones WHERE codigo = %s", (code,)
        )[0]
        self.assertEqual(row, ("Nombre editado", "555-0100"))

    def test_deactivate_confirm_true_and_reactivate(self):
        view = self.open_stations()
        code = self.create(view)
        self.select(view, code)
        station_id = view.selected_id
        asked = []
        view.confirm = lambda message: asked.append(message) or True
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.row_for(view, code)[3], "Inactiva")
        active_ids = [s["station_id"] for s in station_controller.list_active_stations()]
        self.assertNotIn(station_id, active_ids)
        self.assertEqual(str(view.activate_button.cget("state")), "normal")
        self.assertEqual(str(view.deactivate_button.cget("state")), "disabled")
        view.activate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, code)[3], "Activa")
        active_ids = [s["station_id"] for s in station_controller.list_active_stations()]
        self.assertIn(station_id, active_ids)

    def test_deactivate_confirm_false_changes_nothing(self):
        view = self.open_stations()
        code = self.create(view)
        self.select(view, code)
        view.confirm = lambda message: False
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, code)[3], "Activa")

    def test_clear_resets_form(self):
        view = self.open_stations()
        code = self.create(view)
        self.select(view, code)
        view.clear_button.invoke()
        view.update()
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(view.message_label.cget("text"), "")
        self.assertEqual(str(view.create_button.cget("state")), "normal")

    def test_missing_station_resets_and_refreshes(self):
        view = self.open_stations()
        code = self.create(view)
        self.select(view, code)
        _cleanup(self.prefix)
        view.deactivate_button.configure(state="normal")
        view.confirm = lambda message: True
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "La estación ya no existe.")
        self.assertIsNone(view.selected_id)
        self.assertIsNone(self.row_for(view, code))

    def test_unexpected_error_shows_generic_message(self):
        view = self.open_stations()
        self.fill(view)
        with mock.patch.object(
            station_controller, "create_station", side_effect=StationServiceError("SELECT x")
        ), self.assertLogs("views.station_view", level="ERROR"):
            view.create_button.invoke()
            view.update()
        text = view.message_label.cget("text")
        self.assertEqual(text, station_view.MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)

    def test_authorization_denied_shows_permission_message(self):
        view = self.open_stations()
        self.fill(view)
        with mock.patch.object(
            station_controller, "create_station", side_effect=AuthorizationDenied("x")
        ):
            view.create_button.invoke()
            view.update()
        self.assertEqual(view.message_label.cget("text"), station_view.MSG_DENIED)

    def test_forced_open_as_non_admin_is_denied(self):
        for username in ("operador.central", "consulta.sur"):
            with self.subTest(username=username):
                view = self.open_stations(username)
                self.assertEqual(self.rows(view), [])
                self.assertEqual(view.message_label.cget("text"), station_view.MSG_DENIED)
                self.fill(view)
                view.create_button.invoke()
                view.update()
                self.assertEqual(view.message_label.cget("text"), station_view.MSG_DENIED)
                self.assertEqual(self.db_count(), 0)
                view.close()
                session.end_session()

    def test_window_fits_screen_and_source_is_clean(self):
        view = self.open_stations()
        self.assertLessEqual(view.winfo_reqwidth(), 1000)
        self.assertLessEqual(view.winfo_reqheight(), 800)
        for forbidden in (
            "ADMINISTRADOR", "OPERADOR", "CONSULTA", "import mysql", "import bcrypt",
            "config.database", "from models", "float(",
        ):
            self.assertNotIn(forbidden, SOURCE)

    def test_non_admin_main_view_has_no_stations_button(self):
        for username in ("operador.central", "consulta.sur"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                self.assertNotIn("manage_catalogs", main.access_widgets)
                texts = [
                    w.cget("text") for w in main.winfo_children()[0].winfo_children()
                    if isinstance(w, ctk.CTkButton)
                ]
                self.assertNotIn("Estaciones", texts)
                main.destroy()
                session.end_session()

    def test_admin_main_view_opens_reuses_and_closes_module(self):
        user = _login("admin.demo")
        main = MainView(user)
        self.windows.append(main)
        main.update()
        button = main.access_widgets["manage_catalogs"]
        self.assertEqual(button.cget("text"), "Estaciones")
        button.invoke()
        main.update()
        first = main.station_view
        self.assertIsInstance(first, StationView)
        self.assertIn("EST-CENTRAL", [r[0] for r in self.rows(first)])
        button.invoke()
        main.update()
        self.assertIs(main.station_view, first)
        first.back_button.invoke()
        main.update()
        self.assertFalse(first.winfo_exists())


if __name__ == "__main__":
    unittest.main()

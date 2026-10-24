import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import customtkinter as ctk

from controllers import unit_controller
from controllers.access_controller import AuthorizationDenied
from controllers.auth_controller import authenticate
from controllers.unit_controller import UnitServiceError
from tests._unit_support import UnitTestBase, db_all, db_exec
from utils import session
from views import unit_view
from views.main_view import MainView
from views.unit_view import (
    ALL_STATES, ALL_STATIONS, ALL_TYPES, STATION_PLACEHOLDER, TYPE_PLACEHOLDER, UnitView,
)

DEMO_PASSWORD = "FireDemo2026!"
SOURCE = Path(unit_view.__file__).read_text(encoding="utf-8")
CENTRAL = "EST-CENTRAL — Estación Central"
NORTE = "EST-NORTE — Estación Norte"
SUR = "EST-SUR — Estación Sur"
SEED_TYPES = ["AUTOBOMBA", "AMBULANCIA", "RESCATE", "CISTERNA", "HAZMAT", "UNIDAD_DE_MANDO"]


def _login(username):
    user = authenticate(username, DEMO_PASSWORD)
    assert user is not None, f"No se pudo autenticar a {username}"
    session.start_session(user)
    return user


def _set(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


class UnitViewTest(UnitTestBase):
    def setUp(self):
        super().setUp()
        session.end_session()
        # Prefijo propio de este módulo para que la limpieza solo toque sus filas
        self.prefix = f"TUG-{uuid4().hex[:8]}"
        self.plate_prefix = f"TG-{uuid4().hex[:8]}"
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

    def open_units(self, username="operador.central"):
        _login(username)
        view = UnitView(self.root)
        view.update()
        return view

    def code(self, suffix="A"):
        return f"{self.prefix}-{suffix}"

    def plate(self, suffix="A"):
        return f"{self.plate_prefix}-{suffix}"

    def station_text(self, station_id, active=True):
        code = db_all("SELECT codigo FROM estaciones WHERE id_estacion = %s", (station_id,))[0][0]
        text = f"{code} — Estación temporal"
        return text if active else f"{text} (inactiva)"

    def type_text(self, type_id, active=True):
        name = db_all("SELECT nombre FROM tipos_unidad WHERE id_tipo_unidad = %s", (type_id,))[0][0]
        return name if active else f"{name} (inactiva)"

    def fill(self, view, suffix="A", station=CENTRAL, type_name="RESCATE", **overrides):
        values = {
            "code": self.code(suffix), "name": "Unidad de prueba", "plate": self.plate(suffix),
            "capacity": "4", "description": "Temporal",
        }
        values.update(overrides)
        _set(view.code_entry, values["code"])
        _set(view.name_entry, values["name"])
        _set(view.plate_entry, values["plate"])
        _set(view.capacity_entry, values["capacity"])
        _set(view.description_entry, values["description"])
        view.station_combo.set(station)
        view.type_combo.set(type_name)
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

    def refresh(self, view):
        view.refresh_list()
        view.update()

    def make_unit(self, suffix="U", station_id=None, type_id=None):
        """Crea una unidad temporal por el controlador con sesión de administrador."""
        _login("admin.demo")
        unit = unit_controller.create_unit({
            "code": self.code(suffix), "name": "Temporal", "plate": self.plate(suffix),
            "capacity": "3", "description": None,
            "station_id": station_id or self.seed_station, "type_id": type_id or self.seed_type,
        })
        session.end_session()
        return unit

    def test_opening_lists_seed_units_for_admin_and_operator(self):
        for username in ("admin.demo", "operador.central"):
            with self.subTest(username=username):
                view = self.open_units(username)
                self.assertEqual(
                    [view.tree.heading(c, "text") for c in view.tree["columns"]],
                    ["Código", "Placa", "Nombre", "Estación", "Tipo", "Estado operativo", "Activo"],
                )
                rows = self.rows(view)
                self.assertEqual(len(rows), 11)
                b01 = self.row_for(view, "B-01")
                self.assertEqual(b01[1:], (
                    "BOM-001", "Autobomba Central 1", CENTRAL, "AUTOBOMBA", "Disponible", "Sí",
                ))
                self.assertEqual(self.row_for(view, "CM-01")[5:], ("Fuera de servicio", "No"))
                self.assertEqual(self.row_for(view, "R-02")[5], "En ruta")
                self.assertEqual(view.station_filter.get(), ALL_STATIONS)
                self.assertEqual(view.type_filter.get(), ALL_TYPES)
                self.assertEqual(view.state_filter.get(), ALL_STATES)
                self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)
                self.assertEqual(view.type_combo.get(), TYPE_PLACEHOLDER)
                self.assertEqual(view.state_label.cget("text"), "Estado inicial: Disponible")
                view.close()
                session.end_session()

    def test_search_by_code_plate_and_unknown(self):
        view = self.open_units()
        self.assertTrue(view.search_entry._entry.bind("<Return>"))
        self.search(view, "B-02")
        self.assertEqual([r[0] for r in self.rows(view)], ["B-02"])
        self.search(view, "HAZ-001")
        self.assertEqual([r[0] for r in self.rows(view)], ["HZ-01"])
        self.search(view, "zzz-no-existe-xyz")
        self.assertEqual(self.rows(view), [])
        self.assertEqual(view.message_label.cget("text"), "")

    def test_filters_by_station_type_state_and_combinations(self):
        view = self.open_units()
        self.assertEqual(
            list(view.station_filter.cget("values")), [ALL_STATIONS, CENTRAL, NORTE, SUR]
        )
        type_values = list(view.type_filter.cget("values"))
        self.assertEqual(type_values[0], ALL_TYPES)
        self.assertEqual(sorted(type_values[1:]), sorted(SEED_TYPES))
        self.assertEqual(len(list(view.state_filter.cget("values"))), 8)
        view.station_filter.set(NORTE)
        self.refresh(view)
        self.assertEqual(len(self.rows(view)), 4)
        view.type_filter.set("RESCATE")
        self.refresh(view)
        self.assertEqual([r[0] for r in self.rows(view)], ["R-02"])
        view.type_filter.set(ALL_TYPES)
        view.station_filter.set(ALL_STATIONS)
        view.state_filter.set("Disponible")
        self.refresh(view)
        self.assertEqual(sorted(r[0] for r in self.rows(view)), ["A-02", "B-01", "B-02"])
        view.type_filter.set("AUTOBOMBA")
        view.station_filter.set(SUR)
        self.refresh(view)
        self.assertEqual([r[0] for r in self.rows(view)], ["B-02"])
        self.search(view, "A-02")
        self.assertEqual(self.rows(view), [])
        view.clear_search_button.invoke()
        view.update()
        self.assertEqual(view.search_entry.get(), "")
        self.assertEqual(
            (view.station_filter.get(), view.type_filter.get(), view.state_filter.get()),
            (ALL_STATIONS, ALL_TYPES, ALL_STATES),
        )
        self.assertEqual(len(self.rows(view)), 11)

    def test_filter_combobox_command_refreshes_automatically(self):
        view = self.open_units()
        view.station_filter.set(SUR)
        view.station_filter._command(SUR)
        view.update()
        self.assertEqual(len(self.rows(view)), 3)

    def test_create_as_operator_resets_form_without_selection(self):
        view = self.open_units()
        self.fill(view)
        self.assertEqual(view.state_label.cget("text"), "Estado inicial: Disponible")
        view.create_button.invoke()
        view.update()
        code = self.code()
        self.assertIn("creada correctamente", view.message_label.cget("text"))
        row = self.row_for(view, code)
        self.assertEqual(row[1], self.plate())
        self.assertEqual(row[3:], (CENTRAL, "RESCATE", "Disponible", "Sí"))
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(view.capacity_entry.get(), "")
        self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)
        self.assertEqual(view.type_combo.get(), TYPE_PLACEHOLDER)
        self.assertIsNone(view.selected_id)
        self.assertEqual(str(view.create_button.cget("state")), "normal")
        self.assertEqual(self.state_of(self._id(code)), "DISPONIBLE")

    def _id(self, code):
        return db_all("SELECT id_unidad FROM unidades WHERE codigo = %s", (code,))[0][0]

    def test_create_keeps_search_and_filters(self):
        view = self.open_units()
        _set(view.search_entry, self.prefix)
        view.station_filter.set(NORTE)
        view.type_filter.set("CISTERNA")
        view.state_filter.set("Disponible")
        self.create(view, station=NORTE, type_name="CISTERNA")
        self.assertEqual(view.search_entry.get(), self.prefix)
        self.assertEqual(view.station_filter.get(), NORTE)
        self.assertEqual(view.type_filter.get(), "CISTERNA")
        self.assertEqual(view.state_filter.get(), "Disponible")
        self.assertEqual(len(self.rows(view)), 1)

    def test_duplicate_code_and_plate_show_friendly_messages(self):
        view = self.open_units()
        self.create(view)
        self.create(view)
        self.assertEqual(view.message_label.cget("text"), "Ya existe una unidad con ese código.")
        self.assertEqual(view.message_label.cget("text_color"), unit_view.COLOR_ERROR)
        self.assertEqual(self.count_prefix(), 1)
        self.fill(view, "B", plate=self.plate("A"))
        view.create_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "Ya existe una unidad con esa placa.")
        self.assertEqual(self.count_prefix(), 1)
        self.assertEqual(len([r for r in self.rows(view) if r[0].startswith(self.prefix)]), 1)

    def test_invalid_capacity_is_rejected_by_controller(self):
        view = self.open_units()
        for value in ("0", "-1", "abc"):
            with self.subTest(value=value):
                self.create(view, capacity=value)
                self.assertEqual(
                    view.message_label.cget("text"),
                    "La capacidad debe ser un número entero entre 1 y 255.",
                )
                self.assertEqual(self.count_prefix(), 0)
                self.assertEqual(view.code_entry.get(), self.code())

    def test_empty_station_or_type_shows_controller_message(self):
        view = self.open_units()
        self.create(view, station=STATION_PLACEHOLDER)
        self.assertEqual(view.message_label.cget("text"), "La estación es obligatoria.")
        self.create(view, type_name=TYPE_PLACEHOLDER)
        self.assertEqual(view.message_label.cget("text"), "El tipo de unidad es obligatorio.")
        self.create(view, code="")
        self.assertEqual(view.message_label.cget("text"), "El código es obligatorio.")
        self.assertEqual(self.count_prefix(), 0)

    def test_select_loads_form_and_state_is_read_only(self):
        view = self.open_units()
        code = self.create(view, station=SUR, type_name="CISTERNA", capacity="7")
        self.select(view, code)
        self.assertEqual(view.code_entry.get(), code)
        self.assertEqual(view.plate_entry.get(), self.plate())
        self.assertEqual(view.capacity_entry.get(), "7")
        self.assertEqual(view.description_entry.get(), "Temporal")
        self.assertEqual(view.station_combo.get(), SUR)
        self.assertEqual(view.type_combo.get(), "CISTERNA")
        self.assertEqual(view.state_label.cget("text"), "Estado operativo: Disponible")
        self.assertEqual(str(view.create_button.cget("state")), "disabled")
        self.assertEqual(str(view.save_button.cget("state")), "normal")
        self.assertEqual(str(view.deactivate_button.cget("state")), "normal")
        self.assertEqual(str(view.activate_button.cget("state")), "disabled")
        # Ningún combobox del formulario ofrece estados operativos
        for combo in (view.station_combo, view.type_combo):
            self.assertFalse(any("servicio" in v for v in combo.cget("values")))
        self.assertIsInstance(view.state_label, ctk.CTkLabel)
        self.assertNotIn("Fuera de servicio", list(view.station_combo.cget("values")))
        self.assertNotIn("Fuera de servicio", list(view.type_combo.cget("values")))

    def test_edit_persists_admin_fields_and_keeps_state(self):
        unit = self.make_unit()
        self.set_state(unit["unit_id"], "MANTENIMIENTO")
        view = self.open_units("admin.demo")
        self.select(view, unit["code"])
        self.assertEqual(view.state_label.cget("text"), "Estado operativo: Mantenimiento")
        _set(view.name_entry, "Nombre editado")
        _set(view.capacity_entry, "9")
        view.station_combo.set(NORTE)
        view.type_combo.set("HAZMAT")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        row = self.row_for(view, unit["code"])
        self.assertEqual(row[2], "Nombre editado")
        self.assertEqual(row[3:6], (NORTE, "HAZMAT", "Mantenimiento"))
        db = db_all(
            "SELECT nombre, capacidad_personal FROM unidades WHERE id_unidad = %s",
            (unit["unit_id"],),
        )[0]
        self.assertEqual(db, ("Nombre editado", 9))
        self.assertEqual(self.state_of(unit["unit_id"]), "MANTENIMIENTO")

    def test_create_mode_offers_only_active_stations_and_types(self):
        station_id = self.temp_station(active=False)
        type_id = self.temp_type(active=False)
        active_station = self.temp_station()
        active_type = self.temp_type()
        view = self.open_units()
        stations = list(view.station_combo.cget("values"))
        types = list(view.type_combo.cget("values"))
        self.assertNotIn(self.station_text(station_id, False), stations)
        self.assertNotIn(self.station_text(station_id), stations)
        self.assertIn(self.station_text(active_station), stations)
        for seed in (CENTRAL, NORTE, SUR):
            self.assertIn(seed, stations)
        self.assertNotIn(self.type_text(type_id), types)
        self.assertNotIn(self.type_text(type_id, False), types)
        self.assertIn(self.type_text(active_type), types)
        for seed in SEED_TYPES:
            self.assertIn(seed, types)
        # Los filtros sí conservan lo inactivo, marcado
        self.assertIn(self.station_text(station_id, False), list(view.station_filter.cget("values")))
        self.assertIn(self.type_text(type_id, False), list(view.type_filter.cget("values")))

    def test_edit_mode_shows_own_inactive_station_and_type_only(self):
        station_id = self.temp_station()
        type_id = self.temp_type()
        other_station = self.temp_station(active=False)
        other_type = self.temp_type(active=False)
        unit = self.make_unit(station_id=station_id, type_id=type_id)
        self.deactivate_temp_type(type_id)
        from models import station_model
        station_model.set_station_active(station_id, False)
        view = self.open_units("admin.demo")
        self.select(view, unit["code"])
        own_station = self.station_text(station_id, False)
        own_type = self.type_text(type_id, False)
        self.assertEqual(view.station_combo.get(), own_station)
        self.assertEqual(view.type_combo.get(), own_type)
        stations = list(view.station_combo.cget("values"))
        types = list(view.type_combo.cget("values"))
        self.assertIn(own_station, stations)
        self.assertIn(own_type, types)
        self.assertFalse(any(self.station_text(other_station, False)[:12] in s for s in stations))
        self.assertNotIn(self.type_text(other_type, False), types)
        # Guardar otros campos conservando estación y tipo inactivos funciona
        _set(view.name_entry, "Sigue en inactiva")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        self.assertEqual(self.row_for(view, unit["code"])[2], "Sigue en inactiva")
        # Mover a activos funciona y limpia las opciones inactivas
        view.station_combo.set(SUR)
        view.type_combo.set("RESCATE")
        view.save_button.invoke()
        view.update()
        self.assertIn("actualizada correctamente", view.message_label.cget("text"))
        self.assertEqual(self.row_for(view, unit["code"])[3:5], (SUR, "RESCATE"))
        self.assertNotIn(own_station, list(view.station_combo.cget("values")))
        view.clear_button.invoke()
        view.update()
        self.assertNotIn(own_station, list(view.station_combo.cget("values")))
        self.assertNotIn(own_type, list(view.type_combo.cget("values")))

    def test_forced_save_to_inactive_destination_shows_backend_message(self):
        inactive_station = self.temp_station(active=False)
        view = self.open_units()
        code = self.create(view)
        self.select(view, code)
        view.station_combo.set(self.station_text(inactive_station, False))
        view.save_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text_color"), unit_view.COLOR_ERROR)
        self.assertEqual(self.row_for(view, code)[3], CENTRAL)

    def test_deactivate_and_reactivate_temp_unit(self):
        unit = self.make_unit()
        uid = unit["unit_id"]
        view = self.open_units()
        self.select(view, unit["code"])
        # Confirmación negativa no cambia nada
        view.confirm = lambda message: False
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, unit["code"])[5:], ("Disponible", "Sí"))
        self.assertEqual(self.active_of(uid), 1)
        asked = []
        view.confirm = lambda message: asked.append(message) or True
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(len(asked), 1)
        self.assertIn("Fuera de servicio", asked[0])
        self.assertEqual(self.row_for(view, unit["code"])[5:], ("Fuera de servicio", "No"))
        self.assertEqual(view.state_label.cget("text"), "Estado operativo: Fuera de servicio")
        self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
        self.assertTrue(any(r[1] == "DESACTIVAR" for r in self.audit_rows(uid)))
        self.assertEqual(str(view.activate_button.cget("state")), "normal")
        self.assertEqual(str(view.deactivate_button.cget("state")), "disabled")
        view.activate_button.invoke()
        view.update()
        self.assertEqual(self.row_for(view, unit["code"])[5:], ("Fuera de servicio", "Sí"))
        self.assertEqual(view.message_label.cget("text"), unit_view.MSG_REACTIVATED)
        self.assertIn("Fuera de servicio", view.message_label.cget("text"))
        self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
        self.assertEqual(self.active_of(uid), 1)

    def test_deactivate_busy_unit_shows_message_and_changes_nothing(self):
        for state in ("DESPACHADA", "EN_RUTA"):
            with self.subTest(state=state):
                unit = self.make_unit(suffix=state[:3])
                self.set_state(unit["unit_id"], state)
                view = self.open_units()
                self.select(view, unit["code"])
                before = self.row_for(view, unit["code"])
                view.confirm = lambda message: True
                view.deactivate_button.invoke()
                view.update()
                self.assertEqual(view.message_label.cget("text_color"), unit_view.COLOR_ERROR)
                self.assertNotEqual(view.message_label.cget("text"), "")
                self.assertEqual(self.row_for(view, unit["code"]), before)
                self.assertEqual(self.active_of(unit["unit_id"]), 1)
                self.assertEqual(self.state_of(unit["unit_id"]), state)
                view.close()
                session.end_session()

    def test_clear_resets_form(self):
        view = self.open_units()
        code = self.create(view)
        self.select(view, code)
        view.clear_button.invoke()
        view.update()
        self.assertEqual(view.tree.selection(), ())
        self.assertEqual(view.code_entry.get(), "")
        self.assertEqual(view.station_combo.get(), STATION_PLACEHOLDER)
        self.assertEqual(view.type_combo.get(), TYPE_PLACEHOLDER)
        self.assertEqual(view.state_label.cget("text"), "Estado inicial: Disponible")
        self.assertEqual(view.message_label.cget("text"), "")
        self.assertEqual(str(view.create_button.cget("state")), "normal")

    def test_missing_unit_resets_and_refreshes(self):
        view = self.open_units()
        code = self.create(view)
        self.select(view, code)
        uid = view.selected_id
        from tests._unit_support import db_exec
        db_exec("DELETE FROM unidades WHERE id_unidad = %s", (uid,))
        view.confirm = lambda message: True
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), "La unidad ya no existe.")
        self.assertIsNone(view.selected_id)
        self.assertIsNone(self.row_for(view, code))

    def test_unexpected_error_shows_generic_message(self):
        view = self.open_units()
        self.fill(view)
        with mock.patch.object(
            unit_controller, "create_unit", side_effect=UnitServiceError("SELECT x")
        ), self.assertLogs("views.unit_view", level="ERROR"):
            view.create_button.invoke()
            view.update()
        text = view.message_label.cget("text")
        self.assertEqual(text, unit_view.MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)

    def test_authorization_denied_shows_permission_message(self):
        view = self.open_units()
        self.fill(view)
        with mock.patch.object(
            unit_controller, "create_unit", side_effect=AuthorizationDenied("x")
        ):
            view.create_button.invoke()
            view.update()
        self.assertEqual(view.message_label.cget("text"), unit_view.MSG_DENIED)

    def test_forced_open_as_read_only_user_is_denied(self):
        view = self.open_units("consulta.sur")
        self.assertEqual(self.rows(view), [])
        self.assertEqual(view.message_label.cget("text"), unit_view.MSG_DENIED)
        self.fill(view)
        view.create_button.invoke()
        view.update()
        self.assertEqual(view.message_label.cget("text"), unit_view.MSG_DENIED)
        self.assertEqual(self.count_prefix(), 0)

    def test_window_fits_screen_and_source_is_clean(self):
        view = self.open_units()
        self.assertLessEqual(view.winfo_reqwidth(), 1600)
        self.assertLessEqual(view.winfo_reqheight(), 900)
        for forbidden in (
            "ADMINISTRADOR", "OPERADOR", "CONSULTA", "import mysql", "import bcrypt",
            "config.database", "from models", "import models", "station_controller",
            "personnel_controller",
        ):
            self.assertNotIn(forbidden, SOURCE)

    def test_no_delete_or_operational_buttons(self):
        view = self.open_units()
        texts = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    texts.add(child.cget("text"))
                walk(child)

        walk(view)
        self.assertEqual(texts, {
            "Buscar", "Quitar filtros", "Guardar nuevo", "Guardar cambios", "Activar",
            "Desactivar", "Enviar a mantenimiento", "Volver a servicio", "Limpiar", "Regresar",
        })

    def maintenance_buttons(self, view):
        return (str(view.maintenance_button.cget("state")),
                str(view.service_button.cget("state")))

    def test_maintenance_buttons_enablement_matrix(self):
        station = self.temp_station()
        cases = {}
        for suffix, state_code in (
            ("DIS", "DISPONIBLE"), ("MAN", "MANTENIMIENTO"), ("FDS", "FUERA_DE_SERVICIO"),
            ("DSP", "DESPACHADA"), ("RUT", "EN_RUTA"), ("ESC", "EN_ESCENA"),
            ("REG", "REGRESANDO"),
        ):
            unit = self.make_unit(suffix)
            if state_code != "DISPONIBLE":
                self.set_state(unit["unit_id"], state_code)
            cases[suffix] = unit
        inactive = self.make_unit("INA")
        self.set_state(inactive["unit_id"], "FUERA_DE_SERVICIO")
        db_exec("UPDATE unidades SET activo = 0 WHERE id_unidad = %s", (inactive["unit_id"],))
        stranded = self.make_unit("EST", station_id=station)
        self.set_state(stranded["unit_id"], "MANTENIMIENTO")
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station,))
        cases["INA"] = inactive
        cases["EST"] = stranded
        expected = {
            "DIS": ("normal", "disabled"), "MAN": ("disabled", "normal"),
            "FDS": ("disabled", "normal"), "DSP": ("disabled", "disabled"),
            "RUT": ("disabled", "disabled"), "ESC": ("disabled", "disabled"),
            "REG": ("disabled", "disabled"), "INA": ("disabled", "disabled"),
            "EST": ("disabled", "disabled"),
        }
        view = self.open_units("operador.central")
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "disabled"))
        for suffix, buttons in expected.items():
            self.select(view, cases[suffix]["code"])
            self.assertEqual(self.maintenance_buttons(view), buttons, suffix)
        view.clear_button.invoke()
        view.update()
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "disabled"))

    def test_maintenance_and_service_flow(self):
        unit = self.make_unit("FLW")
        uid = unit["unit_id"]
        view = self.open_units("operador.central")
        self.select(view, unit["code"])
        view.confirm = lambda message: False
        view.maintenance_button.invoke()
        view.update()
        self.assertEqual(self.state_of(uid), "DISPONIBLE")
        asked = []
        view.confirm = lambda message: asked.append(message) or True
        view.maintenance_button.invoke()
        view.update()
        self.assertIn(unit["code"], asked[0])
        self.assertEqual(self.state_of(uid), "MANTENIMIENTO")
        self.assertEqual(view.state_label.cget("text"), "Estado operativo: Mantenimiento")
        self.assertEqual(self.row_for(view, unit["code"])[5], "Mantenimiento")
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "normal"))
        self.assertIn("mantenimiento", view.message_label.cget("text"))
        view.confirm = lambda message: False
        view.service_button.invoke()
        self.assertEqual(self.state_of(uid), "MANTENIMIENTO")
        view.confirm = lambda message: asked.append(message) or True
        view.service_button.invoke()
        view.update()
        self.assertIn("aptitud", asked[-1])
        self.assertIn("Disponible", asked[-1])
        self.assertEqual(self.state_of(uid), "DISPONIBLE")
        self.assertEqual(self.row_for(view, unit["code"])[5], "Disponible")
        self.assertEqual(self.maintenance_buttons(view), ("normal", "disabled"))

        # Desactivar, reactivar (queda Fuera de servicio) y volver a servicio
        view.deactivate_button.invoke()
        view.update()
        self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "disabled"))
        view.activate_button.invoke()
        view.update()
        self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "normal"))
        view.service_button.invoke()
        view.update()
        self.assertEqual(self.state_of(uid), "DISPONIBLE")
        self.assertEqual(self.active_of(uid), 1)
        self.assertEqual(self.row_for(view, unit["code"])[5:], ("Disponible", "Sí"))

    def test_maintenance_with_stale_state_shows_message_and_reloads(self):
        unit = self.make_unit("STA")
        uid = unit["unit_id"]
        view = self.open_units("operador.central")
        self.select(view, unit["code"])
        self.set_state(uid, "DESPACHADA")
        view.confirm = lambda message: True
        view.maintenance_button.invoke()
        view.update()
        text = view.message_label.cget("text")
        self.assertNotEqual(text, "")
        self.assertNotEqual(text, unit_view.MSG_INTERNAL)
        self.assertEqual(self.state_of(uid), "DESPACHADA")
        self.assertEqual(view.state_label.cget("text"), "Estado operativo: Despachada")
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "disabled"))

    def test_maintenance_buttons_denied_for_read_only_user(self):
        unit = self.make_unit("RO")
        uid = unit["unit_id"]
        view = self.open_units("consulta.sur")
        self.assertEqual(self.maintenance_buttons(view), ("disabled", "disabled"))
        view.selected_id = uid
        view.selected_active = True
        view.selected_state_code = "DISPONIBLE"
        view.code_entry.insert(0, unit["code"])
        view.confirm = lambda message: True
        view.send_to_maintenance()
        self.assertEqual(view.message_label.cget("text"), unit_view.MSG_DENIED)
        view.return_to_service()
        self.assertEqual(view.message_label.cget("text"), unit_view.MSG_DENIED)
        self.assertEqual(self.state_of(uid), "DISPONIBLE")

    def test_maintenance_service_error_is_generic(self):
        unit = self.make_unit("ERR")
        view = self.open_units("operador.central")
        self.select(view, unit["code"])
        view.confirm = lambda message: True
        error = UnitServiceError("SELECT * FROM unidades Traceback")
        with mock.patch.object(unit_controller, "send_unit_to_maintenance", side_effect=error), \
                self.assertLogs("views.unit_view", level="ERROR"):
            view.maintenance_button.invoke()
        text = view.message_label.cget("text")
        self.assertEqual(text, unit_view.MSG_INTERNAL)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("SELECT", text)

    def buttons_of(self, main):
        return [
            w.cget("text") for w in main.winfo_children()[0].winfo_children()
            if isinstance(w, ctk.CTkButton)
        ]

    def test_read_only_main_view_has_no_units_or_personnel_button(self):
        user = _login("consulta.sur")
        main = MainView(user)
        self.windows.append(main)
        main.update()
        texts = self.buttons_of(main)
        self.assertNotIn("Unidades", texts)
        self.assertNotIn("Personal", texts)
        self.assertIsNone(main.units_button)

    def test_main_view_opens_reuses_and_closes_module(self):
        for username in ("admin.demo", "operador.central"):
            with self.subTest(username=username):
                user = _login(username)
                main = MainView(user)
                self.windows.append(main)
                main.update()
                button = main.units_button
                self.assertEqual(button.cget("text"), "Unidades")
                button.invoke()
                main.update()
                first = main.unit_view
                self.assertIsInstance(first, UnitView)
                self.assertEqual(len(self.rows(first)), 11)
                button.invoke()
                main.update()
                self.assertIs(main.unit_view, first)
                first.back_button.invoke()
                main.update()
                self.assertFalse(first.winfo_exists())
                if username == "operador.central":
                    texts = self.buttons_of(main)
                    self.assertNotIn("Estaciones", texts)
                    self.assertNotIn("Usuarios", texts)
                    self.assertIn("Personal", texts)
                main.destroy()
                session.end_session()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest import mock

import mysql.connector

from controllers import access_controller
from controllers import unit_controller as uc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import station_model, unit_model
from tests._unit_support import UnitTestBase, db_all
from utils import session
from utils.permissions import PERMISSION_MANAGE_CATALOGS

DEMO_PASSWORD = "FireDemo2026!"
STATE_KEYS = ("state", "state_id", "state_code", "status", "id_estado_unidad")


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class UnitControllerTest(UnitTestBase):
    SCENARIOS = [
        ("ACTIVO", "ASSIGNED", True),
        ("ACTIVO", "RELEASED", False),
        ("ACTIVO", "CANCELLED", False),
        ("ACTIVO", "PRESELECTION", False),
        ("COMPLETADO", "ASSIGNED", False),
        ("CANCELADO", "ASSIGNED", False),
        ("CREADO", "PRESELECTION", False),
    ]
    def setUp(self):
        super().setUp()
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def _data(self, suffix="A", **overrides):
        data = {
            "code": f"{self.prefix}-{suffix}", "name": "Unidad temporal",
            "plate": f"{self.plate_prefix}-{suffix}", "capacity": 4,
            "description": "Desc", "station_id": self.seed_station,
            "type_id": self.seed_type,
        }
        data.update(overrides)
        return data

    def _create(self, suffix="A", **overrides):
        return uc.create_unit(self._data(suffix, **overrides))

    def _ids(self, units):
        return [u["unit_id"] for u in units]

    def test_list_search_filter(self):
        units = uc.list_units()
        self.assertEqual(len([u for u in units if not u["code"].startswith("TUN-")]), 11)
        keys = {"unit_id", "code", "name", "plate", "capacity", "description", "active",
                "station_id", "station_code", "station_name", "type_id", "type_name",
                "state_id", "state_code", "state_name"}
        self.assertTrue(all(set(u) == keys for u in units))
        unit = self._create(name="Zzcamion")
        uid = unit["unit_id"]
        for term in (self.prefix, "  ZZCAMION ", self.plate_prefix):
            self.assertEqual(self._ids(uc.search_units(term)), [uid], term)
        self.assertEqual(uc.search_units("inexistente-" + self.prefix), [])
        self.assertEqual(len(uc.search_units("  ")), len(uc.list_units()))
        self.assertEqual(len(uc.search_units(None)), len(uc.list_units()))
        self.assertEqual(self._ids(uc.filter_units(query=self.prefix, station_id=self.seed_station,
                                                   type_id=self.seed_type)), [uid])
        self.assertEqual(uc.filter_units(query=self.prefix, station_id=self.temp_station()), [])
        states = {s["code"]: s["state_id"] for s in uc.list_unit_states()}
        disp = uc.filter_units(state_id=states["DISPONIBLE"])
        self.assertTrue(all(u["state_code"] == "DISPONIBLE" for u in disp))
        self.assertIn(uid, self._ids(disp))
        self.assertEqual(uc.filter_units(station_id=1, state_id=states["DESPACHADA"])[0]["unit_id"], 2)
        for bad in (True, "1", 1.0):
            for kwargs in ({"station_id": bad}, {"type_id": bad}, {"state_id": bad}):
                with self.assertRaises(uc.UnitValidationError):
                    uc.filter_units(**kwargs)

    def test_wildcards(self):
        special = self._create("S", name="100%_x\\y")["unit_id"]
        plain = self._create("P", name="abc")["unit_id"]
        for term in ("%", "_", "\\"):
            found = self._ids(uc.search_units(term))
            self.assertIn(special, found)
            self.assertNotIn(plain, found)

    def test_lookups(self):
        types = uc.list_unit_types()
        self.assertTrue(all(set(t) == {"type_id", "name", "description", "active"} for t in types))
        self.assertEqual(len(uc.list_unit_states()), 7)
        inactive_station = self.temp_station(active=False)
        found = [s for s in uc.list_stations_for_units() if s["station_id"] == inactive_station]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["active"])
        self.assertEqual(set(found[0]), {"station_id", "code", "name", "active"})

    def test_create_valid(self):
        unit = self._create(code=f"  {self.prefix}-A  ", name=" Nom ", capacity="7")
        self.assertEqual(unit["code"], f"{self.prefix}-A")
        self.assertEqual(unit["name"], "Nom")
        self.assertEqual(unit["capacity"], 7)
        self.assertTrue(unit["active"])
        self.assertEqual(unit["state_code"], "DISPONIBLE")
        self.assertEqual(uc.get_unit(unit["unit_id"]), unit)

    def test_empty_optionals(self):
        for index, plate in enumerate(("", "   ", None)):
            unit = self._create(f"N{index}", plate=plate, capacity=None if index else "",
                                name="", description=" ")
            self.assertIsNone(unit["plate"])
            self.assertIsNone(unit["capacity"])
            self.assertIsNone(unit["name"])
            self.assertIsNone(unit["description"])

    def test_capacity_limits(self):
        for good in (None, 1, 255):
            self.assertEqual(self._create(f"C{good}", capacity=good)["capacity"], good)
        for bad in (0, -1, 256, "abc", 1.5, True, "0", "-3", "1.5"):
            with self.assertRaises(uc.UnitValidationError, msg=repr(bad)):
                self._create("CB", capacity=bad)
        self.assertEqual(self.count_prefix(), 3)

    def test_required_and_length(self):
        bad = [{"code": ""}, {"code": "  "}, {"code": None}, {"code": "x" * 21},
               {"name": "x" * 81}, {"plate": "x" * 21}, {"description": "x" * 201},
               {"name": 5}, {"station_id": None}, {"type_id": None}]
        for overrides in bad:
            with self.assertRaises(uc.UnitValidationError, msg=str(overrides)):
                self._create("R", **overrides)
        with self.assertRaises(uc.UnitValidationError):
            uc.create_unit(None)
        self.assertEqual(self.count_prefix(), 0)
        self._create("L", code=self.prefix + "x" * (20 - len(self.prefix)),
                     name="n" * 80, plate="p" * 20, description="d" * 200)

    def test_duplicates(self):
        self._create()
        with self.assertRaises(uc.DuplicateUnitCodeError) as ctx:
            self._create("B", code=f"{self.prefix}-A")
        self.assertEqual(str(ctx.exception), "Ya existe una unidad con ese código.")
        with self.assertRaises(uc.DuplicateUnitPlateError) as ctx:
            self._create("C", plate=f"{self.plate_prefix}-A")
        self.assertEqual(str(ctx.exception), "Ya existe una unidad con esa placa.")
        self.assertEqual(self.count_prefix(), 1)
        self._create("D", plate=None)
        self._create("E", plate="")
        self.assertEqual(db_all("SELECT COUNT(*) FROM unidades WHERE codigo LIKE %s AND placa IS NULL",
                                (self.prefix + "%",))[0][0], 2)

    def test_invalid_station_type_ids(self):
        for bad in (999999, 0, -1, "abc", None, True):
            with self.assertRaises(uc.UnitValidationError, msg=repr(bad)):
                self._create("X", station_id=bad)
            with self.assertRaises(uc.UnitValidationError, msg=repr(bad)):
                self._create("X", type_id=bad)
        with self.assertRaises(uc.InvalidStationError):
            self._create("X", station_id=999999)
        with self.assertRaises(uc.InvalidTypeError):
            self._create("X", type_id=999999)
        self.assertEqual(self.count_prefix(), 0)
        # saltando el pre-chequeo, el modelo sigue rechazando
        with mock.patch.object(station_model, "get_station", return_value={"station_id": 1}):
            with self.assertRaises(uc.InvalidStationError):
                self._create("G", station_id=999999)
        with self.assertRaises(unit_model.InvalidStationError):
            unit_model.create_unit(f"{self.prefix}-M", None, None, None, None, 999999, self.seed_type)
        with self.assertRaises(unit_model.InvalidTypeError):
            unit_model.create_unit(f"{self.prefix}-M", None, None, None, None,
                                   self.seed_station, 999999)
        self.assertEqual(self.count_prefix(), 0)

    def test_inactive_station(self):
        inactive = self.temp_station(active=False)
        with self.assertRaises(uc.InactiveStationError) as ctx:
            self._create(station_id=inactive)
        self.assertEqual(str(ctx.exception),
                         "No se puede asignar una unidad a una estación inactiva.")
        self.assertIsInstance(ctx.exception, uc.UnitValidationError)
        self.assertEqual(self.count_prefix(), 0)
        station = self.temp_station()
        unit = self._create(station_id=station)
        uid = unit["unit_id"]
        before = self.row(uid)
        with self.assertRaises(uc.InactiveStationError):
            uc.update_unit(uid, self._data(station_id=inactive))
        self.assertEqual(self.row(uid), before)
        station_model.set_station_active(station, False)
        edited = uc.update_unit(uid, self._data(name="Editada", station_id=station))
        self.assertEqual(edited["name"], "Editada")
        self.assertIn(uid, self._ids(uc.list_units()))
        with self.assertRaises(uc.InactiveStationError):
            uc.update_unit(uid, self._data(station_id=self.temp_station(active=False)))
        moved = uc.update_unit(uid, self._data(station_id=self.seed_station))
        self.assertEqual(moved["station_id"], self.seed_station)

    def test_inactive_type(self):
        inactive = self.temp_type(active=False)
        with self.assertRaises(uc.InactiveTypeError) as ctx:
            self._create(type_id=inactive)
        self.assertEqual(str(ctx.exception), "No se puede asignar un tipo de unidad inactivo.")
        legacy = self.temp_type()
        uid = self._create("B", type_id=legacy)["unit_id"]
        self.deactivate_temp_type(legacy)
        before = self.row(uid)
        with self.assertRaises(uc.InactiveTypeError):
            uc.update_unit(uid, self._data("B", type_id=inactive))
        self.assertEqual(self.row(uid), before)
        edited = uc.update_unit(uid, self._data("B", name="Otra", type_id=legacy))
        self.assertEqual(edited["type_id"], legacy)
        self.assertIn(uid, self._ids(uc.list_units()))
        self.assertFalse(next(t for t in uc.list_unit_types() if t["type_id"] == legacy)["active"])
        with self.assertRaises(uc.InvalidTypeError):
            uc.update_unit(uid, self._data("B", type_id=999999))

    def test_update_persists_state_unchanged(self):
        station, type_id = self.temp_station(), self.temp_type()
        uid = self._create()["unit_id"]
        self.set_state(uid, "MANTENIMIENTO")
        state_before = db_all("SELECT id_estado_unidad FROM unidades WHERE id_unidad=%s", (uid,))
        data = self._data(code=f"{self.prefix}-N", name="N2", plate=f"{self.plate_prefix}-N",
                          capacity=8, description="D2", station_id=station, type_id=type_id)
        unit = uc.update_unit(uid, data)
        self.assertEqual((unit["code"], unit["name"], unit["plate"], unit["capacity"],
                          unit["description"], unit["station_id"], unit["type_id"]),
                         (data["code"], "N2", data["plate"], 8, "D2", station, type_id))
        self.assertEqual(unit["state_code"], "MANTENIMIENTO")
        self.assertEqual(db_all("SELECT id_estado_unidad FROM unidades WHERE id_unidad=%s", (uid,)),
                         state_before)
        self.assertEqual(uc.update_unit(uid, data)["unit_id"], uid)
        cleared = uc.update_unit(uid, self._data(code=f"{self.prefix}-N", plate=None,
                                                 capacity=None, station_id=station, type_id=type_id))
        self.assertIsNone(cleared["plate"])
        self.assertIsNone(cleared["capacity"])

    def test_update_not_found_and_invalid_ids(self):
        with self.assertRaises(uc.UnitNotFoundError):
            uc.update_unit(4000000000, self._data())
        for func in (uc.get_unit, uc.activate_unit, uc.deactivate_unit):
            with self.assertRaises(uc.UnitNotFoundError):
                func(4000000000)
            for bad in (True, "1", None, 1.0):
                with self.assertRaises(uc.UnitValidationError):
                    func(bad)
        self.assertEqual(self.count_prefix(), 0)

    def test_update_duplicates(self):
        first = self._create("A")["unit_id"]
        second = self._create("B")["unit_id"]
        before = (self.row(first), self.row(second))
        with self.assertRaises(uc.DuplicateUnitCodeError):
            uc.update_unit(second, self._data("B", code=f"{self.prefix}-A"))
        with self.assertRaises(uc.DuplicateUnitPlateError):
            uc.update_unit(second, self._data("B", plate=f"{self.plate_prefix}-A"))
        self.assertEqual((self.row(first), self.row(second)), before)

    def test_state_keys_rejected(self):
        uid = self._create()["unit_id"]
        before = self.row(uid)
        for key in STATE_KEYS:
            value = 1 if key in ("state_id", "id_estado_unidad") else "FUERA_DE_SERVICIO"
            with self.assertRaises(uc.UnitValidationError, msg=key) as ctx:
                self._create("S" + key[:3], **{key: value})
            self.assertEqual(str(ctx.exception),
                             "El estado operativo no se puede modificar desde este módulo.")
            with self.assertRaises(uc.UnitValidationError, msg=key):
                uc.update_unit(uid, self._data(**{key: value}))
        self.assertEqual(self.row(uid), before)
        self.assertEqual(self.count_prefix(), 1)
        with self.assertRaises(uc.UnitValidationError) as ctx:
            uc.update_unit(uid, self._data(active=False))
        self.assertIn("Campo no permitido", str(ctx.exception))
        with self.assertRaises(uc.UnitValidationError):
            self._create("U", unit_id=5)
        self.assertEqual(self.row(uid), before)
        self.assertFalse(any(name.startswith(("set_state", "change_state", "update_state"))
                             for name in dir(uc)))

    def test_transfer_rule(self):
        target = self.temp_station()
        for code in ("DESPACHADA", "EN_RUTA", "EN_ESCENA"):
            suffix = code[:3] + code[-2:]
            uid = self._create(suffix)["unit_id"]
            self.set_state(uid, code)
            before = self.row(uid)
            with self.assertRaises(uc.UnitBusyError, msg=code) as ctx:
                uc.update_unit(uid, self._data(suffix, station_id=target))
            self.assertIn(code, str(ctx.exception))
            self.assertEqual(self.row(uid), before)
        for code in ("DISPONIBLE", "MANTENIMIENTO", "REGRESANDO", "FUERA_DE_SERVICIO"):
            suffix = "T" + code[:3] + code[-2:]
            uid = self._create(suffix)["unit_id"]
            self.set_state(uid, code)
            moved = uc.update_unit(uid, self._data(suffix, station_id=target))
            self.assertEqual(moved["station_id"], target)
            self.assertEqual(moved["state_code"], code)

    def test_deactivate_and_reactivate(self):
        uid = self._create()["unit_id"]
        admin_id = self.user_id("admin.demo")
        unit = uc.deactivate_unit(uid)
        self.assertFalse(unit["active"])
        self.assertEqual(unit["state_code"], "FUERA_DE_SERVICIO")
        self.assertEqual(self.active_of(uid), 0)
        self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
        audit = self.audit_rows(uid)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0][:4], (admin_id, "DESACTIVAR", "UNIDAD", uid))
        self.assertEqual(len(self.row(uid)), 1)
        self.assertIn(uid, self._ids(uc.list_units()))
        self.assertEqual(uc.deactivate_unit(uid)["unit_id"], uid)
        self.assertEqual(len(self.audit_rows(uid)), 1)
        react = uc.activate_unit(uid)
        self.assertTrue(react["active"])
        self.assertEqual(react["state_code"], "FUERA_DE_SERVICIO")
        self.assertEqual(len(self.audit_rows(uid)), 1)
        # reactivar una unidad activa no cambia nada
        other = self._create("B")["unit_id"]
        before = self.row(other)
        self.assertEqual(uc.activate_unit(other)["state_code"], "DISPONIBLE")
        self.assertEqual(self.row(other), before)

    def test_deactivate_busy(self):
        for code in ("DESPACHADA", "EN_RUTA", "EN_ESCENA"):
            uid = self._create(code[:3] + code[-2:])["unit_id"]
            self.set_state(uid, code)
            before = self.row(uid)
            with self.assertRaises(uc.UnitBusyError, msg=code):
                uc.deactivate_unit(uid)
            self.assertEqual(self.row(uid), before)
            self.assertEqual(self.audit_rows(uid), [])

    def test_deactivate_fds_stays_fds(self):
        uid = self._create()["unit_id"]
        self.set_state(uid, "FUERA_DE_SERVICIO")
        unit = uc.deactivate_unit(uid)
        self.assertFalse(unit["active"])
        self.assertEqual(unit["state_code"], "FUERA_DE_SERVICIO")
        self.assertEqual(len(self.audit_rows(uid)), 1)

    def test_deactivate_rollback_on_audit_failure(self):
        uid = self._create()["unit_id"]
        before = self.row(uid)
        fake = dict(session.get_session(), user_id=4000000000)
        with mock.patch.object(session, "get_session", return_value=fake):
            with self.assertRaises(uc.UnitValidationError):
                uc.deactivate_unit(uid)
        self.assertEqual(self.row(uid), before)
        self.assertEqual(self.audit_rows(uid), [])

    def test_operator_allowed_and_audited(self):
        session.end_session()
        _login("operador.central")
        uid = self._create()["unit_id"]
        self.assertIn(uid, self._ids(uc.search_units(self.prefix)))
        self.assertEqual(uc.update_unit(uid, self._data(name="Op"))["name"], "Op")
        uc.list_unit_types(), uc.list_unit_states(), uc.list_stations_for_units()
        uc.deactivate_unit(uid)
        self.assertEqual(self.audit_rows(uid)[0][0], self.user_id("operador.central"))
        self.assertTrue(uc.activate_unit(uid)["active"])
        self.assertFalse(access_controller.can(PERMISSION_MANAGE_CATALOGS))

    def test_consulta_denied_everywhere(self):
        uid = self._create()["unit_id"]
        before = self.row(uid)
        total = db_all("SELECT COUNT(*) FROM unidades")[0][0]
        audit_total = db_all("SELECT COUNT(*) FROM bitacora")[0][0]
        calls = [
            lambda: uc.list_units(), lambda: uc.search_units("x"),
            lambda: uc.filter_units(), lambda: uc.get_unit(uid),
            lambda: uc.create_unit(self._data("DENIED")),
            lambda: uc.update_unit(uid, self._data(name="Hack")),
            lambda: uc.activate_unit(uid), lambda: uc.deactivate_unit(uid),
            lambda: uc.list_unit_types(), lambda: uc.list_unit_states(),
            lambda: uc.list_stations_for_units(),
        ]
        session.end_session()
        _login("consulta.sur")
        for call in calls:
            with self.assertRaises(AuthorizationDenied):
                call()
        self.assertEqual(self.row(uid), before)
        self.assertEqual(db_all("SELECT COUNT(*) FROM unidades")[0][0], total)
        self.assertEqual(db_all("SELECT COUNT(*) FROM bitacora")[0][0], audit_total)

    def test_no_session(self):
        session.end_session()
        for call in (lambda: uc.list_units(), lambda: uc.create_unit(self._data()),
                     lambda: uc.deactivate_unit(1), lambda: uc.activate_unit(1),
                     lambda: uc.list_unit_types(), lambda: uc.list_unit_states(),
                     lambda: uc.list_stations_for_units(), lambda: uc.filter_units()):
            with self.assertRaises(NotAuthenticated):
                call()
        self.assertEqual(self.count_prefix(), 0)

    def test_technical_error_wrapped(self):
        error = mysql.connector.Error("fallo simulado SELECT secreto")
        with mock.patch.object(unit_model, "search_units", side_effect=error):
            with self.assertRaises(uc.UnitServiceError) as ctx:
                uc.list_units()
        self.assertNotIn("SELECT", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, error)
        with mock.patch.object(unit_model, "create_unit", side_effect=error):
            with self.assertRaises(uc.UnitServiceError):
                self._create()
        with mock.patch.object(unit_model, "set_unit_active", side_effect=error):
            with self.assertRaises(uc.UnitServiceError):
                uc.deactivate_unit(1)
        with mock.patch.object(station_model, "list_stations", side_effect=error):
            with self.assertRaises(uc.UnitServiceError):
                uc.list_stations_for_units()

    def test_participation_defensive_checks(self):
        target = self.temp_station()
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            for index, (dstate, kind, blocks) in enumerate(self.SCENARIOS):
                sfx = f"{username[:2]}D{index}"
                uid = self._create(sfx)["unit_id"]
                self.add_participation(uid, dstate, kind)
                if blocks:
                    before = self.row(uid)
                    with self.assertRaises(uc.UnitBusyError):
                        uc.deactivate_unit(uid)
                    with self.assertRaises(uc.UnitBusyError) as ctx:
                        uc.update_unit(uid, self._data(sfx, station_id=target))
                    self.assertIn("participación activa", str(ctx.exception))
                    self.assertEqual(self.row(uid), before)
                    self.assertEqual(self.audit_rows(uid), [])
                    self.assertEqual(
                        uc.update_unit(uid, self._data(sfx, name="Editada"))["name"], "Editada")
                else:
                    moved = uc.update_unit(uid, self._data(sfx, station_id=target))
                    self.assertEqual(moved["station_id"], target)
                    self.assertFalse(uc.deactivate_unit(uid)["active"])
                    self.assertEqual(len(self.audit_rows(uid)), 1)


if __name__ == "__main__":
    unittest.main()

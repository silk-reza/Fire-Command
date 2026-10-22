import unittest
from unittest import mock
from uuid import uuid4

import mysql.connector

from config.database import get_connection
from controllers import access_controller
from controllers import personnel_controller as pc
from controllers import station_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import personnel_model, station_model
from utils import session
from utils.permissions import PERMISSION_MANAGE_CATALOGS, PERMISSION_MANAGE_RESOURCES

DEMO_PASSWORD = "FireDemo2026!"


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


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


def _seed_snapshot():
    stations = _db_all(
        "SELECT * FROM estaciones WHERE codigo NOT LIKE 'TPS-%' ORDER BY id_estacion"
    )
    people = _db_all(
        "SELECT * FROM personal WHERE codigo NOT LIKE 'TPR-%' ORDER BY id_personal"
    )
    return stations, people


class PersonnelControllerTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.prefix = f"TPR-{uuid4().hex[:8]}"
        self.station_ids = []
        self.seed_before = _seed_snapshot()
        self.seed_station = _db_all(
            "SELECT id_estacion FROM estaciones WHERE codigo NOT LIKE 'TPS-%' "
            "ORDER BY id_estacion LIMIT 1"
        )[0][0]
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM personal WHERE codigo LIKE %s", (self.prefix + "%",))
            for station_id in self.station_ids:
                cursor.execute("DELETE FROM estaciones WHERE id_estacion = %s", (station_id,))
            connection.commit()
            cursor.close()
        finally:
            connection.close()
        self.assertEqual(_seed_snapshot(), self.seed_before)

    def _temp_station(self):
        station_id = station_model.create_station(
            f"TPS-{uuid4().hex[:8]}", "Estación temporal", "Dir", None, None, None, None
        )
        self.station_ids.append(station_id)
        return station_id

    def _data(self, suffix="A", **overrides):
        data = {
            "code": f"{self.prefix}-{suffix}",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "position": "Bombero",
            "phone": "",
            "station_id": self.seed_station,
        }
        data.update(overrides)
        return data

    def _create(self, suffix="A", **overrides):
        return pc.create_person(self._data(suffix, **overrides))

    def _row(self, person_id):
        return _db_all("SELECT * FROM personal WHERE id_personal = %s", (person_id,))

    def _count_prefix(self):
        return _db_all(
            "SELECT COUNT(*) FROM personal WHERE codigo LIKE %s", (self.prefix + "%",)
        )[0][0]

    def _ids(self, people):
        return [p["person_id"] for p in people]

    def test_list_seed_and_keys(self):
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            people = pc.list_personnel()
            codes = {p["code"] for p in people}
            self.assertTrue({f"PER-{n:03d}" for n in range(1, 16)} <= codes)
            keys = {"person_id", "code", "first_name", "last_name", "position", "phone",
                    "active", "station_id", "station_code", "station_name"}
            self.assertTrue(all(set(p) == keys for p in people))

    def test_search_variants(self):
        person = self._create(first_name="Zzjuan", last_name="Qwertyez", position="Zzcargo")
        for term in (self.prefix, "ZZJUAN", "  qwertyez ", "zzjuan qwerty", "zzcargo"):
            self.assertEqual(self._ids(pc.search_personnel(term)), [person["person_id"]], term)
        self.assertEqual(pc.search_personnel("inexistente-" + uuid4().hex), [])
        self.assertEqual(len(pc.search_personnel("   ")), len(pc.list_personnel()))
        self.assertEqual(len(pc.search_personnel(None)), len(pc.list_personnel()))
        # PER-001 del seed se encuentra por código
        self.assertIn("PER-001", [p["code"] for p in pc.search_personnel("PER-001")])

    def test_search_wildcards_safe(self):
        special = self._create("S", first_name="100%_x\\y")["person_id"]
        plain = self._create("P", first_name="abc")["person_id"]
        for term in ("%", "_", "\\"):
            self.assertEqual(set(self._ids(pc.search_personnel(term))), {special}, term)
        self.assertNotIn(plain, self._ids(pc.search_personnel("a%")))

    def test_station_filter_and_combined(self):
        other = self._temp_station()
        mine = self._create("M", first_name="Zzcomun")["person_id"]
        theirs = self._create("T", first_name="Zzcomun", station_id=other)["person_id"]
        only_other = self._create("O", first_name="Zzsolootra", station_id=other)["person_id"]
        by_station = pc.filter_personnel_by_station(other)
        self.assertEqual(set(self._ids(by_station)), {theirs, only_other})
        self.assertTrue(all(p["station_id"] == other for p in by_station))
        self.assertEqual(self._ids(pc.find_personnel("zzcomun", self.seed_station)), [mine])
        self.assertEqual(set(self._ids(pc.find_personnel("zzcomun", None))), {mine, theirs})
        self.assertEqual(set(self._ids(pc.find_personnel("  ", other))), {theirs, only_other})
        self.assertEqual(pc.find_personnel("zzsolootra", self.seed_station), [])
        seed_people = pc.filter_personnel_by_station(self.seed_station)
        self.assertTrue(all(p["station_id"] == self.seed_station for p in seed_people))
        self.assertTrue({"PER-001", "PER-006"} <= {p["code"] for p in seed_people})

    def test_create_valid(self):
        person = self._create(
            code=f"  {self.prefix}-A  ", first_name=" Ana ", last_name=" Lopez ",
            position=" Cabo ", phone=" 2222-3333 ",
        )
        self.assertEqual(person["code"], f"{self.prefix}-A")
        self.assertEqual(person["first_name"], "Ana")
        self.assertEqual(person["last_name"], "Lopez")
        self.assertEqual(person["position"], "Cabo")
        self.assertEqual(person["phone"], "2222-3333")
        self.assertEqual(person["station_id"], self.seed_station)
        self.assertTrue(person["active"])
        self.assertIn(person["person_id"], self._ids(pc.list_personnel()))
        self.assertEqual(pc.get_person(person["person_id"]), person)

    def test_empty_phone_is_null(self):
        for index, phone in enumerate(("", "   ", None)):
            person = self._create(f"N{index}", phone=phone)
            self.assertIsNone(person["phone"])
            self.assertIsNone(self._row(person["person_id"])[0][6])

    def test_duplicate_code(self):
        self._create()
        with self.assertRaises(pc.DuplicatePersonCodeError) as ctx:
            self._create()
        self.assertEqual(str(ctx.exception), "Ya existe una persona con ese código.")
        self.assertEqual(self._count_prefix(), 1)

    def test_nonexistent_station_rejected(self):
        for bad in (999999, 0, -1, "abc", None, True, 1.0):
            with self.assertRaises(pc.PersonnelValidationError, msg=repr(bad)):
                self._create("X", station_id=bad)
        with self.assertRaises(pc.InvalidStationError) as ctx:
            self._create("X", station_id=999999)
        self.assertEqual(str(ctx.exception), "La estación indicada no existe.")
        self.assertEqual(self._count_prefix(), 0)

    def test_model_fk_safeguard_bypassing_controller(self):
        with self.assertRaises(personnel_model.InvalidStationError):
            personnel_model.create_person(
                f"{self.prefix}-F", "N", "A", "C", None, 999999
            )
        self.assertEqual(self._count_prefix(), 0)
        # si el pre-chequeo se salta, el controlador mapea el error de la FK
        with mock.patch.object(station_model, "get_station", return_value={"station_id": 1}):
            with self.assertRaises(pc.InvalidStationError):
                self._create("G", station_id=999999)
        self.assertEqual(self._count_prefix(), 0)

    def test_required_and_length_limits(self):
        bad = [
            {"code": ""}, {"code": "   "}, {"code": None}, {"code": "x" * 21},
            {"first_name": ""}, {"first_name": "  "}, {"first_name": "x" * 81},
            {"last_name": ""}, {"last_name": " "}, {"last_name": "x" * 81},
            {"position": ""}, {"position": "  "}, {"position": "x" * 61},
            {"phone": "x" * 26}, {"station_id": None},
        ]
        for overrides in bad:
            with self.assertRaises(pc.PersonnelValidationError, msg=str(overrides)):
                pc.create_person(self._data("R", **overrides))
        with self.assertRaises(pc.PersonnelValidationError):
            pc.create_person(None)
        self.assertEqual(self._count_prefix(), 0)
        ok = pc.create_person(self._data(
            "L", code=self.prefix + "x" * (20 - len(self.prefix)),
            first_name="n" * 80, last_name="a" * 80, position="p" * 60, phone="1" * 25,
        ))
        self.assertEqual(len(ok["code"]), 20)

    def test_update_persists_and_changes_station(self):
        other = self._temp_station()
        person = self._create()
        updated = pc.update_person(person["person_id"], self._data(
            first_name="Nuevo", last_name="Otro", position="Cabo", phone="1234",
            station_id=other,
        ))
        self.assertEqual(updated["first_name"], "Nuevo")
        self.assertEqual(updated["station_id"], other)
        self.assertEqual(pc.get_person(person["person_id"]), updated)
        # update sin cambios no se confunde con inexistente
        self.assertEqual(
            pc.update_person(person["person_id"], self._data(
                first_name="Nuevo", last_name="Otro", position="Cabo", phone="1234",
                station_id=other,
            ))["person_id"],
            person["person_id"],
        )
        cleared = pc.update_person(person["person_id"], self._data(station_id=other))
        self.assertIsNone(cleared["phone"])

    def test_update_duplicate_code_leaves_both(self):
        first = self._create("A")
        second = self._create("B")
        before = (self._row(first["person_id"]), self._row(second["person_id"]))
        with self.assertRaises(pc.DuplicatePersonCodeError):
            pc.update_person(second["person_id"], self._data("A", first_name="Cambio"))
        after = (self._row(first["person_id"]), self._row(second["person_id"]))
        self.assertEqual(before, after)

    def test_update_invalid_station_unchanged(self):
        person = self._create()
        before = self._row(person["person_id"])
        with self.assertRaises(pc.InvalidStationError):
            pc.update_person(person["person_id"], self._data(station_id=999999))
        self.assertEqual(self._row(person["person_id"]), before)

    def test_not_found_and_invalid_ids(self):
        missing = 4000000000
        with self.assertRaises(pc.PersonNotFoundError):
            pc.update_person(missing, self._data())
        for func in (pc.get_person, pc.activate_person, pc.deactivate_person):
            with self.assertRaises(pc.PersonNotFoundError):
                func(missing)
            for bad in (True, "1", None, 1.0):
                with self.assertRaises(pc.PersonnelValidationError):
                    func(bad)
        self.assertEqual(self._count_prefix(), 0)

    def test_create_in_inactive_station_rejected(self):
        other = self._temp_station()
        station_model.set_station_active(other, False)
        with self.assertRaises(pc.InactiveStationError) as ctx:
            self._create(station_id=other)
        self.assertEqual(
            str(ctx.exception), "No se puede asignar personal a una estación inactiva."
        )
        self.assertIsInstance(ctx.exception, pc.PersonnelValidationError)
        self.assertEqual(self._count_prefix(), 0)

    def test_nonexistent_station_keeps_existence_message(self):
        with self.assertRaises(pc.InvalidStationError) as ctx:
            self._create(station_id=999999)
        self.assertNotIsInstance(ctx.exception, pc.InactiveStationError)
        self.assertEqual(str(ctx.exception), "La estación indicada no existe.")

    def test_move_to_inactive_station_rejected_row_unchanged(self):
        inactive = self._temp_station()
        station_model.set_station_active(inactive, False)
        person = self._create()
        before = self._row(person["person_id"])
        with self.assertRaises(pc.InactiveStationError):
            pc.update_person(person["person_id"], self._data(
                first_name="Cambio", station_id=inactive))
        self.assertEqual(self._row(person["person_id"]), before)

    def test_person_in_later_inactive_station(self):
        other = self._temp_station()
        person = self._create(station_id=other)
        pid = person["person_id"]
        station_model.set_station_active(other, False)
        self.assertIn(pid, self._ids(pc.list_personnel()))
        # otros campos con la misma estación inactiva: permitido
        updated = pc.update_person(pid, self._data(
            first_name="Renombrado", position="Teniente", phone="5555", station_id=other))
        self.assertEqual(updated["first_name"], "Renombrado")
        self.assertEqual(updated["position"], "Teniente")
        self.assertEqual(updated["phone"], "5555")
        self.assertEqual(updated["station_id"], other)
        # mover a otra estación inactiva: rechazado
        another = self._temp_station()
        station_model.set_station_active(another, False)
        before = self._row(pid)
        with self.assertRaises(pc.InactiveStationError):
            pc.update_person(pid, self._data(station_id=another))
        self.assertEqual(self._row(pid), before)
        # mover a una estación activa: permitido
        moved = pc.update_person(pid, self._data(station_id=self.seed_station))
        self.assertEqual(moved["station_id"], self.seed_station)

    def test_real_state_checked_at_save_time(self):
        other = self._temp_station()
        # "formulario" cargado cuando la estación aún estaba activa
        form_stations = pc.list_stations_for_personnel()
        self.assertTrue(next(s for s in form_stations if s["station_id"] == other)["active"])
        station_model.set_station_active(other, False)
        with self.assertRaises(pc.InactiveStationError):
            self._create(station_id=other)
        self.assertEqual(self._count_prefix(), 0)
        person = self._create("M")
        before = self._row(person["person_id"])
        with self.assertRaises(pc.InactiveStationError):
            pc.update_person(person["person_id"], self._data("M", station_id=other))
        self.assertEqual(self._row(person["person_id"]), before)

    def test_deactivate_and_reactivate(self):
        other = self._temp_station()
        person = self._create(station_id=other)
        pid = person["person_id"]
        self.assertFalse(pc.deactivate_person(pid)["active"])
        self.assertEqual(self._row(pid)[0][7], 0)
        self.assertNotIn(pid, self._ids(pc.list_active_personnel()))
        self.assertNotIn(pid, self._ids(pc.list_active_personnel(other)))
        self.assertIn(pid, self._ids(pc.list_personnel()))
        self.assertTrue(pc.activate_person(pid)["active"])
        self.assertEqual(self._ids(pc.list_active_personnel(other)), [pid])
        self.assertIn(pid, self._ids(pc.list_active_personnel()))
        self.assertNotIn("PER-011", [p["code"] for p in pc.list_active_personnel()])

    def test_stations_lookup(self):
        expected = {"station_id", "code", "name", "active"}
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            stations = pc.list_stations_for_personnel()
            self.assertTrue(all(set(s) == expected for s in stations))
            self.assertEqual(len(stations), len(station_model.list_stations()))
            self.assertGreaterEqual(len([s for s in stations if not s["code"].startswith("TPS-")]), 3)
            self.assertIn("EST-CENTRAL", {s["code"] for s in stations})
            codes = [s["code"] for s in stations]
            self.assertEqual(codes, sorted(codes))

    def test_stations_lookup_includes_inactive(self):
        other = self._temp_station()
        station_model.set_station_active(other, False)
        found = [s for s in pc.list_stations_for_personnel() if s["station_id"] == other]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["active"])

    def test_operator_allowed_read_and_write(self):
        session.end_session()
        _login("operador.central")
        other = self._temp_station()
        person = self._create()
        pid = person["person_id"]
        self.assertIn(pid, self._ids(pc.find_personnel(self.prefix)))
        updated = pc.update_person(pid, self._data(first_name="Op", station_id=other))
        self.assertEqual(updated["first_name"], "Op")
        self.assertFalse(pc.deactivate_person(pid)["active"])
        self.assertTrue(pc.activate_person(pid)["active"])

    def test_consulta_denied_everywhere_db_unchanged(self):
        person = self._create()
        pid = person["person_id"]
        before = self._row(pid)
        total_before = _db_all("SELECT COUNT(*) FROM personal")[0][0]
        calls = [
            lambda: pc.list_personnel(),
            lambda: pc.search_personnel("x"),
            lambda: pc.filter_personnel_by_station(self.seed_station),
            lambda: pc.find_personnel("x", None),
            lambda: pc.get_person(pid),
            lambda: pc.create_person(self._data("DENIED")),
            lambda: pc.update_person(pid, self._data(first_name="Hackeado")),
            lambda: pc.activate_person(pid),
            lambda: pc.deactivate_person(pid),
            lambda: pc.list_active_personnel(),
            lambda: pc.list_stations_for_personnel(),
        ]
        session.end_session()
        _login("consulta.sur")
        for call in calls:
            with self.assertRaises(AuthorizationDenied):
                call()
        self.assertEqual(self._row(pid), before)
        self.assertEqual(_db_all("SELECT COUNT(*) FROM personal")[0][0], total_before)

    def test_no_session(self):
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            pc.list_personnel()
        with self.assertRaises(NotAuthenticated):
            pc.create_person(self._data())
        with self.assertRaises(NotAuthenticated):
            pc.deactivate_person(1)
        with self.assertRaises(NotAuthenticated):
            pc.list_stations_for_personnel()
        with self.assertRaises(NotAuthenticated):
            pc.list_active_personnel()
        self.assertEqual(self._count_prefix(), 0)

    def test_operator_still_denied_station_crud(self):
        session.end_session()
        _login("operador.central")
        self.assertFalse(access_controller.can(PERMISSION_MANAGE_CATALOGS))
        self.assertTrue(access_controller.can(PERMISSION_MANAGE_RESOURCES))
        sc = station_controller
        data = {"code": f"TPS-{uuid4().hex[:8]}", "name": "N", "address": "D"}
        calls = [
            lambda: sc.list_stations(),
            lambda: sc.list_active_stations(),
            lambda: sc.search_stations("x"),
            lambda: sc.get_station(self.seed_station),
            lambda: sc.create_station(data),
            lambda: sc.update_station(self.seed_station, data),
            lambda: sc.activate_station(self.seed_station),
            lambda: sc.deactivate_station(self.seed_station),
        ]
        for call in calls:
            with self.assertRaises(AuthorizationDenied):
                call()
        # el lookup de personal no habilita nada de estaciones
        pc.list_stations_for_personnel()
        self.assertFalse(access_controller.can(PERMISSION_MANAGE_CATALOGS))
        with self.assertRaises(AuthorizationDenied):
            sc.list_stations()

    def test_technical_error_wrapped(self):
        error = mysql.connector.Error("fallo simulado SELECT secreto")
        with mock.patch.object(personnel_model, "search_personnel", side_effect=error):
            with self.assertRaises(pc.PersonnelServiceError) as ctx:
                pc.list_personnel()
        self.assertNotIn("SELECT", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, error)
        with mock.patch.object(personnel_model, "create_person", side_effect=error):
            with self.assertRaises(pc.PersonnelServiceError):
                self._create()
        with mock.patch.object(station_model, "list_stations", side_effect=error):
            with self.assertRaises(pc.PersonnelServiceError):
                pc.list_stations_for_personnel()


if __name__ == "__main__":
    unittest.main()

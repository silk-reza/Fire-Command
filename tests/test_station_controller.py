import unittest
from unittest import mock
from uuid import uuid4

import mysql.connector

from config.database import get_connection
from controllers import station_controller as sc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import station_model
from utils import session

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
    return _db_all(
        "SELECT * FROM estaciones WHERE codigo NOT LIKE 'TST-%' ORDER BY id_estacion"
    )


class StationControllerTest(unittest.TestCase):
    def setUp(self):
        session.end_session()
        self.prefix = f"TST-{uuid4().hex[:8]}"
        self.seed_before = _seed_snapshot()
        self.extra_codes = []
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM estaciones WHERE codigo LIKE %s", (self.prefix + "%",)
            )
            for code in self.extra_codes:
                cursor.execute("DELETE FROM estaciones WHERE codigo = %s", (code,))
            connection.commit()
            cursor.close()
        finally:
            connection.close()
        self.assertEqual(_seed_snapshot(), self.seed_before)

    def _data(self, suffix="A", **overrides):
        data = {
            "code": f"{self.prefix}-{suffix}",
            "name": "Estación de prueba",
            "address": "Calle de prueba 1",
            "reference": "",
            "phone": "",
            "latitude": "",
            "longitude": "",
        }
        data.update(overrides)
        return data

    def _create(self, suffix="A", **overrides):
        return sc.create_station(self._data(suffix, **overrides))

    def _row(self, station_id):
        return _db_all("SELECT * FROM estaciones WHERE id_estacion = %s", (station_id,))

    def _count_prefix(self):
        return _db_all(
            "SELECT COUNT(*) FROM estaciones WHERE codigo LIKE %s", (self.prefix + "%",)
        )[0][0]

    def test_list_and_dict_keys(self):
        stations = sc.list_stations()
        self.assertIn("EST-CENTRAL", {s["code"] for s in stations})
        keys = {"station_id", "code", "name", "address", "reference",
                "latitude", "longitude", "phone", "active"}
        self.assertTrue(all(set(s) == keys for s in stations))

    def test_search_by_code_name_address(self):
        station = self._create(name="Zzcentral Unico", address="Zona Qwerty 77")
        for term in (self.prefix, "zzcentral", "  qwerty 77  "):
            found = sc.search_stations(term)
            self.assertEqual([s["station_id"] for s in found], [station["station_id"]])
        self.assertEqual(sc.search_stations("inexistente-" + uuid4().hex), [])
        self.assertEqual(len(sc.search_stations("   ")), len(sc.list_stations()))
        self.assertEqual(len(sc.search_stations(None)), len(sc.list_stations()))

    def test_search_wildcards_safe(self):
        special = self._create("S", name="100%_x\y")["station_id"]
        plain = self._create("P", name="abc")["station_id"]
        for term in ("%", "_", "\\"):
            ids = {s["station_id"] for s in sc.search_stations(term)}
            self.assertEqual(ids, {special}, msg=term)
        self.assertNotIn(plain, {s["station_id"] for s in sc.search_stations("a%")})

    def test_code_resembling_constraint_message(self):
        code = f"TST-{uuid4().hex[:4]}' for key 'a"
        self.extra_codes.append(code)
        self.assertLessEqual(len(code), 20)
        self._create(code=code)
        with self.assertRaises(sc.DuplicateStationCodeError):
            self._create(code=code)

    def test_seven_decimals_and_lone_latitude(self):
        with self.assertRaises(sc.StationValidationError):
            self._create(latitude="14.1234567", longitude="10")
        with self.assertRaises(sc.StationValidationError):
            self._create("B", latitude=14, longitude="")
        self.assertEqual(self._count_prefix(), 0)

    def test_get_then_update_roundtrip_float_coordinates(self):
        station = self._create(latitude="14.640720", longitude="-90.513270")
        before = self._row(station["station_id"])
        loaded = sc.get_station(station["station_id"])
        self.assertIsInstance(loaded["latitude"], float)
        sc.update_station(station["station_id"], {
            k: loaded[k] for k in
            ("code", "name", "address", "reference", "phone", "latitude", "longitude")
        })
        after = self._row(station["station_id"])
        self.assertEqual(after[0][5], before[0][5])
        self.assertEqual(after[0][6], before[0][6])

    def test_update_inactive_station_keeps_inactive(self):
        station = self._create()
        sc.deactivate_station(station["station_id"])
        updated = sc.update_station(station["station_id"], self._data(name="Renombrada"))
        self.assertEqual(updated["name"], "Renombrada")
        self.assertFalse(updated["active"])
        self.assertEqual(self._row(station["station_id"])[0][8], 0)

    def test_create_valid_full(self):
        station = self._create(
            code=f"  {self.prefix}-A  ", name=" Nombre ", address=" Dir ",
            reference=" Ref ", phone=" 2222-3333 ",
            latitude="14.640720", longitude="-90.513270",
        )
        self.assertEqual(station["code"], f"{self.prefix}-A")
        self.assertEqual(station["name"], "Nombre")
        self.assertEqual(station["address"], "Dir")
        self.assertEqual(station["reference"], "Ref")
        self.assertEqual(station["phone"], "2222-3333")
        self.assertEqual(station["latitude"], 14.640720)
        self.assertEqual(station["longitude"], -90.513270)
        self.assertTrue(station["active"])
        self.assertIn(station["station_id"], [s["station_id"] for s in sc.list_stations()])
        self.assertEqual(sc.get_station(station["station_id"]), station)

    def test_optional_empty_stored_as_null(self):
        station = self._create(reference="   ", phone="")
        row = self._row(station["station_id"])[0]
        self.assertIsNone(station["reference"])
        self.assertIsNone(station["phone"])
        self.assertIsNone(station["latitude"])
        self.assertIsNone(station["longitude"])
        # referencia, latitud, longitud y telefono en NULL, no cadena vacía
        self.assertEqual([row[4], row[5], row[6], row[7]], [None] * 4)

    def test_duplicate_code(self):
        self._create()
        with self.assertRaises(sc.DuplicateStationCodeError) as ctx:
            self._create()
        self.assertEqual(str(ctx.exception), "Ya existe una estación con ese código.")
        self.assertEqual(self._count_prefix(), 1)

    def test_valid_coordinates_and_boundaries(self):
        cases = [("0.0", "0.0"), ("90", "180"), ("-90", "-180"), ("90.000000", "-180.000000"),
                 (14.64072, -90.51327)]
        for index, (lat, lon) in enumerate(cases):
            station = self._create(f"V{index}", latitude=lat, longitude=lon)
            self.assertEqual(station["latitude"], float(lat))
            self.assertEqual(station["longitude"], float(lon))

    def test_invalid_latitude(self):
        for index, lat in enumerate(("90.000001", "-91", "1000")):
            with self.assertRaises(sc.StationValidationError) as ctx:
                self._create(f"L{index}", latitude=lat, longitude="10")
            self.assertIn("latitud", str(ctx.exception))
        self.assertEqual(self._count_prefix(), 0)

    def test_invalid_longitude(self):
        for index, lon in enumerate(("180.5", "-181")):
            with self.assertRaises(sc.StationValidationError) as ctx:
                self._create(f"G{index}", latitude="10", longitude=lon)
            self.assertIn("longitud", str(ctx.exception))
        self.assertEqual(self._count_prefix(), 0)

    def test_incomplete_coordinates(self):
        cases = [("10", ""), ("", "10"), ("10", "   "), ("  ", "10"), ("10", None)]
        for index, (lat, lon) in enumerate(cases):
            with self.assertRaises(sc.StationValidationError):
                self._create(f"I{index}", latitude=lat, longitude=lon)
        self.assertEqual(self._count_prefix(), 0)

    def test_both_empty_accepted_as_null(self):
        station = self._create(latitude="  ", longitude="")
        self.assertIsNone(station["latitude"])
        row = self._row(station["station_id"])[0]
        self.assertIsNone(row[5])
        self.assertIsNone(row[6])

    def test_non_numeric_coordinates(self):
        for index, bad in enumerate(("abc", "nan", "inf", "-Infinity", "1,5", "1_0", True)):
            with self.assertRaises(sc.StationValidationError):
                self._create(f"N{index}", latitude=bad, longitude="10")
            with self.assertRaises(sc.StationValidationError):
                self._create(f"M{index}", latitude="10", longitude=bad)
        self.assertEqual(self._count_prefix(), 0)

    def test_more_than_six_decimals_rejected(self):
        with self.assertRaises(sc.StationValidationError) as ctx:
            self._create(latitude="14.6407201", longitude="-90.5")
        self.assertIn("decimales", str(ctx.exception))
        with self.assertRaises(sc.StationValidationError):
            self._create("B", latitude="14.5", longitude="-90.1234567")
        self.assertEqual(self._count_prefix(), 0)

    def test_required_and_length_limits(self):
        bad = [
            {"code": ""}, {"code": "   "}, {"code": None}, {"code": "x" * 21},
            {"name": ""}, {"name": "x" * 101},
            {"address": " "}, {"address": "x" * 201},
            {"reference": "x" * 201}, {"phone": "x" * 26},
        ]
        for overrides in bad:
            with self.assertRaises(sc.StationValidationError, msg=str(overrides)):
                sc.create_station(self._data("R", **overrides))
        with self.assertRaises(sc.StationValidationError):
            sc.create_station(None)
        self.assertEqual(self._count_prefix(), 0)
        # Los límites exactos sí se aceptan
        ok = sc.create_station(self._data(
            "L", code=self.prefix + "x" * (20 - len(self.prefix)),
            name="n" * 100, address="a" * 200, reference="r" * 200, phone="p" * 25,
        ))
        self.assertEqual(len(ok["code"]), 20)

    def test_update_persists(self):
        station = self._create()
        updated = sc.update_station(station["station_id"], self._data(
            name="Nuevo nombre", address="Nueva dir", reference="Ref", phone="1234",
            latitude="1.5", longitude="-2.5",
        ))
        self.assertEqual(updated["name"], "Nuevo nombre")
        self.assertEqual(updated["latitude"], 1.5)
        self.assertEqual(sc.get_station(station["station_id"])["address"], "Nueva dir")
        # Volver coordenadas a NULL/NULL
        cleared = sc.update_station(station["station_id"], self._data())
        self.assertIsNone(cleared["latitude"])
        self.assertIsNone(cleared["longitude"])
        self.assertIsNone(cleared["reference"])
        # Update sin cambios no se confunde con inexistente
        self.assertEqual(sc.update_station(station["station_id"], self._data())["name"],
                         "Estación de prueba")

    def test_update_duplicate_code_leaves_both(self):
        first = self._create("A")
        second = self._create("B")
        before = (self._row(first["station_id"]), self._row(second["station_id"]))
        with self.assertRaises(sc.DuplicateStationCodeError):
            sc.update_station(second["station_id"], self._data("A", name="Cambio"))
        after = (self._row(first["station_id"]), self._row(second["station_id"]))
        self.assertEqual(before, after)

    def test_update_single_coordinate_rejected_row_unchanged(self):
        station = self._create(latitude="5", longitude="6")
        before = self._row(station["station_id"])
        with self.assertRaises(sc.StationValidationError):
            sc.update_station(station["station_id"], self._data(latitude="7", longitude=""))
        with self.assertRaises(sc.StationValidationError):
            sc.update_station(station["station_id"], self._data(latitude="", longitude="7"))
        self.assertEqual(self._row(station["station_id"]), before)

    def test_not_found_and_invalid_ids(self):
        missing = 4000000000
        with self.assertRaises(sc.StationNotFoundError):
            sc.update_station(missing, self._data())
        with self.assertRaises(sc.StationNotFoundError):
            sc.get_station(missing)
        with self.assertRaises(sc.StationNotFoundError):
            sc.activate_station(missing)
        with self.assertRaises(sc.StationNotFoundError):
            sc.deactivate_station(missing)
        for bad in (True, "1", None, 1.0):
            with self.assertRaises(sc.StationValidationError):
                sc.get_station(bad)
            with self.assertRaises(sc.StationValidationError):
                sc.deactivate_station(bad)
        self.assertEqual(self._count_prefix(), 0)

    def test_deactivate_and_reactivate(self):
        station = self._create()
        station_id = station["station_id"]
        result = sc.deactivate_station(station_id)
        self.assertFalse(result["active"])
        self.assertEqual(self._row(station_id)[0][8], 0)
        self.assertNotIn(station_id, [s["station_id"] for s in sc.list_active_stations()])
        self.assertIn(station_id, [s["station_id"] for s in sc.list_stations()])
        result = sc.activate_station(station_id)
        self.assertTrue(result["active"])
        active = sc.list_active_stations()
        self.assertIn(station_id, [s["station_id"] for s in active])
        self.assertTrue(all(s["active"] for s in active))

    def test_unauthorized_roles_all_functions(self):
        station = self._create()
        station_id = station["station_id"]
        before = self._row(station_id)
        total_before = len(_seed_snapshot()) + self._count_prefix()
        calls = [
            lambda: sc.list_stations(),
            lambda: sc.search_stations("x"),
            lambda: sc.get_station(station_id),
            lambda: sc.create_station(self._data("DENIED")),
            lambda: sc.update_station(station_id, self._data(name="Hackeado")),
            lambda: sc.activate_station(station_id),
            lambda: sc.deactivate_station(station_id),
            lambda: sc.list_active_stations(),
        ]
        for username in ("operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            for call in calls:
                with self.assertRaises(AuthorizationDenied):
                    call()
        self.assertEqual(self._row(station_id), before)
        self.assertEqual(len(_seed_snapshot()) + self._count_prefix(), total_before)
        self.assertEqual(self._count_prefix(), 1)

    def test_no_session(self):
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            sc.list_stations()
        with self.assertRaises(NotAuthenticated):
            sc.create_station(self._data())
        with self.assertRaises(NotAuthenticated):
            sc.deactivate_station(1)
        self.assertEqual(self._count_prefix(), 0)

    def test_technical_error_wrapped(self):
        error = mysql.connector.Error("fallo simulado SELECT secreto")
        with mock.patch.object(station_model, "list_stations", side_effect=error):
            with self.assertRaises(sc.StationServiceError) as ctx:
                sc.list_stations()
        self.assertNotIn("SELECT", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, error)
        with mock.patch.object(station_model, "create_station", side_effect=error):
            with self.assertRaises(sc.StationServiceError):
                self._create()


if __name__ == "__main__":
    unittest.main()

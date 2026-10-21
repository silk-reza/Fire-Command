import unittest
from decimal import Decimal
from uuid import uuid4

from config.database import get_connection
from models import station_model


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


class StationModelTest(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.prefix = f"TST-{uuid4().hex[:8]}"

    def tearDown(self):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            for station_id in self.created:
                cursor.execute("DELETE FROM estaciones WHERE id_estacion = %s", (station_id,))
            cursor.execute(
                "DELETE FROM estaciones WHERE codigo LIKE %s", (self.prefix + "%",)
            )
            connection.commit()
            cursor.close()
        finally:
            connection.close()

    def _create(self, suffix="A", **overrides):
        values = {
            "code": f"{self.prefix}-{suffix}",
            "name": "Estación de prueba",
            "address": "Calle de prueba 1",
            "reference": None,
            "latitude": None,
            "longitude": None,
            "phone": None,
        }
        values.update(overrides)
        station_id = station_model.create_station(**values)
        self.created.append(station_id)
        return station_id

    def _count_prefix(self):
        return _db_all(
            "SELECT COUNT(*) FROM estaciones WHERE codigo LIKE %s", (self.prefix + "%",)
        )[0][0]

    def test_list_has_seed_and_keys(self):
        stations = station_model.list_stations()
        codes = {s["code"] for s in stations}
        self.assertIn("EST-CENTRAL", codes)
        keys = {"station_id", "code", "name", "address", "reference",
                "latitude", "longitude", "phone", "active"}
        for station in stations:
            self.assertEqual(set(station), keys)
            self.assertIsInstance(station["active"], bool)

    def test_search_partial_and_empty(self):
        station_id = self._create(name="Zzbomberos Unicos", address="Avenida Qwerty 99")
        for term in (self.prefix, "zzbomberos", "qwerty 99"):
            found = station_model.search_stations(term)
            self.assertEqual([s["station_id"] for s in found], [station_id])
        self.assertEqual(station_model.search_stations("no-existe-" + uuid4().hex), [])

    def test_search_wildcards_are_literal(self):
        special = self._create("S", name="100%_x\y")
        plain = self._create("P", name="abc")
        for term in ("%", "_", "\\"):
            ids = {s["station_id"] for s in station_model.search_stations(term)}
            self.assertEqual(ids, {special}, msg=term)
        ids = {s["station_id"] for s in station_model.search_stations("a%")}
        self.assertNotIn(plain, ids)
        self.assertEqual(
            {s["station_id"] for s in station_model.search_stations("100%_x\y")}, {special}
        )

    def test_code_resembling_constraint_message(self):
        code = f"TST-{uuid4().hex[:4]}' for key 'a"
        self.assertLessEqual(len(code), 20)
        self._create(code=code)
        with self.assertRaises(station_model.DuplicateStationCodeError):
            self._create(code=code)
        self.assertEqual(
            _db_all("SELECT COUNT(*) FROM estaciones WHERE codigo = %s", (code,))[0][0], 1
        )

    def test_create_round_trip_coordinates(self):
        station_id = self._create(
            reference="Frente al parque", phone="2222-3333",
            latitude=Decimal("14.640720"), longitude=Decimal("-90.513270"),
        )
        station = station_model.get_station(station_id)
        self.assertEqual(station["latitude"], 14.640720)
        self.assertEqual(station["longitude"], -90.513270)
        self.assertEqual(station["reference"], "Frente al parque")
        self.assertEqual(station["phone"], "2222-3333")
        self.assertTrue(station["active"])
        self.assertIsInstance(station["latitude"], float)

    def test_optional_fields_null(self):
        station = station_model.get_station(self._create())
        for key in ("reference", "phone", "latitude", "longitude"):
            self.assertIsNone(station[key])

    def test_duplicate_code_translated(self):
        self._create()
        with self.assertRaises(station_model.DuplicateStationCodeError):
            self._create()
        self.assertEqual(self._count_prefix(), 1)

    def test_update_duplicate_code_and_not_found(self):
        first = self._create("A")
        second = self._create("B")
        fields = {k: v for k, v in station_model.get_station(second).items()
                  if k not in ("station_id", "active")}
        fields["code"] = station_model.get_station(first)["code"]
        with self.assertRaises(station_model.DuplicateStationCodeError):
            station_model.update_station(second, fields)
        self.assertEqual(station_model.get_station(second)["code"], f"{self.prefix}-B")
        self.assertFalse(station_model.update_station(0, fields))
        self.assertFalse(station_model.set_station_active(0, False))

    def test_update_without_changes_is_found(self):
        station_id = self._create()
        fields = {k: v for k, v in station_model.get_station(station_id).items()
                  if k not in ("station_id", "active")}
        self.assertTrue(station_model.update_station(station_id, fields))

    def test_set_active_and_active_list(self):
        station_id = self._create()
        self.assertTrue(station_model.set_station_active(station_id, False))
        self.assertFalse(station_model.get_station(station_id)["active"])
        self.assertNotIn(station_id, [s["station_id"] for s in station_model.list_active_stations()])
        self.assertIn(station_id, [s["station_id"] for s in station_model.list_stations()])
        station_model.set_station_active(station_id, True)
        active = station_model.list_active_stations()
        self.assertIn(station_id, [s["station_id"] for s in active])
        self.assertTrue(all(s["active"] for s in active))

    def test_db_check_is_final_safeguard(self):
        cases = [
            {"latitude": Decimal("10"), "longitude": None},
            {"latitude": None, "longitude": Decimal("10")},
            {"latitude": Decimal("91"), "longitude": Decimal("10")},
            {"latitude": Decimal("10"), "longitude": Decimal("181")},
        ]
        for index, case in enumerate(cases):
            with self.assertRaises(station_model.InvalidStationDataError):
                self._create(f"C{index}", **case)
        self.assertEqual(self._count_prefix(), 0)


if __name__ == "__main__":
    unittest.main()

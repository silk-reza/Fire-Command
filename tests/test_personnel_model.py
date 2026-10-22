import unittest
from uuid import uuid4

from config.database import get_connection
from models import personnel_model, station_model


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


class PersonnelModelTest(unittest.TestCase):
    def setUp(self):
        self.prefix = f"TPR-{uuid4().hex[:8]}"
        self.station_ids = []
        self.seed_before = _seed_snapshot()
        self.seed_station = _db_all(
            "SELECT id_estacion FROM estaciones WHERE codigo NOT LIKE 'TPS-%' "
            "ORDER BY id_estacion LIMIT 1"
        )[0][0]

    def tearDown(self):
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

    def _create(self, suffix="A", **overrides):
        values = {
            "code": f"{self.prefix}-{suffix}",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "position": "Bombero",
            "phone": None,
            "station_id": self.seed_station,
        }
        values.update(overrides)
        return personnel_model.create_person(**values)

    def _count_prefix(self):
        return _db_all(
            "SELECT COUNT(*) FROM personal WHERE codigo LIKE %s", (self.prefix + "%",)
        )[0][0]

    def _row(self, person_id):
        return _db_all("SELECT * FROM personal WHERE id_personal = %s", (person_id,))

    def test_list_seed_and_keys(self):
        people = personnel_model.list_personnel()
        codes = {p["code"] for p in people}
        self.assertTrue({f"PER-{n:03d}" for n in range(1, 16)} <= codes)
        keys = {"person_id", "code", "first_name", "last_name", "position", "phone",
                "active", "station_id", "station_code", "station_name"}
        for person in people:
            self.assertEqual(set(person), keys)
            self.assertIsInstance(person["active"], bool)

    def test_search_fields_and_full_name(self):
        pid = self._create(first_name="Zzjuan", last_name="Qwertyez", position="Zzcargo")
        for term in (self.prefix, "zzjuan", "qwertyez", "zzjuan qwerty", "zzcargo"):
            found = personnel_model.search_personnel(term)
            self.assertEqual([p["person_id"] for p in found], [pid], msg=term)
        self.assertEqual(personnel_model.search_personnel("no-existe-" + uuid4().hex), [])

    def test_search_wildcards_literal(self):
        special = self._create("S", first_name="100%_x\\y")
        plain = self._create("P", first_name="abc")
        for term in ("%", "_", "\\"):
            ids = {p["person_id"] for p in personnel_model.search_personnel(term)}
            self.assertIn(special, ids, msg=term)
            self.assertNotIn(plain, ids, msg=term)
        # ninguna persona seed contiene estos caracteres: coincidencia exacta
        for term in ("%", "_", "\\"):
            ids = {p["person_id"] for p in personnel_model.search_personnel(term)}
            self.assertEqual(ids, {special}, msg=term)

    def test_station_filter_and_combined(self):
        other = self._temp_station()
        mine = self._create("M", first_name="Zzcomun")
        theirs = self._create("T", first_name="Zzcomun", station_id=other)
        only_other = self._create("O", first_name="Zzsolootra", station_id=other)
        by_station = personnel_model.list_personnel(other)
        self.assertEqual({p["person_id"] for p in by_station}, {theirs, only_other})
        self.assertTrue(all(p["station_id"] == other for p in by_station))
        combined = personnel_model.search_personnel("zzcomun", self.seed_station)
        self.assertEqual([p["person_id"] for p in combined], [mine])
        self.assertEqual(personnel_model.search_personnel("zzsolootra", self.seed_station), [])
        self.assertEqual(
            len(personnel_model.list_personnel(self.seed_station)),
            _db_all("SELECT COUNT(*) FROM personal WHERE id_estacion = %s",
                    (self.seed_station,))[0][0],
        )

    def test_create_and_phone_null(self):
        pid = self._create(phone="")
        person = personnel_model.get_person(pid)
        self.assertTrue(person["active"])
        self.assertIsNone(person["phone"])
        self.assertIsNone(self._row(pid)[0][6])
        self.assertEqual(person["station_id"], self.seed_station)

    def test_duplicate_code(self):
        self._create()
        with self.assertRaises(personnel_model.DuplicatePersonCodeError):
            self._create()
        self.assertEqual(self._count_prefix(), 1)

    def test_invalid_station_fk(self):
        with self.assertRaises(personnel_model.InvalidStationError):
            self._create(station_id=999999)
        self.assertEqual(self._count_prefix(), 0)
        pid = self._create("U")
        before = self._row(pid)
        with self.assertRaises(personnel_model.InvalidStationError):
            personnel_model.update_person(pid, {
                "code": f"{self.prefix}-U", "first_name": "X", "last_name": "Y",
                "position": "Z", "phone": None, "station_id": 999999,
            })
        self.assertEqual(self._row(pid), before)

    def test_inactive_station_rejected_by_model(self):
        inactive = self._temp_station()
        station_model.set_station_active(inactive, False)
        with self.assertRaises(personnel_model.InactiveStationError):
            self._create(station_id=inactive)
        self.assertEqual(self._count_prefix(), 0)
        pid = self._create("U")
        before = self._row(pid)
        fields = {
            "code": f"{self.prefix}-U", "first_name": "X", "last_name": "Y",
            "position": "Z", "phone": None, "station_id": inactive,
        }
        with self.assertRaises(personnel_model.InactiveStationError):
            personnel_model.update_person(pid, fields)
        self.assertEqual(self._row(pid), before)
        # nonexistente sigue siendo InvalidStationError
        with self.assertRaises(personnel_model.InvalidStationError):
            self._create("V", station_id=999999)

    def test_person_keeps_station_that_became_inactive(self):
        other = self._temp_station()
        pid = self._create(station_id=other)
        station_model.set_station_active(other, False)
        fields = {
            "code": f"{self.prefix}-A", "first_name": "Editado", "last_name": "Y",
            "position": "Z", "phone": None, "station_id": other,
        }
        self.assertTrue(personnel_model.update_person(pid, fields))
        person = personnel_model.get_person(pid)
        self.assertEqual((person["first_name"], person["station_id"]), ("Editado", other))
        fields["station_id"] = self.seed_station
        self.assertTrue(personnel_model.update_person(pid, fields))
        self.assertEqual(personnel_model.get_person(pid)["station_id"], self.seed_station)

    def _fields(self, station_id, **overrides):
        fields = {
            "code": f"{self.prefix}-A", "first_name": "Nombre", "last_name": "Apellido",
            "position": "Bombero", "phone": None, "station_id": station_id,
        }
        fields.update(overrides)
        return fields

    def test_person_in_inactive_station_cannot_move_to_another_inactive(self):
        first = self._temp_station()
        second = self._temp_station()
        pid = self._create(station_id=first)
        station_model.set_station_active(first, False)
        station_model.set_station_active(second, False)
        before = self._row(pid)
        with self.assertRaises(personnel_model.InactiveStationError):
            personnel_model.update_person(pid, self._fields(second, first_name="Cambio"))
        self.assertEqual(self._row(pid), before)

    def test_edit_fields_keeping_inactive_station_and_duplicate_rollback(self):
        other = self._temp_station()
        pid = self._create(station_id=other)
        taken = self._create("B")
        station_model.set_station_active(other, False)
        fields = self._fields(
            other, code=f"{self.prefix}-A2", first_name="Editado", position="Capitan"
        )
        self.assertTrue(personnel_model.update_person(pid, fields))
        person = personnel_model.get_person(pid)
        self.assertEqual(
            (person["code"], person["first_name"], person["position"], person["station_id"]),
            (f"{self.prefix}-A2", "Editado", "Capitan", other),
        )
        # código duplicado: rollback, la fila no cambia
        before = self._row(pid)
        taken_before = self._row(taken)
        with self.assertRaises(personnel_model.DuplicatePersonCodeError):
            personnel_model.update_person(
                pid, self._fields(other, code=f"{self.prefix}-B", first_name="Otro")
            )
        self.assertEqual(self._row(pid), before)
        self.assertEqual(self._row(taken), taken_before)

    def test_update_and_not_found(self):
        other = self._temp_station()
        pid = self._create()
        ok = personnel_model.update_person(pid, {
            "code": f"{self.prefix}-A", "first_name": "Nuevo", "last_name": "Otro",
            "position": "Cabo", "phone": "1234", "station_id": other,
        })
        self.assertTrue(ok)
        person = personnel_model.get_person(pid)
        self.assertEqual((person["first_name"], person["station_id"], person["phone"]),
                         ("Nuevo", other, "1234"))
        self.assertFalse(personnel_model.update_person(4000000000, {
            "code": "x", "first_name": "x", "last_name": "x", "position": "x",
            "phone": None, "station_id": other,
        }))
        self.assertFalse(personnel_model.set_person_active(4000000000, False))

    def test_update_duplicate_code_unchanged(self):
        first = self._create("A")
        second = self._create("B")
        before = (self._row(first), self._row(second))
        with self.assertRaises(personnel_model.DuplicatePersonCodeError):
            personnel_model.update_person(second, {
                "code": f"{self.prefix}-A", "first_name": "Cambio", "last_name": "Y",
                "position": "Z", "phone": None, "station_id": self.seed_station,
            })
        self.assertEqual((self._row(first), self._row(second)), before)

    def test_active_toggle_and_active_list(self):
        other = self._temp_station()
        pid = self._create(station_id=other)
        self.assertTrue(personnel_model.set_person_active(pid, False))
        self.assertEqual(self._row(pid)[0][7], 0)
        self.assertNotIn(pid, [p["person_id"] for p in personnel_model.list_active_personnel()])
        self.assertIn(pid, [p["person_id"] for p in personnel_model.list_personnel()])
        self.assertTrue(personnel_model.set_person_active(pid, True))
        active = personnel_model.list_active_personnel(other)
        self.assertEqual([p["person_id"] for p in active], [pid])
        self.assertTrue(all(p["active"] for p in personnel_model.list_active_personnel()))
        # el seed inactivo (PER-011) no aparece entre activos
        codes = {p["code"] for p in personnel_model.list_active_personnel()}
        self.assertNotIn("PER-011", codes)

    def test_active_list_order(self):
        people = personnel_model.list_active_personnel()
        expected = _db_all(
            "SELECT id_personal FROM personal WHERE activo = 1 "
            "ORDER BY apellidos, nombres, codigo, id_personal"
        )
        self.assertEqual([p["person_id"] for p in people], [r[0] for r in expected])


if __name__ == "__main__":
    unittest.main()

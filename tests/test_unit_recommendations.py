import unittest

from controllers import dispatch_controller as dc
from controllers.access_controller import NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from tests.test_dispatch_model import function_body
from utils import session
from pathlib import Path

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent
ITEM_KEYS = {"unit_type_id", "unit_type_name", "priority", "note", "available_count",
             "available_units"}
UNIT_KEYS = {"unit_id", "code", "station_id", "station_code"}


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class UnitRecommendationTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.compat_before = db_all(
            "SELECT * FROM compatibilidad_incidente_unidad ORDER BY 1, 2")
        self._n = 0
        session.end_session()
        _login("admin.demo")

    def cleanup_steps(self):
        steps = [
            ("DELETE FROM compatibilidad_incidente_unidad WHERE id_tipo_incidente = %s", (t,))
            for t in self.type_ids
        ] + [
            ("DELETE FROM compatibilidad_incidente_unidad WHERE id_tipo_unidad = %s", (t,))
            for t in self.unit_type_ids
        ]
        return steps + super().cleanup_steps()

    def tearDown(self):
        session.end_session()
        super().tearDown()
        self.assertEqual(db_all(
            "SELECT * FROM compatibilidad_incidente_unidad ORDER BY 1, 2"), self.compat_before)

    def compat(self, incident_type, unit_type, priority, note=None, active=True):
        db_exec(
            "INSERT INTO compatibilidad_incidente_unidad (id_tipo_incidente, id_tipo_unidad, "
            "prioridad_recomendacion, observacion, activo) VALUES (%s, %s, %s, %s, %s)",
            (incident_type, unit_type, priority, note, 1 if active else 0))

    def incident_of(self, incident_type, state="REPORTADO"):
        self._n += 1
        return self.insert_incident(f"RC{self._n}", state, tipo=incident_type)

    def seed_id(self, table, column, name):
        return db_all(f"SELECT {column} FROM {table} WHERE nombre = %s", (name,))[0][0]

    def names(self, recommendations):
        return [r["unit_type_name"] for r in recommendations]

    # 1, 11
    def test_ordering_and_keys(self):
        incident_type = self.temp_type()
        type_a, type_b, type_c = (self.temp_unit_type() for _ in range(3))
        self.compat(incident_type, type_a, 3, "Apoyo")
        self.compat(incident_type, type_b, 1, None)
        self.compat(incident_type, type_c, 3, "Empate")
        incident_id = self.incident_of(incident_type)
        result = dc.get_unit_recommendations(incident_id)
        self.assertEqual([r["unit_type_id"] for r in result],
                         [type_b] + sorted([type_a, type_c], key=lambda t: (
                             db_all("SELECT nombre FROM tipos_unidad WHERE id_tipo_unidad = %s",
                                    (t,))[0][0], t)))
        self.assertEqual(set(result[0]), ITEM_KEYS)
        self.assertEqual((result[0]["priority"], result[0]["note"]), (1, None))
        self.assertEqual(result[0]["available_count"], 0)
        self.assertEqual(result[0]["available_units"], [])

    # 2
    def test_seed_fire_starts_with_pumper(self):
        fire = self.seed_id("tipos_incidente", "id_tipo_incidente", "INCENDIO")
        result = dc.get_unit_recommendations(self.incident_of(fire))
        self.assertEqual(self.names(result),
                         ["AUTOBOMBA", "CISTERNA", "AMBULANCIA", "UNIDAD_DE_MANDO"])
        self.assertEqual([r["priority"] for r in result], [1, 2, 3, 4])

    # 3, 4
    def test_inactive_compatibility_and_inactive_type_excluded(self):
        other = self.seed_id("tipos_incidente", "id_tipo_incidente", "OTRO")
        self.assertNotIn("AUTOBOMBA", self.names(dc.get_unit_recommendations(
            self.incident_of(other))))
        incident_type = self.temp_type()
        listed, inactive_compat, inactive_type = (self.temp_unit_type() for _ in range(3))
        self.compat(incident_type, listed, 1)
        self.compat(incident_type, inactive_compat, 2, active=False)
        self.compat(incident_type, inactive_type, 3)
        db_exec("UPDATE tipos_unidad SET activo = 0 WHERE id_tipo_unidad = %s", (inactive_type,))
        result = dc.get_unit_recommendations(self.incident_of(incident_type))
        self.assertEqual([r["unit_type_id"] for r in result], [listed])

    # 5, 6
    def test_missing_incident_and_no_compatibility(self):
        for missing in (999999, 0, -1):
            with self.assertRaises(dc.DispatchIncidentNotFoundError):
                dc.get_unit_recommendations(missing)
            with self.assertRaises(dm.DispatchIncidentNotFoundError):
                dm.get_unit_recommendations_for_incident(missing)
        for bad in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.get_unit_recommendations(bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.get_unit_recommendations_for_incident(bad)
        self.assertEqual(dc.get_unit_recommendations(
            self.incident_of(self.temp_type())), [])

    # 7, 8
    def test_availability_counts(self):
        incident_type = self.temp_type()
        stocked, empty = self.temp_unit_type(), self.temp_unit_type()
        self.compat(incident_type, stocked, 1)
        self.compat(incident_type, empty, 2)
        station = self.temp_station()
        ok_a = self.temp_unit(type_id=stocked)
        ok_b = self.temp_unit(type_id=stocked, station_id=station)
        for state in ("DESPACHADA", "EN_RUTA", "EN_ESCENA", "REGRESANDO", "MANTENIMIENTO",
                      "FUERA_DE_SERVICIO"):
            self.temp_unit(state, type_id=stocked)
        self.temp_unit(active=False, type_id=stocked)
        closed_station = self.temp_station()
        closed_unit = self.temp_unit(type_id=stocked, station_id=closed_station)
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (closed_station,))
        self.assertIsNotNone(closed_unit)
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO", "CERRADO",
                      "CANCELADO"):
            result = dc.get_unit_recommendations(self.incident_of(incident_type, state))
            self.assertEqual([r["unit_type_id"] for r in result], [stocked, empty], state)
            first, second = result
            self.assertEqual(first["available_count"], 2, state)
            self.assertEqual({u["unit_id"] for u in first["available_units"]}, {ok_a, ok_b})
            self.assertEqual(set(first["available_units"][0]), UNIT_KEYS)
            self.assertEqual((second["available_count"], second["available_units"]), (0, []))
        # Coincide con la consulta de unidades disponibles para el borrador
        available = {u["unit_id"] for u in dm.list_available_units(type_id=stocked)}
        self.assertEqual(available, {ok_a, ok_b})

    # 9
    def test_authorization(self):
        incident_id = self.incident_of(self.temp_type())
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            self.assertEqual(dc.get_unit_recommendations(incident_id), [])
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            dc.get_unit_recommendations(incident_id)

    # 10
    def test_read_only(self):
        source = (ROOT / "models" / "dispatch_model.py").read_text(encoding="utf-8")
        body = function_body(source, "get_unit_recommendations_for_incident")
        for forbidden in ("INSERT", "UPDATE", "DELETE", "FOR SHARE", "FOR UPDATE", "_write("):
            self.assertNotIn(forbidden, body)
        self.assertEqual(body.count("_fetch_all("), 1)
        fire = self.seed_id("tipos_incidente", "id_tipo_incidente", "INCENDIO")
        incident_id = self.incident_of(fire)
        from tests._dispatch_support import full_snapshot
        before = full_snapshot()
        compat = db_all("SELECT * FROM compatibilidad_incidente_unidad ORDER BY 1, 2")
        dc.get_unit_recommendations(incident_id)
        self.assertEqual(full_snapshot(), before)
        self.assertEqual(
            db_all("SELECT * FROM compatibilidad_incidente_unidad ORDER BY 1, 2"), compat)


if __name__ == "__main__":
    unittest.main()

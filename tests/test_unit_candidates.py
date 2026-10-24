import unittest
from pathlib import Path
from uuid import uuid4

from controllers import dispatch_controller as dc
from controllers.access_controller import NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from tests._dispatch_support import DispatchTestBase, db_all, db_exec, full_snapshot
from tests.test_dispatch_model import function_body
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_KEYS = {
    "unit_id", "unit_code", "unit_name", "unit_type_id", "unit_type_name", "station_id",
    "station_code", "station_name", "recommendation_priority", "recommendation_note",
    "approx_distance",
}


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class UnitCandidatesTest(DispatchTestBase):
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

    def station(self, lat=None, lon=None, code=None):
        station_id = db_exec(
            "INSERT INTO estaciones (codigo, nombre, direccion, latitud, longitud) "
            "VALUES (%s, %s, 'Dir', %s, %s)",
            (code or f"TUS-{uuid4().hex[:8]}", "Estación temporal", lat, lon))
        self.station_ids.append(station_id)
        return station_id

    def incident(self, incident_type, lat=None, lon=None, state="REPORTADO"):
        self._n += 1
        incident_id = self.insert_incident(f"CA{self._n}", state, tipo=incident_type)
        if lat is not None:
            db_exec("UPDATE incidentes SET latitud = %s, longitud = %s WHERE id_incidente = %s",
                    (lat, lon, incident_id))
        return incident_id

    def candidates(self, incident_id):
        return dc.get_unit_candidates(incident_id)

    def ids(self, candidates):
        return [c["unit_id"] for c in candidates]

    # 1, 6
    def test_closer_first_and_distance_value(self):
        incident_type, unit_type = self.temp_type(), self.temp_unit_type()
        self.compat(incident_type, unit_type, 1, "Principal")
        near = self.station(14.01, -90.0)
        far = self.station(14.08, -90.0)
        far_unit = self.temp_unit(station_id=far, type_id=unit_type, name="Lejana")
        near_unit = self.temp_unit(station_id=near, type_id=unit_type, name="Cercana")
        result = self.candidates(self.incident(incident_type, 14.0, -90.0))
        self.assertEqual(self.ids(result), [near_unit, far_unit])
        self.assertEqual(set(result[0]), CANDIDATE_KEYS)
        self.assertEqual(result[0]["unit_name"], "Cercana")
        self.assertEqual(result[0]["recommendation_note"], "Principal")
        self.assertEqual(result[0]["unit_type_id"], unit_type)
        # 0.01 grado de latitud ~ 1.11 km; 0.08 ~ 8.9 km
        self.assertAlmostEqual(result[0]["approx_distance"], 1.11, delta=0.05)
        self.assertAlmostEqual(result[1]["approx_distance"], 8.90, delta=0.1)
        self.assertTrue(all(c["approx_distance"] >= 0 for c in result))
        # Un grado de latitud completo: ~111.19 km; mismo punto: 0
        one_degree = self.station(15.0, -90.0)
        same_point = self.station(14.0, -90.0)
        far_one = self.temp_unit(station_id=one_degree, type_id=unit_type)
        zero = self.temp_unit(station_id=same_point, type_id=unit_type)
        by_id = {c["unit_id"]: c for c in self.candidates(self.incident(incident_type, 14.0, -90.0))}
        self.assertAlmostEqual(by_id[far_one]["approx_distance"], 111.19, delta=0.5)
        self.assertEqual(by_id[zero]["approx_distance"], 0.0)

    # 2
    def test_priority_dominates_distance(self):
        incident_type = self.temp_type()
        first_type, second_type = self.temp_unit_type(), self.temp_unit_type()
        self.compat(incident_type, first_type, 1)
        self.compat(incident_type, second_type, 2)
        far = self.station(14.08, -90.0)
        near = self.station(14.01, -90.0)
        far_first = self.temp_unit(station_id=far, type_id=first_type)
        near_second = self.temp_unit(station_id=near, type_id=second_type)
        result = self.candidates(self.incident(incident_type, 14.0, -90.0))
        self.assertEqual(self.ids(result), [far_first, near_second])
        self.assertEqual([c["recommendation_priority"] for c in result], [1, 2])
        self.assertGreater(result[0]["approx_distance"], result[1]["approx_distance"])

    # 3
    def test_exclusions(self):
        incident_type = self.temp_type()
        good, inactive_compat, inactive_type = (self.temp_unit_type() for _ in range(3))
        self.compat(incident_type, good, 1)
        self.compat(incident_type, inactive_compat, 2, active=False)
        self.compat(incident_type, inactive_type, 3)
        station = self.station(14.0, -90.0)
        eligible = self.temp_unit(station_id=station, type_id=good)
        for state in ("DESPACHADA", "EN_RUTA", "EN_ESCENA", "REGRESANDO", "MANTENIMIENTO",
                      "FUERA_DE_SERVICIO"):
            self.temp_unit(state, station_id=station, type_id=good)
        self.temp_unit(active=False, station_id=station, type_id=good)
        closed = self.station(14.0, -90.0)
        self.temp_unit(station_id=closed, type_id=good)
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (closed,))
        # Unidades de tipos con compatibilidad inactiva o tipo inactivo tampoco entran
        self.temp_unit(station_id=station, type_id=inactive_compat)
        hidden = self.temp_unit(station_id=station, type_id=inactive_type)
        db_exec("UPDATE tipos_unidad SET activo = 0 WHERE id_tipo_unidad = %s", (inactive_type,))
        self.assertIsNotNone(hidden)
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO", "CERRADO"):
            result = self.candidates(self.incident(incident_type, 14.0, -90.0, state))
            self.assertEqual(self.ids(result), [eligible], state)
        # Misma elegibilidad que el recomendador por tipo
        incident_id = self.incident(incident_type)
        by_type = dc.get_unit_recommendations(incident_id)
        flat = {u["unit_id"] for r in by_type for u in r["available_units"]}
        self.assertEqual(set(self.ids(self.candidates(incident_id))), flat)
        self.assertEqual(sum(r["available_count"] for r in by_type),
                         len(self.candidates(incident_id)))

    # 4
    def test_station_without_coordinates_goes_after_known(self):
        incident_type, unit_type = self.temp_type(), self.temp_unit_type()
        self.compat(incident_type, unit_type, 1)
        unknown = self.station(code="TUS-AAAA0001")
        known = self.station(14.05, -90.0, code="TUS-ZZZZ0001")
        unknown_unit = self.temp_unit(station_id=unknown, type_id=unit_type)
        known_unit = self.temp_unit(station_id=known, type_id=unit_type)
        result = self.candidates(self.incident(incident_type, 14.0, -90.0))
        self.assertEqual(self.ids(result), [known_unit, unknown_unit])
        self.assertIsNotNone(result[0]["approx_distance"])
        self.assertIsNone(result[1]["approx_distance"])

    # 5
    def test_incident_without_coordinates_is_deterministic(self):
        incident_type = self.temp_type()
        type_a, type_b = self.temp_unit_type(), self.temp_unit_type()
        self.compat(incident_type, type_a, 1)
        self.compat(incident_type, type_b, 2)
        station_b = self.station(14.0, -90.0, code="TUS-BBBB0001")
        station_a = self.station(15.0, -90.0, code="TUS-AAAA0002")
        low = self.temp_unit(station_id=station_b, type_id=type_b)
        second = self.temp_unit(station_id=station_b, type_id=type_a)
        first = self.temp_unit(station_id=station_a, type_id=type_a)
        incident_id = self.incident(incident_type)
        result = self.candidates(incident_id)
        self.assertEqual(self.ids(result), [first, second, low])
        self.assertTrue(all(c["approx_distance"] is None for c in result))
        self.assertEqual(self.ids(self.candidates(incident_id)), self.ids(result))

    # tie-break final
    def test_final_tie_break_by_unit_code(self):
        incident_type, unit_type = self.temp_type(), self.temp_unit_type()
        self.compat(incident_type, unit_type, 1)
        station = self.station(14.0, -90.0)
        units = [self.temp_unit(station_id=station, type_id=unit_type) for _ in range(3)]
        result = self.candidates(self.incident(incident_type, 14.0, -90.0))
        codes = [c["unit_code"] for c in result]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(set(self.ids(result)), set(units))

    # errores, autorización
    def test_errors_and_authorization(self):
        for missing in (999999, 0, -1):
            with self.assertRaises(dc.DispatchIncidentNotFoundError):
                dc.get_unit_candidates(missing)
            with self.assertRaises(dm.DispatchIncidentNotFoundError):
                dm.get_unit_candidates_for_incident(missing)
        for bad in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.get_unit_candidates(bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.get_unit_candidates_for_incident(bad)
        incident_id = self.incident(self.temp_type())
        self.assertEqual(dc.get_unit_candidates(incident_id), [])
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            self.assertEqual(dc.get_unit_candidates(incident_id), [])
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            dc.get_unit_candidates(incident_id)

    # solo lectura
    def test_read_only(self):
        source = (ROOT / "models" / "dispatch_model.py").read_text(encoding="utf-8")
        for name in ("get_unit_candidates_for_incident", "_approx_distance_km",
                     "get_unit_recommendations_for_incident"):
            body = function_body(source, name)
            for forbidden in ("INSERT", "UPDATE", "DELETE", "FOR SHARE", "FOR UPDATE",
                              "_write("):
                self.assertNotIn(forbidden, body, name)
        fragment = source[source.index("_RECOMMENDATION_FROM = "):]
        fragment = fragment[:fragment.index('"""\n\n\n', 30)]
        for forbidden in ("FOR SHARE", "FOR UPDATE", "INSERT", "UPDATE ", "DELETE"):
            self.assertNotIn(forbidden, fragment)
        incident_type, unit_type = self.temp_type(), self.temp_unit_type()
        self.compat(incident_type, unit_type, 1)
        self.temp_unit(type_id=unit_type)
        incident_id = self.incident(incident_type)
        before = full_snapshot()
        compat = db_all("SELECT * FROM compatibilidad_incidente_unidad ORDER BY 1, 2")
        dc.get_unit_candidates(incident_id)
        self.assertEqual(full_snapshot(), before)
        self.assertEqual(
            db_all("SELECT * FROM compatibilidad_incidente_unidad ORDER BY 1, 2"), compat)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from unittest import mock

import mysql.connector

from controllers import dashboard_controller as dbc
from controllers.access_controller import NotAuthenticated
from controllers.auth_controller import authenticate
from models import dashboard_model as dbm
from models import dispatch_model as dm
from tests._dispatch_support import DispatchTestBase, db_all, db_exec, full_snapshot
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent
DATA_KEYS = {"active_incidents", "critical_incidents", "available_units",
             "avg_response_minutes", "recent_activity", "incidents_by_type",
             "incidents_by_period"}
ACTIVITY_KEYS = {"occurred_at", "username", "action", "entity", "entity_id", "detail"}


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class DashboardTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self._n = 0
        self.top_priority = db_all(
            "SELECT id_prioridad FROM prioridades WHERE activo = 1 "
            "ORDER BY nivel DESC LIMIT 1")[0][0]
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def incident(self, state="REPORTADO", **overrides):
        self._n += 1
        return self.insert_incident(f"DB{self._n}", state, **overrides)

    # 1
    def test_active_and_critical_deltas(self):
        before = dbm.get_dashboard_data()
        self.incident(prioridad=self.top_priority)
        self.incident("EN_ATENCION", prioridad=self.top_priority)
        self.incident("ASIGNADO")
        self.incident("CERRADO", prioridad=self.top_priority)
        self.incident("CANCELADO", prioridad=self.top_priority)
        after = dbm.get_dashboard_data()
        self.assertEqual(after["active_incidents"] - before["active_incidents"], 3)
        self.assertEqual(after["critical_incidents"] - before["critical_incidents"], 2)
        # Comparación con una consulta independiente
        independent = db_all(
            "SELECT COUNT(*) FROM incidentes i JOIN estados_incidente s "
            "ON s.id_estado_incidente = i.id_estado_incidente "
            "WHERE s.codigo NOT IN ('CERRADO', 'CANCELADO')")[0][0]
        self.assertEqual(after["active_incidents"], independent)

    # 2
    def test_available_units(self):
        before = dbm.get_dashboard_data()["available_units"]
        self.temp_unit()
        self.temp_unit("DESPACHADA")
        self.temp_unit(active=False)
        closed = self.temp_station()
        self.temp_unit(station_id=closed)
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (closed,))
        inactive_type = self.temp_unit_type()
        self.temp_unit(type_id=inactive_type)
        db_exec("UPDATE tipos_unidad SET activo = 0 WHERE id_tipo_unidad = %s", (inactive_type,))
        after = dbm.get_dashboard_data()["available_units"]
        self.assertEqual(after - before, 1)
        # Coincide con list_available_units() restringido a tipos activos
        active_types = {r[0] for r in db_all(
            "SELECT id_tipo_unidad FROM tipos_unidad WHERE activo = 1")}
        expected = len([u for u in dm.list_available_units() if u["type_id"] in active_types])
        self.assertEqual(after, expected)

    # 3
    def test_average_response_minutes(self):
        def independent():
            value = db_all(
                "SELECT AVG(TIMESTAMPDIFF(SECOND, fecha_salida, fecha_llegada_escena)) / 60 "
                "FROM despacho_unidades WHERE fecha_salida IS NOT NULL "
                "AND fecha_llegada_escena IS NOT NULL")[0][0]
            return None if value is None else round(float(value), 1)

        before = dbm.get_dashboard_data()["avg_response_minutes"]
        self.assertEqual(before, independent())
        dispatch = self.add_dispatch(self.incident("ASIGNADO"), "ACTIVO")
        for minutes in (10, 30):
            unit_id = self.temp_unit()
            self.add_assigned_participation(dispatch, unit_id)
            db_exec(
                "UPDATE despacho_unidades SET fecha_salida = DATE_ADD(fecha_asignacion, "
                "INTERVAL 1 MINUTE), fecha_llegada_escena = DATE_ADD(fecha_asignacion, "
                "INTERVAL %s MINUTE) WHERE id_despacho = %s AND id_unidad = %s",
                (minutes + 1, dispatch, unit_id))
        after = dbm.get_dashboard_data()["avg_response_minutes"]
        self.assertIsInstance(after, float)
        self.assertEqual(after, independent())
        # Sin llegada la participación se ignora
        pending = self.temp_unit()
        self.add_assigned_participation(dispatch, pending)
        db_exec("UPDATE despacho_unidades SET fecha_salida = fecha_asignacion "
                "WHERE id_despacho = %s AND id_unidad = %s", (dispatch, pending))
        self.assertEqual(dbm.get_dashboard_data()["avg_response_minutes"], after)

    # 4
    def test_recent_activity(self):
        incident_id = self.incident()
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        activity = dbm.get_dashboard_data()["recent_activity"]
        self.assertLessEqual(len(activity), 15)
        self.assertEqual(set(activity[0]), ACTIVITY_KEYS)
        self.assertEqual(activity[0]["entity"], "DESPACHO")
        self.assertEqual(activity[0]["entity_id"], dispatch_id)
        self.assertEqual(activity[0]["username"], "admin.demo")
        self.assertIn(f"{self.prefix}-DB1", activity[0]["detail"])
        stamps = [a["occurred_at"] for a in activity]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        expected = db_all(
            "SELECT accion, entidad, id_entidad, detalle FROM bitacora "
            "ORDER BY fecha_hora DESC, id_bitacora DESC LIMIT 15")
        self.assertEqual([(a["action"], a["entity"], a["entity_id"], a["detail"])
                          for a in activity], [tuple(r) for r in expected])

    # 5
    def test_incidents_by_type(self):
        before = {t["type_id"]: t["count"] for t in dbm.get_dashboard_data()["incidents_by_type"]}
        for _ in range(3):
            self.incident()
        self.incident("CERRADO")
        result = dbm.get_dashboard_data()["incidents_by_type"]
        after = {t["type_id"]: t["count"] for t in result}
        self.assertEqual(after[self.seed_type] - before.get(self.seed_type, 0), 4)
        expected = db_all(
            "SELECT t.id_tipo_incidente, t.nombre, COUNT(*) FROM incidentes i "
            "JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente "
            "GROUP BY t.id_tipo_incidente, t.nombre ORDER BY COUNT(*) DESC, t.nombre ASC")
        self.assertEqual([(t["type_id"], t["type_name"], t["count"]) for t in result],
                         [tuple(r) for r in expected])
        self.assertEqual(set(result[0]), {"type_id", "type_name", "count"})

    # 6
    def test_incidents_by_period(self):
        today = db_all("SELECT CURRENT_DATE")[0][0].strftime("%Y-%m-%d")
        three = db_all("SELECT CURRENT_DATE - INTERVAL 3 DAY")[0][0].strftime("%Y-%m-%d")
        old = db_all("SELECT CURRENT_DATE - INTERVAL 30 DAY")[0][0].strftime("%Y-%m-%d")

        def by_date():
            return {p["date"]: p["count"] for p in dbm.get_dashboard_data()["incidents_by_period"]}

        before = by_date()
        for sql_offset, times in (("NOW()", 2), ("DATE_SUB(NOW(), INTERVAL 3 DAY)", 1),
                                  ("DATE_SUB(NOW(), INTERVAL 30 DAY)", 1)):
            for _ in range(times):
                incident_id = self.incident()
                db_exec(f"UPDATE incidentes SET fecha_reporte = {sql_offset} "
                        "WHERE id_incidente = %s", (incident_id,))
        result = dbm.get_dashboard_data()["incidents_by_period"]
        after = {p["date"]: p["count"] for p in result}
        self.assertEqual(after[today] - before.get(today, 0), 2)
        self.assertEqual(after[three] - before.get(three, 0), 1)
        self.assertNotIn(old, after)
        dates = [p["date"] for p in result]
        self.assertEqual(dates, sorted(dates))
        self.assertTrue(all(isinstance(p["date"], str) and p["count"] >= 1 for p in result))

    # 7
    def test_read_only_and_shape(self):
        source = (ROOT / "models" / "dashboard_model.py").read_text(encoding="utf-8")
        for forbidden in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE", "FOR SHARE", "commit(",
                          "start_transaction"):
            self.assertNotIn(forbidden, source)
        self.incident()
        before = full_snapshot()
        data = dbm.get_dashboard_data()
        self.assertEqual(full_snapshot(), before)
        self.assertEqual(set(data), DATA_KEYS)
        for key in ("active_incidents", "critical_incidents", "available_units"):
            self.assertIsInstance(data[key], int)
        self.assertTrue(data["avg_response_minutes"] is None
                        or isinstance(data["avg_response_minutes"], float))
        self.assertIsInstance(data["recent_activity"], list)

    # 8
    def test_controller(self):
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            self.assertEqual(set(dbc.get_dashboard_data()), DATA_KEYS)
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            dbc.get_dashboard_data()
        _login("admin.demo")
        failing = mysql.connector.Error("SELECT secreto FROM tabla")
        with mock.patch.object(dbm, "get_dashboard_data", side_effect=failing):
            with self.assertRaises(dbc.DashboardServiceError) as ctx:
                dbc.get_dashboard_data()
        self.assertNotIn("SELECT", str(ctx.exception))
        self.assertIn("indicadores", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, failing)


if __name__ == "__main__":
    unittest.main()

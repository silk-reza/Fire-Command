import unittest
from pathlib import Path
from unittest import mock

import mysql.connector

from controllers import report_controller as rc
from controllers.access_controller import NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from models import report_model as rm
from tests._dispatch_support import DispatchTestBase, db_all, db_exec, full_snapshot
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class ReportBase(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self.person_prefix = f"TDP-{self.prefix[-8:]}"
        self._n = 0
        self._p = 0
        session.end_session()
        _login("admin.demo")

    def cleanup_steps(self):
        steps = [
            ("DELETE FROM despacho_personal WHERE id_personal IN "
             "(SELECT id_personal FROM personal WHERE codigo LIKE %s)",
             (self.person_prefix + "%",)),
        ]
        steps += super().cleanup_steps()
        steps.append(("DELETE FROM personal WHERE codigo LIKE %s", (self.person_prefix + "%",)))
        return steps

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def person(self):
        self._p += 1
        return db_exec(
            "INSERT INTO personal (id_estacion, codigo, nombres, apellidos, cargo, activo) "
            "VALUES (%s, %s, %s, %s, 'Bombero', 1)",
            (self.seed_station, f"{self.person_prefix}-{self._p}", f"Nombre{self._p}",
             f"Apellido{self._p}"))

    def incident(self, state="REPORTADO", **overrides):
        self._n += 1
        return self.insert_incident(f"RP{self._n}", state, **overrides)

    def set_reported(self, incident_id, sql_expression):
        db_exec(f"UPDATE incidentes SET fecha_reporte = {sql_expression} "
                "WHERE id_incidente = %s", (incident_id,))

    def activate_on(self, incident_id, units_steps, station_ids=None):
        """Despacho activado con una unidad por elemento de units_steps (pasos operativos)."""
        dispatch_id = dm.create_dispatch_draft(incident_id, "Obs", self.admin_id)
        units = []
        for index, _ in enumerate(units_steps):
            station = station_ids[index] if station_ids else None
            unit_id = self.temp_unit(station_id=station)
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        return dispatch_id, units

    def run_steps(self, dispatch_id, unit_id, steps):
        operations = (dm.mark_unit_en_route, dm.mark_unit_on_scene, dm.start_unit_return)
        for operation in operations[:steps]:
            operation(dispatch_id, unit_id, self.admin_id)


class IncidentReportTest(ReportBase):
    def build_rich(self):
        incident_id = self.incident()
        dispatch_a, (u1, u2) = self.activate_on(incident_id, [3, 2])
        crew = [self.person(), self.person()]
        for person_id in crew:
            dm.assign_crew_member(dispatch_a, u1, person_id, self.admin_id)
        self.run_steps(dispatch_a, u1, 3)
        self.run_steps(dispatch_a, u2, 2)
        # Segundo despacho: borrador cancelado con una preselección (no es participación)
        dispatch_b = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        draft_unit = self.temp_unit()
        dm.add_unit_to_draft(dispatch_b, draft_unit)
        dm.cancel_dispatch_draft(dispatch_b, "No se necesita", self.admin_id)
        self.set_reported(incident_id, "DATE_SUB(fecha_reporte, INTERVAL 3 HOUR)")
        return incident_id, dispatch_a, dispatch_b, (u1, u2, draft_unit), crew

    # 1-5
    def test_rich_report(self):
        incident_id, dispatch_a, dispatch_b, (u1, u2, draft_unit), crew = self.build_rich()
        report = rc.get_incident_report(incident_id)
        self.assertEqual(report["code"], f"{self.prefix}-RP1")
        self.assertEqual(report["state_code"], "EN_ATENCION")
        self.assertEqual(report["type_name"], db_all(
            "SELECT nombre FROM tipos_incidente WHERE id_tipo_incidente = %s",
            (self.seed_type,))[0][0])
        self.assertEqual(report["registered_by_username"], "admin.demo")
        self.assertIsNone(report["latitude"])
        self.assertEqual(report["address"], "Dirección temporal")
        self.assertEqual([d["dispatch_id"] for d in report["dispatches"]],
                         [dispatch_a, dispatch_b])
        self.assertEqual([d["state_code"] for d in report["dispatches"]],
                         ["ACTIVO", "CANCELADO"])
        self.assertEqual(report["dispatches"][1]["cancel_reason"], "No se necesita")
        self.assertEqual(report["dispatches"][0]["observations"], "Obs")
        # Participaciones reales: la preselección del borrador cancelado queda fuera
        self.assertEqual({u["unit_id"] for u in report["units"]}, {u1, u2})
        by_unit = {u["unit_id"]: u for u in report["units"]}
        self.assertEqual(by_unit[u1]["participation_status"], "LIBERADA")
        self.assertEqual(by_unit[u2]["participation_status"], "ACTIVA")
        self.assertIsNotNone(by_unit[u1]["origin_station_code"])
        self.assertIsNotNone(by_unit[u1]["released_at"])
        self.assertIsNone(by_unit[u2]["released_at"])
        self.assertEqual([c["personnel_code"] for c in report["crew"]],
                         [f"{self.person_prefix}-1", f"{self.person_prefix}-2"])
        self.assertTrue(all(c["unit_code"] == by_unit[u1]["unit_code"] for c in report["crew"]))
        # Timeline con fechas reales y en orden
        keys = [e["key"] for e in report["timeline"]]
        self.assertEqual(keys, ["reported", "assigned", "attention_started"])
        times = [e["at"] for e in report["timeline"]]
        self.assertEqual(times, sorted(times))
        # Respuesta = primera llegada - reporte (≈ 3 h)
        expected = db_all(
            "SELECT TIMESTAMPDIFF(SECOND, i.fecha_reporte, MIN(du.fecha_llegada_escena)) / 60 "
            "FROM incidentes i JOIN despachos d ON d.id_incidente = i.id_incidente "
            "JOIN despacho_unidades du ON du.id_despacho = d.id_despacho "
            "WHERE i.id_incidente = %s", (incident_id,))[0][0]
        self.assertEqual(report["response_minutes"], round(float(expected), 1))
        self.assertGreater(report["response_minutes"], 179)
        self.assertIsNone(report["duration_minutes"])

    # 7, 10
    def test_no_arrival_and_open(self):
        incident_id = self.incident()
        self.activate_on(incident_id, [0])
        report = rm.get_incident_report(incident_id)
        self.assertIsNone(report["response_minutes"])
        self.assertIsNone(report["duration_minutes"])
        self.assertEqual(report["units"][0]["participation_status"], "ACTIVA")

    # 8, 9
    def test_durations(self):
        closed = self.incident("CERRADO")
        cancelled = self.incident("CANCELADO")
        self.assertEqual(rm.get_incident_report(closed)["duration_minutes"], 4.0)
        self.assertEqual(rm.get_incident_report(cancelled)["duration_minutes"], 1.0)
        report = rm.get_incident_report(closed)
        self.assertEqual([e["key"] for e in report["timeline"]],
                         ["reported", "assigned", "attention_started", "controlled", "closed"])
        self.assertEqual([e["key"] for e in rm.get_incident_report(cancelled)["timeline"]],
                         ["reported", "cancelled"])
        self.assertEqual(report["dispatches"], [])
        self.assertEqual((report["units"], report["crew"]), ([], []))

    # 11
    def test_missing_incident_and_controller_errors(self):
        for missing in (999999, 0, -1):
            with self.assertRaises(rm.ReportIncidentNotFoundError):
                rm.get_incident_report(missing)
            with self.assertRaises(rc.ReportNotFoundError):
                rc.get_incident_report(missing)
        for bad in (True, "1", None):
            with self.assertRaises(rc.ReportValidationError):
                rc.get_incident_report(bad)
        failing = mysql.connector.Error("SELECT secreto FROM tabla")
        with mock.patch.object(rm, "get_incident_report", side_effect=failing):
            with self.assertRaises(rc.ReportServiceError) as ctx:
                rc.get_incident_report(1)
        self.assertNotIn("SELECT", str(ctx.exception))

    def test_authorization(self):
        incident_id = self.incident()
        for username in ("admin.demo", "operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            self.assertEqual(rc.get_incident_report(incident_id)["incident_id"], incident_id)
            self.assertIn("incidents", rc.search_general_report({"type_id": self.seed_type}))
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            rc.get_incident_report(incident_id)
        with self.assertRaises(NotAuthenticated):
            rc.search_general_report()
        with self.assertRaises(NotAuthenticated):
            rc.export_general_pdf(None, "x.pdf")


class GeneralReportTest(ReportBase):
    def build_set(self):
        """Tres incidentes de un tipo y prioridades temporales, con actividad en 2 estaciones."""
        self.itype = self.temp_type()
        self.p_low, self.p_high = self.temp_priority(), self.temp_priority()
        st_a, st_b = self.temp_station(), self.temp_station()
        self.st_a, self.st_b = st_a, st_b
        old = self.incident(tipo=self.itype, prioridad=self.p_low)
        mid = self.incident(tipo=self.itype, prioridad=self.p_high)
        new = self.incident(tipo=self.itype, prioridad=self.p_high)
        self.set_reported(old, "DATE_SUB(NOW(), INTERVAL 10 DAY)")
        self.set_reported(mid, "DATE_SUB(NOW(), INTERVAL 5 DAY)")
        self.set_reported(new, "DATE_SUB(NOW(), INTERVAL 1 DAY)")
        # old: 2 participaciones en A (una con llegada); mid: 1 en B; new: ninguna
        d_old, (u1, u2) = self.activate_on(old, [2, 0], [st_a, st_a])
        self.run_steps(d_old, u1, 2)
        d_mid, (u3,) = self.activate_on(mid, [1], [st_b])
        self.run_steps(d_mid, u3, 2)
        # Borrador con preselección: no cuenta como actividad
        draft = dm.create_dispatch_draft(new, None, self.admin_id)
        dm.add_unit_to_draft(draft, self.temp_unit(station_id=st_b))
        # Se cierra después: las fechas del hito se calculan desde su fecha de reporte
        self.set_state(new, "CERRADO")
        return old, mid, new

    def independent(self, where_extra="", params=()):
        base = ("FROM incidentes i JOIN estados_incidente s ON s.id_estado_incidente = "
                "i.id_estado_incidente WHERE i.id_tipo_incidente = %s ")
        return base + where_extra, (self.itype,) + tuple(params)

    def check_metrics(self, result, where_extra="", params=()):
        """Compara tabla y métricas con consultas SQL independientes sobre el mismo conjunto."""
        base, args = self.independent(where_extra, params)
        ids = [r[0] for r in db_all("SELECT i.id_incidente " + base, args)]
        self.assertEqual({r["incident_id"] for r in result["incidents"]}, set(ids))
        self.assertEqual(result["metrics"]["incident_count"], len(result["incidents"]))
        if not ids:
            self.assertEqual(result["metrics"]["units_used"], 0)
            return ids
        marks = ", ".join(["%s"] * len(ids))
        avg = db_all(
            "SELECT AVG(sec) FROM (SELECT TIMESTAMPDIFF(SECOND, i.fecha_reporte, "
            "MIN(du.fecha_llegada_escena)) AS sec FROM incidentes i "
            "JOIN despachos d ON d.id_incidente = i.id_incidente "
            "JOIN despacho_unidades du ON du.id_despacho = d.id_despacho "
            f"WHERE i.id_incidente IN ({marks}) AND du.fecha_llegada_escena IS NOT NULL "
            "GROUP BY i.id_incidente) x", tuple(ids))[0][0]
        expected_avg = None if avg is None else round(float(avg) / 60, 1)
        self.assertEqual(result["metrics"]["avg_response_minutes"], expected_avg)
        units = db_all(
            "SELECT COUNT(DISTINCT du.id_unidad) FROM despachos d "
            "JOIN despacho_unidades du ON du.id_despacho = d.id_despacho "
            f"WHERE d.id_incidente IN ({marks}) AND du.fecha_asignacion IS NOT NULL",
            tuple(ids))[0][0]
        self.assertEqual(result["metrics"]["units_used"], units)
        stations = db_all(
            "SELECT e.id_estacion, COUNT(*) AS activity FROM despachos d "
            "JOIN despacho_unidades du ON du.id_despacho = d.id_despacho "
            "JOIN estaciones e ON e.id_estacion = du.id_estacion_origen "
            f"WHERE d.id_incidente IN ({marks}) AND du.fecha_asignacion IS NOT NULL "
            "GROUP BY e.id_estacion ORDER BY activity DESC, e.codigo ASC LIMIT 5",
            tuple(ids))
        self.assertEqual([(s["station_id"], s["activity"])
                          for s in result["metrics"]["top_stations"]],
                         [(r[0], r[1]) for r in stations])
        return ids

    # 12-21
    def test_no_filters_and_each_filter(self):
        old, mid, new = self.build_set()
        everything = rc.search_general_report()
        self.assertTrue({old, mid, new} <= {r["incident_id"] for r in everything["incidents"]})
        total = db_all("SELECT COUNT(*) FROM incidentes")[0][0]
        self.assertEqual(everything["metrics"]["incident_count"], total)
        self.assertEqual(len(everything["incidents"]), total)
        stamps = [(r["reported_at"], r["incident_id"]) for r in everything["incidents"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertEqual(everything["filters_applied"]["type_id"], None)

        by_type = rc.search_general_report({"type_id": self.itype})
        self.check_metrics(by_type)
        self.assertEqual({r["incident_id"] for r in by_type["incidents"]}, {old, mid, new})
        self.assertEqual(by_type["metrics"]["units_used"], 3)
        stations = by_type["metrics"]["top_stations"]
        self.assertEqual([(s["station_id"], s["activity"]) for s in stations],
                         sorted([(self.st_a, 2), (self.st_b, 1)], key=lambda x: -x[1]))
        self.assertIsNotNone(by_type["metrics"]["avg_response_minutes"])

        by_priority = rc.search_general_report({"type_id": self.itype, "priority_id": self.p_high})
        self.assertEqual({r["incident_id"] for r in by_priority["incidents"]}, {mid, new})
        self.check_metrics(by_priority, "AND i.id_prioridad = %s", (self.p_high,))

        by_code = rc.search_general_report({"type_id": self.itype, "state_code": "CERRADO"})
        self.assertEqual([r["incident_id"] for r in by_code["incidents"]], [new])
        self.check_metrics(by_code, "AND s.codigo = %s", ("CERRADO",))
        self.assertEqual(by_code["metrics"]["units_used"], 0)
        self.assertEqual(by_code["metrics"]["top_stations"], [])
        self.assertIsNone(by_code["metrics"]["avg_response_minutes"])
        expected = db_all("SELECT TIMESTAMPDIFF(SECOND, fecha_reporte, fecha_cierre) / 60 "
                          "FROM incidentes WHERE id_incidente = %s", (new,))[0][0]
        self.assertEqual(by_code["incidents"][0]["duration_minutes"], round(float(expected), 1))

        state_id = db_all("SELECT id_estado_incidente FROM estados_incidente "
                          "WHERE codigo = 'CERRADO'")[0][0]
        by_state_id = rc.search_general_report({"type_id": self.itype, "state_id": state_id})
        self.assertEqual([r["incident_id"] for r in by_state_id["incidents"]], [new])
        self.assertEqual(by_state_id["filters_applied"]["state_code"], "CERRADO")

        days = db_all("SELECT CURRENT_DATE - INTERVAL 6 DAY, CURRENT_DATE - INTERVAL 5 DAY")[0]
        by_dates = rc.search_general_report({
            "type_id": self.itype, "date_from": days[0].strftime("%Y-%m-%d"),
            "date_to": days[1].strftime("%Y-%m-%d")})
        self.assertEqual([r["incident_id"] for r in by_dates["incidents"]], [mid])
        self.assertEqual(by_dates["metrics"]["incident_count"], 1)
        # Fecha hasta inclusiva por día y desde == hasta
        same_day = rc.search_general_report({
            "type_id": self.itype, "date_from": days[1], "date_to": days[1]})
        self.assertEqual([r["incident_id"] for r in same_day["incidents"]], [mid])
        combo = rc.search_general_report({
            "type_id": self.itype, "priority_id": self.p_high, "state_code": "CERRADO",
            "date_from": (days[1]).strftime("%Y-%m-%d")})
        self.assertEqual([r["incident_id"] for r in combo["incidents"]], [new])
        empty = rc.search_general_report({"type_id": self.itype, "priority_id": self.p_low,
                                          "state_code": "CERRADO"})
        self.assertEqual(empty["incidents"], [])
        self.assertEqual(empty["metrics"]["incident_count"], 0)

    def test_row_keys_and_response(self):
        old, mid, new = self.build_set()
        result = rc.search_general_report({"type_id": self.itype})
        row = {r["incident_id"]: r for r in result["incidents"]}[old]
        self.assertEqual(set(row), {
            "incident_id", "code", "reported_at", "type_name", "priority_name", "state_code",
            "state_name", "address", "response_minutes", "duration_minutes"})
        self.assertGreater(row["response_minutes"], 14000)  # ~10 días
        self.assertIsNone(row["duration_minutes"])
        self.assertEqual(rc.get_incident_report(old)["response_minutes"], row["response_minutes"])
        self.assertEqual(set(result["metrics"]), {
            "incident_count", "avg_response_minutes", "top_stations", "units_used", "top_units"})
        self.assertEqual(result["filters_applied"]["type_name"], db_all(
            "SELECT nombre FROM tipos_incidente WHERE id_tipo_incidente = %s",
            (self.itype,))[0][0])

    # 22
    def test_invalid_filters(self):
        for bad in ({"date_from": "abc"}, {"date_to": 5}, {"type_id": True},
                    {"priority_id": "x"}, {"state_id": 1.5}, {"state_code": 3},
                    {"unknown": 1}, "texto",
                    {"date_from": "2026-05-02", "date_to": "2026-05-01"}):
            with self.assertRaises(rc.ReportValidationError, msg=bad):
                rc.search_general_report(bad)
            with self.assertRaises(rm.InvalidReportFilterError, msg=bad):
                rm.search_general_report(bad)
        # Valores en blanco equivalen a "sin filtro"
        blank = rm.search_general_report({"date_from": "", "type_id": None, "state_code": " "})
        self.assertEqual(blank["metrics"]["incident_count"],
                         db_all("SELECT COUNT(*) FROM incidentes")[0][0])
        failing = mysql.connector.Error("SELECT secreto")
        with mock.patch.object(rm, "search_general_report", side_effect=failing):
            with self.assertRaises(rc.ReportServiceError) as ctx:
                rc.search_general_report()
        self.assertNotIn("SELECT", str(ctx.exception))

    # solo lectura
    def test_read_only(self):
        source = (ROOT / "models" / "report_model.py").read_text(encoding="utf-8")
        for forbidden in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE", "FOR SHARE", "commit(",
                          "start_transaction"):
            self.assertNotIn(forbidden, source)
        old, mid, new = self.build_set()
        before = full_snapshot()
        rc.search_general_report({"type_id": self.itype})
        rc.get_incident_report(old)
        self.assertEqual(full_snapshot(), before)


if __name__ == "__main__":
    unittest.main()

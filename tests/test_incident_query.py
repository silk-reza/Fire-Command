import inspect
import unittest
from datetime import date, datetime
from unittest import mock

from controllers import incident_controller as ic
from controllers.access_controller import NotAuthenticated
from controllers.auth_controller import authenticate
from models import incident_model
from tests._incident_support import IncidentTestBase, db_all, db_exec, seed_snapshot
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
DAY = "2031-03-15"
DETAIL_KEYS = {
    "code", "reported_at", "type_name", "priority_name", "state_name", "address",
    "reference", "latitude", "longitude", "description", "affected_people",
    "registered_by_username", "assigned_at", "attention_started_at", "controlled_at",
    "closed_at", "cancelled_at", "cancel_reason",
}
# Funciones públicas del modelo antes del OBJ 09 (no debe aparecer ninguna nueva)
MODEL_PUBLIC_FUNCTIONS = {
    "create_incident", "update_incident", "cancel_incident", "search_incidents",
    "get_incident", "list_incident_types", "list_priorities", "list_incident_states",
}


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


TEMP_PREFIXES = ("TIN-", "TUI-", "TIG-", "QIN-")
_FOREIGN_SQL = " AND (i.codigo NOT REGEXP '^(TIN|TUI|TIG|QIN)-' OR i.codigo LIKE %s)"


class IncidentQueryTest(IncidentTestBase):
    def setUp(self):
        super().setUp()
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def _sql_ids(self, where="1=1", params=()):
        """Ids esperados por SQL: seed y temporales propios, nunca ajenos."""
        rows = db_all(
            "SELECT i.id_incidente FROM incidentes i WHERE (" + where + ")" + _FOREIGN_SQL
            + " ORDER BY i.fecha_reporte DESC, i.id_incidente DESC",
            tuple(params) + (self.prefix + "%",))
        return [row[0] for row in rows]

    def _ids(self, incidents):
        """Ids del resultado ignorando filas temporales de otros módulos de test."""
        return [i["incident_id"] for i in incidents
                if not i["code"].startswith(TEMP_PREFIXES)
                or i["code"].startswith(self.prefix)]

    def _scoped_state(self):
        """Estado global sin filas temporales ni bitácora de ellas."""
        temp = ("(SELECT id_incidente FROM incidentes WHERE codigo REGEXP "
                "'^(TIN|TUI|TIG|QIN)-')")
        return (
            seed_snapshot(),
            db_all("SELECT COUNT(*) FROM bitacora WHERE NOT (entidad = 'INCIDENTE' AND "
                   "id_entidad IN " + temp + ") AND NOT (entidad = 'DESPACHO' AND "
                   "id_entidad IN (SELECT id_despacho FROM despachos WHERE id_incidente IN "
                   + temp + "))")[0][0],
            db_all("SELECT * FROM usuarios ORDER BY id_usuario"),
            db_all("SELECT * FROM incidentes WHERE codigo NOT REGEXP "
                   "'^(TIN|TUI|TIG|QIN)-' ORDER BY id_incidente"),
            db_all("SELECT * FROM despachos WHERE id_incidente NOT IN " + temp
                   + " ORDER BY id_despacho"),
        )

    def _at(self, suffix, moment, **overrides):
        incident_id = self.insert_incident(suffix, **overrides)
        db_exec("UPDATE incidentes SET fecha_reporte = %s WHERE id_incidente = %s",
                (moment, incident_id))
        return incident_id

    def _dated(self):
        """Cuatro incidentes temporales alrededor de un día único."""
        return {
            "late": self._at("L", f"{DAY} 23:30:00"),
            "early": self._at("E", f"{DAY} 00:00:00"),
            "next": self._at("N", "2031-03-16 00:00:00"),
            "prev": self._at("P", "2031-03-14 23:59:59"),
        }

    def _mine(self, **kwargs):
        return self._ids(ic.filter_incidents(query=self.prefix, **kwargs))

    def test_list_all_by_role_and_no_session(self):
        expected = self._sql_ids()
        for username in ("admin.demo", "operador.norte", "consulta.sur"):
            session.end_session()
            _login(username)
            self.assertEqual(self._ids(ic.list_incidents()), expected, username)
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            ic.filter_incidents()
        with self.assertRaises(NotAuthenticated):
            ic.get_incident(1)

    def test_consulta_user_can_run_everything(self):
        session.end_session()
        _login("consulta.sur")
        self.assertEqual(session.get_session()["role_code"], "CONSULTA")
        first = ic.list_incidents()[0]["incident_id"]
        ic.filter_incidents(date_from="2026-01-01", date_to="2026-12-31", query="INC")
        self.assertEqual(ic.get_incident(first)["incident_id"], first)
        self.assertTrue(ic.list_incident_types())
        self.assertTrue(ic.list_priorities())
        self.assertTrue(ic.list_incident_states())

    def test_ordering_deterministic_with_ties(self):
        a = self._at("A", f"{DAY} 10:00:00")
        b = self._at("B", f"{DAY} 10:00:00")
        c = self._at("C", f"{DAY} 11:00:00")
        first = self._mine()
        self.assertEqual(first, [c, max(a, b), min(a, b)])
        for _ in range(3):
            self.assertEqual(self._mine(), first)
        everything = self._ids(ic.list_incidents())
        self.assertEqual(everything, self._sql_ids())

    def test_free_text(self):
        self.insert_incident("A", direccion="Zzcalle Rara 9",
                             descripcion="Zzincendio peculiar")
        self.assertEqual(len(self._mine()), 1)
        for term in ("zzcalle", "ZZINCENDIO", "rara 9", self.prefix.lower()):
            self.assertEqual(len(ic.search_incidents(term)), 1, term)
        self.assertEqual(ic.search_incidents("INC-2026-003"),
                         ic.filter_incidents(query="inc-2026-003"))
        self.assertEqual(len(ic.search_incidents("INC-2026-003")), 1)
        self.assertEqual(ic.search_incidents("nada-" + self.prefix), [])

    def test_catalog_filters_match_sql(self):
        types = {row[0] for row in db_all("SELECT id_tipo_incidente FROM incidentes")}
        for type_id in types:
            self.assertEqual(self._ids(ic.filter_incidents(type_id=type_id)),
                             self._sql_ids("i.id_tipo_incidente = %s", (type_id,)))
        prios = {row[0] for row in db_all("SELECT id_prioridad FROM incidentes")}
        for priority_id in prios:
            self.assertEqual(self._ids(ic.filter_incidents(priority_id=priority_id)),
                             self._sql_ids("i.id_prioridad = %s", (priority_id,)))
        states = {row[0] for row in db_all("SELECT id_estado_incidente FROM incidentes")}
        for state_id in states:
            self.assertEqual(self._ids(ic.filter_incidents(state_id=state_id)),
                             self._sql_ids("i.id_estado_incidente = %s", (state_id,)))

    def test_date_bounds(self):
        ids = self._dated()
        late, early, nxt, prev = ids["late"], ids["early"], ids["next"], ids["prev"]
        self.assertEqual(self._mine(date_from=DAY), [nxt, late, early])
        self.assertEqual(self._mine(date_to=DAY), [late, early, prev])
        self.assertEqual(self._mine(date_from=DAY, date_to=DAY), [late, early])
        self.assertEqual(self._mine(date_from=f"{DAY} 12:00", date_to=f"{DAY}T23:30:00"),
                         [late])
        self.assertEqual(self._mine(date_to=datetime(2031, 3, 15, 0, 0, 0)), [early, prev])
        self.assertEqual(self._mine(date_from=datetime(2031, 3, 15, 0, 0, 1)), [nxt, late])
        self.assertEqual(self._mine(date_from=date(2031, 3, 15), date_to=date(2031, 3, 15)),
                         [late, early])
        self.assertEqual(self._mine(date_from="  ", date_to=""), [nxt, late, early, prev])
        self.assertEqual(self._mine(date_from="2031-03-14", date_to="2031-03-16"),
                         [nxt, late, early, prev])

    def test_minute_precision_bounds(self):
        inside = self._at("A", f"{DAY} 23:30:30")
        after = self._at("B", f"{DAY} 23:31:00")
        exact = self._at("C", f"{DAY} 23:30:00")
        before = self._at("D", f"{DAY} 23:29:59")
        self.assertEqual(self._mine(date_to=f"{DAY} 23:30"), [inside, exact, before])
        self.assertNotIn(after, self._mine(date_to=f"{DAY} 23:30"))
        self.assertEqual(self._mine(date_to=f"{DAY} 23:30:00"), [exact, before])
        self.assertEqual(self._mine(date_from=f"{DAY} 23:30"), [after, inside, exact])
        self.assertNotIn(before, self._mine(date_from=f"{DAY} 23:30"))
        self.assertEqual(self._mine(date_from=f"{DAY} 23:30", date_to=f"{DAY}T23:30"),
                         [inside, exact])
        # La comparación usa las cotas normalizadas: 23:30 -> 23:30:00 y 23:30:59
        ic.filter_incidents(date_from=f"{DAY} 23:30", date_to=f"{DAY} 23:30")
        with self.assertRaises(ic.IncidentValidationError):
            ic.filter_incidents(date_from=f"{DAY} 23:31", date_to=f"{DAY} 23:30")

    def test_date_range_matches_sql_on_seed(self):
        expected = self._sql_ids("i.fecha_reporte >= %s AND i.fecha_reporte <= %s",
                            ("2026-10-01 00:00:00", "2026-10-12 23:59:59"))
        self.assertTrue(expected)
        self.assertEqual(
            self._ids(ic.filter_incidents(date_from="2026-10-01", date_to="2026-10-12")),
            expected)
        self.assertEqual(
            self._ids(ic.filter_incidents(date_from="2026-10-17")),
            self._sql_ids("i.fecha_reporte >= %s", ("2026-10-17 00:00:00",)))
        self.assertEqual(
            self._ids(ic.filter_incidents(date_to="2026-10-01")),
            self._sql_ids("i.fecha_reporte <= %s", ("2026-10-01 23:59:59",)))

    def test_inverted_and_invalid_dates(self):
        with mock.patch.object(incident_model, "search_incidents") as model:
            for start, end in (("2026-10-02", "2026-10-01"),
                               ("2026-10-01 10:00", "2026-10-01 09:59:59"),
                               (datetime(2026, 1, 2), date(2026, 1, 1))):
                with self.assertRaises(ic.IncidentValidationError) as ctx:
                    ic.filter_incidents(date_from=start, date_to=end)
                self.assertIn("posterior", str(ctx.exception))
            for bad in ("2026-13-01", "31/12/2026", "abc", 20261001, True, "2026-02-30",
                        1.5, ["2026-01-01"], "2026-01-01 25:00"):
                with self.assertRaises(ic.IncidentValidationError) as ctx:
                    ic.filter_incidents(date_from=bad)
                self.assertIn("La fecha desde no es válida", str(ctx.exception))
                with self.assertRaises(ic.IncidentValidationError) as ctx:
                    ic.filter_incidents(date_to=bad)
                self.assertIn("La fecha hasta no es válida", str(ctx.exception))
            model.assert_not_called()

    def test_combined_filters_match_sql(self):
        seed = db_all("SELECT id_tipo_incidente, id_prioridad, id_estado_incidente, codigo "
                      "FROM incidentes WHERE codigo = 'INC-2026-005'")[0]
        type_id, priority_id, state_id, code = seed
        self.assertEqual(
            self._ids(ic.filter_incidents(type_id=type_id, priority_id=priority_id)),
            self._sql_ids("i.id_tipo_incidente = %s AND i.id_prioridad = %s",
                     (type_id, priority_id)))
        self.assertEqual(
            self._ids(ic.filter_incidents(state_id=state_id, date_from="2026-09-01",
                                          date_to="2026-10-12")),
            self._sql_ids("i.id_estado_incidente = %s AND i.fecha_reporte >= %s "
                     "AND i.fecha_reporte <= %s",
                     (state_id, "2026-09-01 00:00:00", "2026-10-12 23:59:59")))
        self.assertEqual(
            self._ids(ic.filter_incidents(state_id=state_id, query="Zona")),
            self._sql_ids("i.id_estado_incidente = %s AND (i.direccion LIKE '%%Zona%%' "
                     "OR i.codigo LIKE '%%Zona%%' OR i.descripcion LIKE '%%Zona%%')",
                     (state_id,)))
        full = ic.filter_incidents(type_id=type_id, priority_id=priority_id,
                                   state_id=state_id, query="taller",
                                   date_from="2026-10-10", date_to="2026-10-10")
        self.assertEqual(
            self._ids(full),
            self._sql_ids("i.id_tipo_incidente = %s AND i.id_prioridad = %s AND "
                     "i.id_estado_incidente = %s AND i.descripcion LIKE '%%taller%%' "
                     "AND i.fecha_reporte BETWEEN '2026-10-10 00:00:00' AND "
                     "'2026-10-10 23:59:59'", (type_id, priority_id, state_id)))
        self.assertEqual([i["code"] for i in full], [code])
        # Un filtro que no coincide vacía el resultado aunque los demás sí
        self.assertEqual(ic.filter_incidents(type_id=type_id, query="zzz-no-existe"), [])

    def test_invalid_and_inactive_catalog_ids(self):
        for bad in ("1", 1.0, True, [1], {}):
            for name in ("type_id", "priority_id", "state_id"):
                with self.assertRaises(ic.IncidentValidationError):
                    ic.filter_incidents(**{name: bad})
        # Id bien formado pero inexistente: lista vacía, consistente en los tres filtros
        self.assertEqual(ic.filter_incidents(type_id=999999), [])
        self.assertEqual(ic.filter_incidents(priority_id=999999), [])
        self.assertEqual(ic.filter_incidents(state_id=999999), [])
        self.assertEqual(ic.filter_incidents(type_id=0), [])
        type_id = self.temp_type()
        priority_id = self.temp_priority()
        incident = self.insert_incident("H", tipo=type_id, prioridad=priority_id)
        self.deactivate_type(type_id)
        self.deactivate_priority(priority_id)
        self.assertEqual(self._ids(ic.filter_incidents(type_id=type_id)), [incident])
        self.assertEqual(self._ids(ic.filter_incidents(priority_id=priority_id)), [incident])
        self.assertEqual(self._ids(ic.filter_incidents(
            type_id=type_id, priority_id=priority_id, query=self.prefix)), [incident])
        self.assertIn(incident, self._ids(ic.list_incidents()))

    def test_injection_and_wildcards(self):
        self.insert_incident("W", descripcion="Avance 100%_ruta\\x")
        total = len(self._sql_ids())
        terms = ["' OR 1=1 --", "'; DROP TABLE incidentes; --", "%", "_", "\\",
                 "%%", "\\%", "' OR ''='", "100%_", "\\x"]
        for term in terms:
            expected = self._sql_ids(
                "LOCATE(%s, i.codigo) > 0 OR LOCATE(%s, i.descripcion) > 0 "
                "OR LOCATE(%s, i.direccion) > 0", (term, term, term))
            self.assertEqual(self._ids(ic.filter_incidents(query=term)), expected, term)
        for term in ("%", "_", "\\"):
            self.assertLess(len(self._ids(ic.filter_incidents(query=term))), total)
        self.assertEqual(len(self._sql_ids()), total)

    def test_detail_all_fields(self):
        incident = ic.get_incident(self._sql_ids("i.codigo = 'INC-2026-005'")[0])
        self.assertTrue(DETAIL_KEYS <= set(incident))
        self.assertEqual(incident["code"], "INC-2026-005")
        self.assertEqual(incident["type_name"], db_all(
            "SELECT t.nombre FROM incidentes i JOIN tipos_incidente t ON "
            "t.id_tipo_incidente = i.id_tipo_incidente WHERE i.codigo = 'INC-2026-005'")[0][0])
        self.assertEqual(incident["reported_at"], datetime(2026, 10, 10, 14, 0, 0))
        self.assertEqual(incident["closed_at"], datetime(2026, 10, 10, 16, 20, 0))
        self.assertIsInstance(incident["latitude"], float)
        self.assertEqual(incident["affected_people"], 2)
        self.assertTrue(incident["registered_by_username"])
        with self.assertRaises(ic.IncidentNotFoundError):
            ic.get_incident(999999)
        with self.assertRaises(ic.IncidentValidationError):
            ic.get_incident("1")
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            ic.get_incident(incident["incident_id"])
        _login("consulta.sur")
        self.assertEqual(ic.get_incident(incident["incident_id"])["code"], "INC-2026-005")

    def test_null_historical_fields(self):
        reported = ic.get_incident(self._sql_ids("i.codigo = 'INC-2026-001'")[0])
        for key in ("assigned_at", "attention_started_at", "controlled_at", "closed_at",
                    "cancelled_at", "cancel_reason"):
            self.assertIsNone(reported[key], key)
        no_coords = ic.get_incident(self._sql_ids("i.codigo = 'INC-2026-008'")[0])
        self.assertIsNone(no_coords["latitude"])
        self.assertIsNone(no_coords["longitude"])
        self.assertTrue(no_coords["cancel_reason"])
        self.assertIsNotNone(no_coords["cancelled_at"])
        temp = ic.get_incident(self.insert_incident("Z"))
        self.assertIsNone(temp["reference"])
        self.assertIsNone(temp["latitude"])
        self.assertIsNone(temp["cancel_reason"])

    def test_read_only(self):
        before = self._scoped_state()
        ic.list_incidents()
        ic.search_incidents("INC")
        ic.filter_incidents(1, 1, 1, "x", "2026-01-01", "2026-12-31")
        ic.filter_incidents(date_from="2026-10-01")
        ic.get_incident(self._sql_ids()[0])
        ic.list_incident_types()
        ic.list_priorities()
        ic.list_incident_states()
        after = self._scoped_state()
        self.assertEqual(before, after)
        public = {name for name, obj in vars(incident_model).items()
                  if inspect.isfunction(obj) and obj.__module__ == incident_model.__name__
                  and not name.startswith("_")}
        self.assertEqual(public, MODEL_PUBLIC_FUNCTIONS)
        source = inspect.getsource(incident_model.search_incidents).upper()
        for word in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE", "FOR SHARE"):
            self.assertNotIn(word, source)
        self.assertNotIn("FOR UPDATE", incident_model._INCIDENT_SELECT.upper())

    def test_refresh_reads_database(self):
        incident = self.insert_incident("R", direccion="Dirección inicial")
        self.assertEqual(self._mine(), [incident])
        self.assertEqual(ic.get_incident(incident)["address"], "Dirección inicial")
        db_exec("UPDATE incidentes SET direccion = 'Cambiada RRR' WHERE id_incidente = %s",
                (incident,))
        self.assertEqual(ic.get_incident(incident)["address"], "Cambiada RRR")
        self.assertEqual(self._ids(ic.filter_incidents(query="Cambiada RRR")), [incident])
        db_exec("UPDATE incidentes SET fecha_reporte = %s WHERE id_incidente = %s",
                (f"{DAY} 08:00:00", incident))
        self.assertEqual(self._mine(date_from="2031-03-16"), [])
        self.assertEqual(self._mine(date_from=DAY, date_to=DAY), [incident])

    def test_single_query_for_list(self):
        real = incident_model.get_connection
        with mock.patch.object(incident_model, "get_connection", wraps=real) as spy:
            ic.filter_incidents(type_id=1, priority_id=1, state_id=1, query="x",
                                date_from="2026-01-01", date_to="2026-12-31")
        self.assertEqual(spy.call_count, 1)
        with mock.patch.object(incident_model, "get_connection", wraps=real) as spy:
            ic.list_incidents()
        self.assertEqual(spy.call_count, 1)


if __name__ == "__main__":
    unittest.main()

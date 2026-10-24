import unittest
from unittest import mock

import mysql.connector

from controllers import access_controller
from controllers import dispatch_controller as dc
from controllers import incident_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model
from tests._dispatch_support import DispatchTestBase, db_exec
from utils import session
from utils.permissions import (
    PERMISSION_OPERATE_DISPATCHES,
    PERMISSION_VIEW_OPERATIONAL_DATA,
)

DEMO_PASSWORD = "FireDemo2026!"
STATE_KEYS = ("state", "status", "state_id", "state_code", "id_estado_despacho")
DATE_KEYS = ("fecha_despacho", "dispatched_at", "fecha_asignacion", "assigned_at",
             "fecha_liberacion", "fecha_cancelacion", "cancelled_at", "motivo_cancelacion",
             "cancel_reason", "fecha_finalizacion", "finished_at")
ORIGIN_KEYS = ("id_estacion_origen", "origin_station_id")
USER_KEYS = ("user", "id_usuario", "created_by", "user_id", "created_by_id")


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class DispatchControllerTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self._counter = 0
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def incident(self, state="REPORTADO"):
        self._counter += 1
        return self.insert_incident(f"C{self._counter}", state)

    def create(self, incident_id=None, observations=None):
        incident_id = incident_id or self.incident()
        return dc.create_dispatch_draft(
            {"incident_id": incident_id, "observations": observations})

    # 1, 2, 3
    def test_create_flow(self):
        incident_id = self.incident()
        incident_before = self.row(incident_id)
        dispatch = self.create(incident_id, "  Notas  ")
        self.assertEqual(dispatch["state_code"], "CREADO")
        self.assertEqual(dispatch["incident_id"], incident_id)
        self.assertEqual(dispatch["observations"], "Notas")
        self.assertEqual(dispatch["created_by_username"], "admin.demo")
        self.assertIsNone(dispatch["dispatched_at"])
        self.assertEqual(dispatch["units"], [])
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(len(self.dispatch_audit(dispatch["dispatch_id"])), 1)
        # El creador es siempre el usuario de la sesión
        session.end_session()
        _login("operador.central")
        other = self.create(incident_id)
        self.assertEqual(other["created_by_username"], "operador.central")
        self.assertEqual(self.dispatch_audit(other["dispatch_id"])[0][0],
                         self.user_id("operador.central"))
        listed = dc.list_dispatches(incident_id=incident_id)
        self.assertEqual([d["dispatch_id"] for d in listed],
                         [other["dispatch_id"], dispatch["dispatch_id"]])
        self.assertEqual(len(dc.list_draft_dispatches(incident_id)), 2)

    def test_create_terminal_and_missing(self):
        before = self.table_count("despachos")
        for state in ("CERRADO", "CANCELADO"):
            with self.assertRaises(dc.IncidentTerminalError) as ctx:
                self.create(self.incident(state))
            self.assertEqual(
                str(ctx.exception),
                "No se puede crear un despacho para un incidente cerrado o cancelado.")
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            dispatch = self.create(self.incident(state))
            self.assertEqual(dispatch["incident_state_code"], state)
        after_valid = self.table_count("despachos")
        self.assertEqual(after_valid, before + 4)
        for missing in (999999, 0, -1):
            with self.assertRaises(dc.DispatchIncidentNotFoundError):
                dc.create_dispatch_draft({"incident_id": missing})
        for bad in ("abc", None, True, 1.5, "1"):
            with self.assertRaises(dc.DispatchValidationError):
                dc.create_dispatch_draft({"incident_id": bad})
        for bad_data in (None, [], "x", {}):
            with self.assertRaises(dc.DispatchValidationError):
                dc.create_dispatch_draft(bad_data)
        for bad_obs in (5, ["x"], "x" * 65536):
            with self.assertRaises(dc.DispatchValidationError):
                dc.create_dispatch_draft({"incident_id": self.incident(), "observations": bad_obs})
        self.assertEqual(self.table_count("despachos"), after_valid)

    # 4, 5, 6, 7
    def test_units_flow(self):
        dispatch_id = self.create()["dispatch_id"]
        unit_id = self.temp_unit()
        unit_before = self.unit_row(unit_id)
        du_id = dc.add_unit_to_draft(dispatch_id, unit_id)
        self.assertIsInstance(du_id, int)
        full = dc.get_dispatch(dispatch_id)
        self.assertEqual(full["unit_count"], 1)
        self.assertEqual([u["unit_id"] for u in full["units"]], [unit_id])
        self.assertEqual(dc.list_draft_units(dispatch_id), full["units"])
        self.assertEqual(self.unit_row(unit_id), unit_before)
        with self.assertRaises(dc.DuplicateDraftUnitError):
            dc.add_unit_to_draft(dispatch_id, unit_id)
        self.assertEqual(len(self.preselections(dispatch_id)), 1)
        self.assertTrue(dc.remove_unit_from_draft(dispatch_id, unit_id))
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual(self.preselections(dispatch_id), [])
        with self.assertRaises(dc.DraftUnitNotFoundError):
            dc.remove_unit_from_draft(dispatch_id, unit_id)
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.remove_unit_from_draft(999999, unit_id)
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.add_unit_to_draft(999999, unit_id)

    def test_ineligible_and_invalid_units(self):
        dispatch_id = self.create()["dispatch_id"]
        for state in ("DESPACHADA", "EN_RUTA", "EN_ESCENA", "REGRESANDO",
                      "MANTENIMIENTO", "FUERA_DE_SERVICIO"):
            with self.assertRaises(dc.UnitNotEligibleError):
                dc.add_unit_to_draft(dispatch_id, self.temp_unit(state))
        with self.assertRaises(dc.UnitNotEligibleError):
            dc.add_unit_to_draft(dispatch_id, self.temp_unit(active=False))
        station_id = self.temp_station()
        unit_id = self.temp_unit(station_id=station_id)
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station_id,))
        with self.assertRaises(dc.UnitNotEligibleError):
            dc.add_unit_to_draft(dispatch_id, unit_id)
        for missing in (999999, 0, -1):
            with self.assertRaises(dc.UnitNotFoundError):
                dc.add_unit_to_draft(dispatch_id, missing)
        for bad in (None, True, "1", 1.0):
            with self.assertRaises(dc.DispatchValidationError):
                dc.add_unit_to_draft(dispatch_id, bad)
            with self.assertRaises(dc.DispatchValidationError):
                dc.remove_unit_from_draft(dispatch_id, bad)
            with self.assertRaises(dc.DispatchValidationError):
                dc.add_unit_to_draft(bad, self.temp_unit())
            with self.assertRaises(dc.DispatchValidationError):
                dc.get_dispatch(bad)
        self.assertEqual(self.preselections(dispatch_id), [])

    # 8, 9
    def test_no_reservation_and_stale_draft(self):
        draft_a = self.create()["dispatch_id"]
        draft_b = self.create()["dispatch_id"]
        unit_id = self.temp_unit()
        dc.add_unit_to_draft(draft_a, unit_id)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.active_participation_count(unit_id), 0)
        self.assertNotIn(unit_id, [u["unit_id"] for u in dc.list_available_units(draft_a)])
        self.assertIn(unit_id, [u["unit_id"] for u in dc.list_available_units(draft_b)])
        dc.add_unit_to_draft(draft_b, unit_id)
        self.assertEqual(self.active_participation_count(unit_id), 0)
        self.set_unit_state(unit_id, "MANTENIMIENTO")
        for dispatch_id in (draft_a, draft_b):
            full = dc.get_dispatch(dispatch_id)
            self.assertEqual(full["units"][0]["state_code"], "MANTENIMIENTO")
            self.assertFalse(full["units"][0]["eligible_now"])
            self.assertEqual(full["state_code"], "CREADO")
        self.assertNotIn(unit_id, [u["unit_id"] for u in dc.list_available_units()])
        self.assertEqual(self.unit_state(unit_id), "MANTENIMIENTO")
        self.assertEqual(self.active_participation_count(unit_id), 0)

    def test_available_units_query_validation(self):
        unit_id = self.temp_unit(name="Zqx unidad", plate=f"{self.plate_prefix}-Q")
        ids = lambda **kw: [u["unit_id"] for u in dc.list_available_units(**kw)]  # noqa: E731
        self.assertIn(unit_id, ids(query="  zqx "))
        self.assertIn(unit_id, ids(query=f"{self.plate_prefix}-Q"))
        self.assertEqual(ids(query="no-existe-" + self.unit_prefix), [])
        self.assertIn(unit_id, ids(query="   "))
        self.assertIn(unit_id, ids(station_id=self.seed_station, type_id=self.seed_unit_type))
        for kwargs in ({"query": 5}, {"type_id": "1"}, {"station_id": True},
                       {"dispatch_id": "x"}):
            with self.assertRaises(dc.DispatchValidationError):
                dc.list_available_units(**kwargs)

    def test_selectable_incidents(self):
        open_id, closed = self.incident(), self.incident("CERRADO")
        cancelled = self.incident("CANCELADO")
        found = [i["incident_id"] for i in dc.list_selectable_incidents()]
        self.assertIn(open_id, found)
        self.assertNotIn(closed, found)
        self.assertNotIn(cancelled, found)
        self.assertEqual(len(dc.list_dispatch_states()), 4)

    # 10
    def test_forbidden_keys(self):
        incident_id = self.incident()
        dispatch_id = self.create(incident_id)["dispatch_id"]
        before_all = self.table_count("despachos")
        before_row = self.dispatch_row(dispatch_id)
        audit_before = self.table_count("bitacora")
        groups = (
            (STATE_KEYS, "estado"), (DATE_KEYS, "fechas"),
            (ORIGIN_KEYS, "estación de origen"), (USER_KEYS, "usuario"),
        )
        for keys, word in groups:
            for key in keys:
                with self.assertRaises(dc.DispatchValidationError) as ctx:
                    dc.create_dispatch_draft({"incident_id": incident_id, key: 1})
                self.assertIn(word, str(ctx.exception), key)
                with self.assertRaises(dc.DispatchValidationError) as ctx:
                    dc.update_dispatch_draft(dispatch_id, {"observations": "x", key: 1})
                self.assertIn(word, str(ctx.exception), key)
        with self.assertRaises(dc.DispatchValidationError) as ctx:
            dc.update_dispatch_draft(dispatch_id, {"incident_id": incident_id})
        self.assertIn("incidente", str(ctx.exception))
        with self.assertRaises(dc.DispatchValidationError) as ctx:
            dc.create_dispatch_draft({"incident_id": incident_id, "cualquiera": 1})
        self.assertIn("Campo no permitido: cualquiera", str(ctx.exception))
        with self.assertRaises(dc.DispatchValidationError):
            dc.create_dispatch_draft({"incident_id": incident_id, "unit_ids": [1]})
        with self.assertRaises(dc.DispatchValidationError):
            dc.update_dispatch_draft(dispatch_id, {})
        with self.assertRaises(dc.DispatchValidationError):
            dc.update_dispatch_draft(dispatch_id, None)
        self.assertEqual(self.table_count("despachos"), before_all)
        self.assertEqual(self.dispatch_row(dispatch_id), before_row)
        self.assertEqual(self.table_count("bitacora"), audit_before)

    # 11
    def test_update_only_draft(self):
        dispatch_id = self.create(observations="A")["dispatch_id"]
        self.assertEqual(
            dc.update_dispatch_draft(dispatch_id, {"observations": " B "})["observations"], "B")
        self.assertIsNone(
            dc.update_dispatch_draft(dispatch_id, {"observations": "  "})["observations"])
        self.assertIsNone(
            dc.update_dispatch_draft(dispatch_id, {"observations": None})["observations"])
        for bad in (5, "x" * 65536):
            with self.assertRaises(dc.DispatchValidationError):
                dc.update_dispatch_draft(dispatch_id, {"observations": bad})
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.update_dispatch_draft(999999, {"observations": "x"})
        unit_id = self.temp_unit()
        for state in ("ACTIVO", "COMPLETADO", "CANCELADO"):
            other = self.add_dispatch(self.incident(), state)
            held = self.temp_unit()
            self.add_assigned_participation(other, held)
            before = self.dispatch_row(other)
            with self.assertRaises(dc.DispatchStateError) as ctx:
                dc.update_dispatch_draft(other, {"observations": "x"})
            self.assertEqual(str(ctx.exception),
                             "Solo se puede modificar o cancelar un despacho en estado Creado.")
            with self.assertRaises(dc.DispatchStateError):
                dc.add_unit_to_draft(other, unit_id)
            with self.assertRaises(dc.DispatchStateError):
                dc.remove_unit_from_draft(other, held)
            with self.assertRaises(dc.DispatchStateError):
                dc.cancel_dispatch_draft(other, "Motivo")
            self.assertEqual(self.dispatch_row(other), before)
            self.assertEqual(len(self.preselections(other)), 1)

    # 12, 13
    def test_cancel_draft(self):
        incident_id = self.incident()
        dispatch_id = self.create(incident_id)["dispatch_id"]
        unit_id = self.temp_unit()
        dc.add_unit_to_draft(dispatch_id, unit_id)
        incident_before, unit_before = self.row(incident_id), self.unit_row(unit_id)
        rows_before = self.preselections(dispatch_id)
        reason = "r" * 250
        result = dc.cancel_dispatch_draft(dispatch_id, f"  {reason}  ")
        self.assertEqual(result["state_code"], "CANCELADO")
        self.assertEqual(result["cancel_reason"], reason)
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual(self.preselections(dispatch_id), rows_before)
        self.assertEqual([r[1] for r in self.dispatch_audit(dispatch_id)],
                         ["CREAR", "CAMBIO_ESTADO"])
        with self.assertRaises(dc.DispatchStateError):
            dc.cancel_dispatch_draft(dispatch_id, "otra vez")
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.cancel_dispatch_draft(999999, "Motivo")

    def test_cancel_invalid_reason(self):
        dispatch_id = self.create()["dispatch_id"]
        before = self.dispatch_row(dispatch_id)
        audit_before = self.table_count("bitacora")
        for bad in (None, "", "   ", 5, ["x"], "x" * 251):
            with self.assertRaises(dc.DispatchValidationError):
                dc.cancel_dispatch_draft(dispatch_id, bad)
        for bad_id in (None, True, "1"):
            with self.assertRaises(dc.DispatchValidationError):
                dc.cancel_dispatch_draft(bad_id, "Motivo")
        self.assertEqual(self.dispatch_row(dispatch_id), before)
        self.assertEqual(self.table_count("bitacora"), audit_before)

    # 14
    def test_incident_cancellation_compat(self):
        incident_id = self.incident()
        draft_a = self.create(incident_id)["dispatch_id"]
        draft_b = self.create(incident_id)["dispatch_id"]
        dc.cancel_dispatch_draft(draft_b, "Cancelado antes")
        unit_id = self.temp_unit()
        dc.add_unit_to_draft(draft_a, unit_id)
        incident_controller.cancel_incident(incident_id, "Falsa alarma")
        cancelled = dc.get_dispatch(draft_a)
        self.assertEqual(cancelled["state_code"], "CANCELADO")
        self.assertEqual(cancelled["cancel_reason"], "Falsa alarma")
        self.assertEqual(len(cancelled["units"]), 1)
        self.assertEqual(dc.get_dispatch(draft_b)["cancel_reason"], "Cancelado antes")
        self.assertEqual(self.dispatch_audit(draft_a)[-1][4].split(" por ")[0],
                         "CREADO → CANCELADO")
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        with self.assertRaises(dc.IncidentTerminalError):
            self.create(incident_id)

    # 15
    def test_authorization_matrix(self):
        incident_id = self.incident()
        base = self.create(incident_id)["dispatch_id"]
        unit_id = self.temp_unit()
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            self._run_reads(base)
            dispatch = self.create(incident_id)["dispatch_id"]
            dc.update_dispatch_draft(dispatch, {"observations": username})
            dc.add_unit_to_draft(dispatch, unit_id)
            dc.remove_unit_from_draft(dispatch, unit_id)
            self.assertEqual(dc.cancel_dispatch_draft(dispatch, "Prueba")["state_code"],
                             "CANCELADO")
        # CONSULTA lee todo y no escribe nada
        session.end_session()
        _login("consulta.sur")
        self._run_reads(base)
        snapshot = (self.table_count("despachos"), self.table_count("despacho_unidades"),
                    self.table_count("bitacora"), self.dispatch_row(base))
        writes = (
            lambda: dc.create_dispatch_draft({"incident_id": incident_id}),
            lambda: dc.update_dispatch_draft(base, {"observations": "x"}),
            lambda: dc.add_unit_to_draft(base, unit_id),
            lambda: dc.remove_unit_from_draft(base, unit_id),
            lambda: dc.cancel_dispatch_draft(base, "Motivo"),
        )
        for write in writes:
            with self.assertRaises(AuthorizationDenied):
                write()
        self.assertEqual(snapshot, (self.table_count("despachos"),
                                    self.table_count("despacho_unidades"),
                                    self.table_count("bitacora"), self.dispatch_row(base)))
        # Sin sesión todo falla antes de tocar la base
        session.end_session()
        for read in self._reads(base):
            with self.assertRaises(NotAuthenticated):
                read()
        for write in writes:
            with self.assertRaises(NotAuthenticated):
                write()

    def _reads(self, dispatch_id):
        return (
            lambda: dc.list_dispatches(),
            lambda: dc.list_dispatches(incident_id=1, state_id=1),
            lambda: dc.list_draft_dispatches(),
            lambda: dc.get_dispatch(dispatch_id),
            lambda: dc.list_draft_units(dispatch_id),
            lambda: dc.list_available_units(),
            lambda: dc.list_selectable_incidents(),
            lambda: dc.list_dispatch_states(),
        )

    def _run_reads(self, dispatch_id):
        for read in self._reads(dispatch_id):
            read()

    def test_permissions_used(self):
        base = self.create()["dispatch_id"]
        unit_id = self.temp_unit()
        original = access_controller.require
        with mock.patch.object(access_controller, "require", wraps=original) as spy:
            for read in self._reads(base):
                spy.reset_mock()
                read()
                self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_VIEW_OPERATIONAL_DATA))
                self.assertEqual(spy.call_count, 1)
            writes = (
                lambda: dc.create_dispatch_draft({"incident_id": self.incident()}),
                lambda: dc.update_dispatch_draft(base, {"observations": "x"}),
                lambda: dc.add_unit_to_draft(base, unit_id),
                lambda: dc.remove_unit_from_draft(base, unit_id),
                lambda: dc.cancel_dispatch_draft(base, "Motivo"),
            )
            for write in writes:
                spy.reset_mock()
                write()
                self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_OPERATE_DISPATCHES))
                self.assertEqual(spy.call_count, 1)

    # Sin N+1
    def test_no_n_plus_one(self):
        dispatch_id = self.create()["dispatch_id"]
        for _ in range(3):
            dc.add_unit_to_draft(dispatch_id, self.temp_unit())
        self.create()
        real = dispatch_model.get_connection
        with mock.patch.object(dispatch_model, "get_connection", wraps=real) as spy:
            result = dc.list_dispatches()
            self.assertEqual(spy.call_count, 1)
            self.assertGreater(len(result), 3)
            spy.reset_mock()
            dc.list_draft_dispatches()
            self.assertEqual(spy.call_count, 1)
            spy.reset_mock()
            full = dc.get_dispatch(dispatch_id)
            self.assertLessEqual(spy.call_count, 2)
            self.assertEqual(len(full["units"]), 3)
            self.assertEqual(full["unit_count"], 3)

    # 17
    def test_atomic_rollback_through_controller(self):
        incident_id = self.incident()
        audit_before = self.table_count("bitacora")
        with mock.patch.object(dispatch_model, "_audit",
                               side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(dc.DispatchServiceError) as ctx:
                dc.create_dispatch_draft({"incident_id": incident_id})
        self.assertIsInstance(ctx.exception.__cause__, mysql.connector.Error)
        self.assertNotIn("INSERT", str(ctx.exception))
        self.assertEqual(self.dispatch_rows(incident_id), [])
        dispatch_id = self.create(incident_id)["dispatch_id"]
        audit_mid = self.table_count("bitacora")
        with mock.patch.object(dispatch_model, "_audit",
                               side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(dc.DispatchServiceError):
                dc.cancel_dispatch_draft(dispatch_id, "Motivo")
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertEqual(self.table_count("bitacora"), audit_mid)
        self.assertEqual(audit_mid, audit_before + 1)

    def test_technical_errors_are_wrapped(self):
        failing = mysql.connector.Error("SELECT secreto FROM tabla")
        for name, call in (
            ("list_dispatches", lambda: dc.list_dispatches()),
            ("list_available_units", lambda: dc.list_available_units()),
            ("list_selectable_incidents", lambda: dc.list_selectable_incidents()),
            ("list_dispatch_states", lambda: dc.list_dispatch_states()),
            ("get_dispatch", lambda: dc.get_dispatch(1)),
            ("list_draft_units", lambda: dc.list_draft_units(1)),
            ("list_dispatches", lambda: dc.list_draft_dispatches()),
        ):
            with mock.patch.object(dispatch_model, name, side_effect=failing):
                with self.assertRaises(dc.DispatchServiceError) as ctx:
                    call()
                self.assertNotIn("SELECT", str(ctx.exception))
                self.assertIs(ctx.exception.__cause__, failing)
        with mock.patch.object(dispatch_model, "add_unit_to_draft", side_effect=failing):
            with self.assertRaises(dc.DispatchServiceError):
                dc.add_unit_to_draft(1, 1)
        with mock.patch.object(dispatch_model, "remove_unit_from_draft", side_effect=failing):
            with self.assertRaises(dc.DispatchServiceError):
                dc.remove_unit_from_draft(1, 1)
        with mock.patch.object(dispatch_model, "update_dispatch_draft", side_effect=failing):
            with self.assertRaises(dc.DispatchServiceError):
                dc.update_dispatch_draft(1, {"observations": "x"})

    def test_terminal_incident_draft_rules_through_controller(self):
        for terminal in ("CERRADO", "CANCELADO"):
            incident_id = self.incident()
            dispatch_id = self.create(incident_id, "Orig")["dispatch_id"]
            kept, other = self.temp_unit(), self.temp_unit()
            dc.add_unit_to_draft(dispatch_id, kept)
            self.set_state(incident_id, terminal)
            writes = (
                lambda: dc.update_dispatch_draft(dispatch_id, {"observations": "Nueva"}),
                lambda: dc.update_dispatch_draft(dispatch_id, {"observations": "Orig"}),
                lambda: dc.add_unit_to_draft(dispatch_id, other),
                lambda: dc.remove_unit_from_draft(dispatch_id, kept),
            )
            before = (self.dispatch_row(dispatch_id), self.preselections(dispatch_id),
                      self.row(incident_id), self.table_count("bitacora"))
            for username in ("admin.demo", "operador.central"):
                session.end_session()
                _login(username)
                for write in writes:
                    with self.assertRaises(dc.DraftIncidentClosedError) as ctx:
                        write()
                    self.assertIsInstance(ctx.exception, dc.DispatchStateError)
                    self.assertIn("solo puede cancelarse", str(ctx.exception))
            # CONSULTA recibe la denegación antes que cualquier regla de estado
            session.end_session()
            _login("consulta.sur")
            for write in writes:
                with self.assertRaises(AuthorizationDenied):
                    write()
            self.assertEqual(before, (self.dispatch_row(dispatch_id),
                                      self.preselections(dispatch_id), self.row(incident_id),
                                      self.table_count("bitacora")))
            # La cancelación sigue permitida para sanear el borrador
            session.end_session()
            _login("operador.central")
            result = dc.cancel_dispatch_draft(dispatch_id, "c" * 250)
            self.assertEqual(result["state_code"], "CANCELADO")
            self.assertEqual(result["cancel_reason"], "c" * 250)
            self.assertEqual(len(result["units"]), 1)
            self.assertEqual(self.row(incident_id), before[2])
            self.assertEqual(self.dispatch_audit(dispatch_id)[-1][1], "CAMBIO_ESTADO")

    def test_seed_draft_readable_not_modified(self):
        seed = dc.get_dispatch(1)
        self.assertEqual(seed["state_code"], "CREADO")
        self.assertEqual(seed["incident_state_code"], "REPORTADO")
        self.assertIn("units", seed)
        self.assertEqual(seed["unit_count"], len(seed["units"]))
        self.assertTrue(any(d["dispatch_id"] == 1 for d in dc.list_draft_dispatches()))
        # Los 9 despachos del seed deben seguir presentes; pueden existir otros válidos de uso manual
        persistent_ids = {d["dispatch_id"] for d in dc.list_dispatches()
                          if not d["incident_code"].startswith("TIN-")}
        self.assertTrue(set(range(1, 10)).issubset(persistent_ids))


if __name__ == "__main__":
    unittest.main()

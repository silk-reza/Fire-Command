import inspect
import unittest
from unittest import mock

import mysql.connector

from controllers import access_controller
from controllers import dispatch_controller as dc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from utils import session
from utils.permissions import PERMISSION_OPERATE_DISPATCHES

DEMO_PASSWORD = "FireDemo2026!"
STATE_COLUMN = 3  # id_estado_unidad dentro de SELECT * FROM unidades


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class DispatchActivationTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self._n = 0
        session.end_session()

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def make_draft(self, unit_count=1, incident_state="REPORTADO", station_ids=None):
        self._n += 1
        incident_id = self.insert_incident(f"A{self._n}", incident_state)
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        units = []
        for index in range(unit_count):
            station = station_ids[index] if station_ids else None
            unit_id = self.temp_unit(station_id=station)
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        return incident_id, dispatch_id, units

    def fingerprint(self, incident_id, dispatch_id, units):
        return (self.dispatch_row(dispatch_id), self.preselections(dispatch_id),
                [self.unit_row(u) for u in units], self.row(incident_id),
                self.table_count("bitacora"))

    def assert_untouched(self, before, incident_id, dispatch_id, units):
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, units), before)
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")

    def unit_without_state(self, unit_id):
        row = self.unit_row(unit_id)[0]
        return row[:STATE_COLUMN] + row[STATE_COLUMN + 1:-1]

    def test_signatures_accept_no_forcing_parameters(self):
        self.assertEqual(list(inspect.signature(dm.activate_dispatch).parameters),
                         ["dispatch_id", "user_id"])
        self.assertEqual(list(inspect.signature(dc.activate_dispatch).parameters),
                         ["dispatch_id"])

    # 1
    def test_normal_activation_single_moment(self):
        station = self.temp_station()
        incident_id, dispatch_id, units = self.make_draft(2, station_ids=[None, station])
        others_before = [self.unit_without_state(u) for u in units]
        self.assertTrue(dm.activate_dispatch(dispatch_id, self.admin_id))
        dispatch = self.dispatch_row(dispatch_id)[0]
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        moment = dispatch[4]
        self.assertIsNotNone(moment)
        self.assertIsNone(dispatch[5])
        self.assertIsNone(dispatch[6])
        incident = db_all("SELECT fecha_asignacion FROM incidentes WHERE id_incidente = %s",
                          (incident_id,))[0][0]
        self.assertEqual(incident, moment)
        for unit_id, before in zip(units, others_before):
            du = db_all(
                "SELECT fecha_asignacion, id_estacion_origen, fecha_salida, "
                "fecha_llegada_escena, fecha_liberacion, fecha_cancelacion, "
                "motivo_cancelacion FROM despacho_unidades "
                "WHERE id_despacho = %s AND id_unidad = %s", (dispatch_id, unit_id))[0]
            current_station = db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s",
                                     (unit_id,))[0][0]
            self.assertEqual(du[0], moment)
            self.assertEqual(du[1], current_station)
            self.assertEqual(du[2:], (None,) * 5)
            self.assertEqual(self.unit_state(unit_id), "DESPACHADA")
            self.assertEqual(self.unit_without_state(unit_id), before)
        self.assertNotEqual(
            db_all("SELECT id_estacion_origen FROM despacho_unidades WHERE id_despacho = %s "
                   "AND id_unidad = %s", (dispatch_id, units[1]))[0][0], self.seed_station)
        audit = self.dispatch_audit(dispatch_id)
        self.assertEqual([a[1] for a in audit], ["CREAR", "CAMBIO_ESTADO"])
        self.assertEqual(audit[1][4], f"CREADO → ACTIVO para {self.prefix}-A1.")
        self.assertEqual(self.audit_rows(incident_id), [(
            self.admin_id, "CAMBIO_ESTADO", "INCIDENTE", incident_id,
            f"REPORTADO → ASIGNADO por activación del despacho #{dispatch_id}.")])
        # Tras activar, el borrador deja de ser editable
        with self.assertRaises(dm.DispatchStateError):
            dm.update_dispatch_draft(dispatch_id, "x")
        with self.assertRaises(dm.DispatchStateError):
            dm.add_unit_to_draft(dispatch_id, self.temp_unit())
        with self.assertRaises(dm.DispatchStateError):
            dm.remove_unit_from_draft(dispatch_id, units[0])
        with self.assertRaises(dm.DispatchStateError):
            dm.cancel_dispatch_draft(dispatch_id, "Motivo", self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")

    def test_missing_dispatch(self):
        self.assertFalse(dm.activate_dispatch(999999, self.admin_id))
        for bad in (True, "1", None):
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.activate_dispatch(bad, self.admin_id)

    # 2
    def test_reported_incident_becomes_assigned(self):
        incident_id, dispatch_id, units = self.make_draft()
        dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        moment = self.dispatch_row(dispatch_id)[0][4]
        self.assertEqual(db_all("SELECT fecha_asignacion FROM incidentes "
                                "WHERE id_incidente = %s", (incident_id,))[0][0], moment)

    def test_existing_assignment_date_is_kept(self):
        incident_id, dispatch_id, units = self.make_draft()
        db_exec("UPDATE incidentes SET fecha_asignacion = DATE_ADD(fecha_reporte, "
                "INTERVAL 1 MINUTE) WHERE id_incidente = %s", (incident_id,))
        kept = db_all("SELECT fecha_asignacion FROM incidentes WHERE id_incidente = %s",
                      (incident_id,))[0][0]
        dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        self.assertEqual(db_all("SELECT fecha_asignacion FROM incidentes "
                                "WHERE id_incidente = %s", (incident_id,))[0][0], kept)

    # 3
    def test_reinforcement_keeps_incident(self):
        for state in ("ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            incident_id, dispatch_id, units = self.make_draft(incident_state=state)
            incident_before = self.row(incident_id)
            audit_before = self.audit_rows(incident_id)
            dm.activate_dispatch(dispatch_id, self.admin_id)
            self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
            self.assertEqual(self.unit_state(units[0]), "DESPACHADA")
            self.assertEqual(self.row(incident_id), incident_before, state)
            self.assertEqual(self.audit_rows(incident_id), audit_before)

    # 4
    def test_empty_draft_rejected(self):
        incident_id, dispatch_id, units = self.make_draft(0)
        before = self.fingerprint(incident_id, dispatch_id, units)
        with self.assertRaises(dm.DispatchHasNoUnitsError):
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assert_untouched(before, incident_id, dispatch_id, units)
        with self.assertRaises(dc.DispatchHasNoUnitsError):
            session.start_session(authenticate("admin.demo", DEMO_PASSWORD))
            dc.activate_dispatch(dispatch_id)
        self.assertTrue(issubclass(dc.DispatchHasNoUnitsError, dc.DispatchValidationError))

    # 5
    def test_unit_no_longer_available_rejects_everything(self):
        incident_id, dispatch_id, units = self.make_draft(2)
        self.set_unit_state(units[0], "MANTENIMIENTO")
        before = self.fingerprint(incident_id, dispatch_id, units)
        with self.assertRaises(dm.UnitNotEligibleError) as ctx:
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertIn(f"{self.unit_prefix}-1", str(ctx.exception))
        self.assert_untouched(before, incident_id, dispatch_id, units)
        self.assertEqual(self.unit_state(units[1]), "DISPONIBLE")
        self.assertEqual(self.state_of(incident_id), "REPORTADO")

    # 6
    def test_active_participation_elsewhere_rejected(self):
        incident_id, dispatch_id, units = self.make_draft()
        other = self.add_dispatch(self.insert_incident("OTRO", "ASIGNADO"), "ACTIVO")
        self.add_assigned_participation(other, units[0])
        self.assertEqual(self.unit_state(units[0]), "DISPONIBLE")
        before = self.fingerprint(incident_id, dispatch_id, units)
        with self.assertRaises(dm.UnitNotEligibleError) as ctx:
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertIn("participación activa", str(ctx.exception))
        self.assert_untouched(before, incident_id, dispatch_id, units)

    # 7
    def test_one_invalid_unit_rolls_back_all(self):
        variants = ("estado", "inactiva", "estacion")
        for variant in variants:
            station = self.temp_station()
            incident_id, dispatch_id, units = self.make_draft(
                3, station_ids=[None, station, None])
            bad = units[1]
            if variant == "estado":
                self.set_unit_state(bad, "EN_RUTA")
            elif variant == "inactiva":
                db_exec("UPDATE unidades SET activo = 0 WHERE id_unidad = %s", (bad,))
            else:
                db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station,))
            before = self.fingerprint(incident_id, dispatch_id, units)
            with self.assertRaises(dm.UnitNotEligibleError) as ctx:
                dm.activate_dispatch(dispatch_id, self.admin_id)
            self.assertIn(f"{self.unit_prefix}-{self.unit_index(bad)}", str(ctx.exception))
            self.assert_untouched(before, incident_id, dispatch_id, units)
            self.assertEqual(self.state_of(incident_id), "REPORTADO")
            for good in (units[0], units[2]):
                self.assertEqual(self.unit_state(good), "DISPONIBLE")

    def unit_index(self, unit_id):
        code = db_all("SELECT codigo FROM unidades WHERE id_unidad = %s", (unit_id,))[0][0]
        return code.rsplit("-", 1)[1]

    # 8
    def test_terminal_incident_rejected(self):
        for terminal in ("CERRADO", "CANCELADO"):
            incident_id, dispatch_id, units = self.make_draft()
            self.set_state(incident_id, terminal)
            before = self.fingerprint(incident_id, dispatch_id, units)
            with self.assertRaises(dm.IncidentTerminalError):
                dm.activate_dispatch(dispatch_id, self.admin_id)
            self.assert_untouched(before, incident_id, dispatch_id, units)
            self.assertEqual(self.unit_state(units[0]), "DISPONIBLE")

    # 9
    def test_activating_twice_rejected(self):
        incident_id, dispatch_id, units = self.make_draft()
        dm.activate_dispatch(dispatch_id, self.admin_id)
        before = self.fingerprint(incident_id, dispatch_id, units)
        with self.assertRaises(dm.DispatchStateError) as ctx:
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertEqual(str(ctx.exception), "Solo se puede activar un despacho en estado Creado.")
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, units), before)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")

    def test_non_draft_states_rejected(self):
        for state in ("COMPLETADO", "CANCELADO"):
            dispatch_id = self.add_dispatch(self.insert_incident(f"S{state}"), state)
            before = self.dispatch_row(dispatch_id)
            with self.assertRaises(dm.DispatchStateError):
                dm.activate_dispatch(dispatch_id, self.admin_id)
            self.assertEqual(self.dispatch_row(dispatch_id), before)

    # 10
    def test_two_drafts_sharing_a_unit(self):
        incident_a, draft_a, units = self.make_draft()
        incident_b = self.insert_incident("SHARE")
        draft_b = dm.create_dispatch_draft(incident_b, None, self.admin_id)
        dm.add_unit_to_draft(draft_b, units[0])
        dm.activate_dispatch(draft_a, self.admin_id)
        before = self.fingerprint(incident_b, draft_b, units)
        # La unidad ya quedó DESPACHADA al activar A
        before = before[:2] + ([self.unit_row(units[0])],) + before[3:]
        with self.assertRaises(dm.UnitNotEligibleError):
            dm.activate_dispatch(draft_b, self.admin_id)
        self.assertEqual(self.dispatch_state(draft_b), "CREADO")
        self.assertEqual(self.dispatch_state(draft_a), "ACTIVO")
        self.assertEqual(self.fingerprint(incident_b, draft_b, units), before)
        self.assertEqual(len(self.preselections(draft_b)), 1)
        self.assertIsNone(self.preselections(draft_b)[0][4])
        self.assertEqual(self.state_of(incident_b), "REPORTADO")

    # 11
    def test_authorization(self):
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            incident_id, dispatch_id, units = self.make_draft()
            with mock.patch.object(access_controller, "require",
                                   wraps=access_controller.require) as spy:
                result = dc.activate_dispatch(dispatch_id)
            self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_OPERATE_DISPATCHES))
            self.assertEqual(result["state_code"], "ACTIVO")
            self.assertEqual(result["incident_state_code"], "ASIGNADO")
            self.assertIsNotNone(result["dispatched_at"])
            self.assertEqual([u["state_code"] for u in result["units"]], ["DESPACHADA"])
            self.assertFalse(result["units"][0]["eligible_now"])
            self.assertEqual(self.dispatch_audit(dispatch_id)[-1][0], self.user_id(username))
        incident_id, dispatch_id, units = self.make_draft()
        before = self.fingerprint(incident_id, dispatch_id, units)
        session.end_session()
        _login("consulta.sur")
        with self.assertRaises(AuthorizationDenied):
            dc.activate_dispatch(dispatch_id)
        self.assert_untouched(before, incident_id, dispatch_id, units)
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            dc.activate_dispatch(dispatch_id)
        self.assert_untouched(before, incident_id, dispatch_id, units)
        _login("admin.demo")
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.activate_dispatch(999999)
        for bad in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.activate_dispatch(bad)

    def test_controller_error_mapping(self):
        _login("admin.demo")
        incident_id, dispatch_id, units = self.make_draft(2)
        self.set_unit_state(units[1], "MANTENIMIENTO")
        with self.assertRaises(dc.UnitNotEligibleError):
            dc.activate_dispatch(dispatch_id)
        self.set_unit_state(units[1], "DISPONIBLE")
        self.set_state(incident_id, "CERRADO")
        with self.assertRaises(dc.IncidentTerminalError):
            dc.activate_dispatch(dispatch_id)
        self.set_state(incident_id, "REPORTADO")
        dc.activate_dispatch(dispatch_id)
        with self.assertRaises(dc.DispatchStateError):
            dc.activate_dispatch(dispatch_id)

    # 12
    def test_forced_audit_failure_rolls_back_everything(self):
        incident_id, dispatch_id, units = self.make_draft(2)
        before = self.fingerprint(incident_id, dispatch_id, units)
        for target in ("_audit", "_audit_incident"):
            with mock.patch.object(dm, target, side_effect=mysql.connector.Error("fallo")):
                with self.assertRaises(mysql.connector.Error):
                    dm.activate_dispatch(dispatch_id, self.admin_id)
            self.assert_untouched(before, incident_id, dispatch_id, units)
        with self.assertRaises(dm.InvalidReferenceError):
            dm.activate_dispatch(dispatch_id, 999999999)
        self.assert_untouched(before, incident_id, dispatch_id, units)
        for unit_id in units:
            self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.state_of(incident_id), "REPORTADO")
        _login("admin.demo")
        with mock.patch.object(dm, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(dc.DispatchServiceError):
                dc.activate_dispatch(dispatch_id)
        self.assert_untouched(before, incident_id, dispatch_id, units)

    # Aislamiento: solo la activación corre en READ COMMITTED
    def test_only_activation_uses_read_committed(self):
        incident_id, dispatch_id, units = self.make_draft()
        # @@transaction_isolation no refleja SET TRANSACTION de un solo uso, así que se
        # comprueba el efecto: tras una lectura, un cambio confirmado por otra conexión
        # solo es visible dentro de la transacción en READ COMMITTED
        def visible_after_outside_commit(isolation, marker):
            def operation(cursor):
                cursor.execute("SELECT observaciones FROM despachos WHERE id_despacho = %s",
                               (dispatch_id,))
                cursor.fetchone()
                db_exec("UPDATE despachos SET observaciones = %s WHERE id_despacho = %s",
                        (marker, dispatch_id))
                cursor.execute("SELECT observaciones FROM despachos WHERE id_despacho = %s",
                               (dispatch_id,))
                return cursor.fetchone()["observaciones"]
            return dm._write(operation, isolation) if isolation else dm._write(operation)

        self.assertEqual(visible_after_outside_commit("READ COMMITTED", "rc"), "rc")
        self.assertNotEqual(visible_after_outside_commit(None, "rr"), "rr")
        # La conexión siguiente vuelve al nivel por defecto
        self.assertEqual(db_all("SELECT @@transaction_isolation")[0][0], "REPEATABLE-READ")
        # activate_dispatch pide READ COMMITTED y las demás escrituras no
        requested = []
        original = dm._write

        def spy(operation, isolation=None):
            requested.append(isolation)
            return original(operation, isolation)

        with mock.patch.object(dm, "_write", spy):
            dm.add_unit_to_draft(dispatch_id, self.temp_unit())
            dm.update_dispatch_draft(dispatch_id, "x")
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertEqual(requested, [None, None, "READ COMMITTED"])

    def test_now_is_read_once(self):
        incident_id, dispatch_id, units = self.make_draft(2)
        executed = []

        class Spy:
            def __init__(self, cursor):
                self._cursor = cursor

            def execute(self, query, params=()):
                executed.append(query)
                return self._cursor.execute(query, params)

            def __getattr__(self, name):
                return getattr(self._cursor, name)

        original = dm._write
        with mock.patch.object(
                dm, "_write",
                lambda op, isolation=None: original(lambda cur: op(Spy(cur)), isolation)):
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assertEqual(sum("NOW()" in q for q in executed), 1)

    # Fechas incoherentes del incidente
    def test_incident_dates_check_rolls_back(self):
        incident_id, dispatch_id, units = self.make_draft(2)
        db_exec("UPDATE incidentes SET fecha_reporte = DATE_ADD(NOW(), INTERVAL 1 HOUR), "
                "fecha_asignacion = NULL WHERE id_incidente = %s", (incident_id,))
        before = self.fingerprint(incident_id, dispatch_id, units)
        with self.assertRaises(dm.IncidentDatesError):
            dm.activate_dispatch(dispatch_id, self.admin_id)
        self.assert_untouched(before, incident_id, dispatch_id, units)
        for unit_id in units:
            self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        session.start_session(authenticate("admin.demo", DEMO_PASSWORD))
        with self.assertRaises(dc.DispatchValidationError):
            dc.activate_dispatch(dispatch_id)
        self.assert_untouched(before, incident_id, dispatch_id, units)

    # Activación frente a add_unit de otro borrador que comparte la unidad
    def test_activation_vs_add_unit_has_no_deadlock(self):
        import threading
        for round_number in range(10):
            incident_a, draft_a, units = self.make_draft(2)
            incident_b = self.insert_incident(f"RB{round_number}")
            draft_b = dm.create_dispatch_draft(incident_b, None, self.admin_id)
            shared = units[0]
            barrier = threading.Barrier(2)
            outcome = {}

            def activate():
                barrier.wait()
                try:
                    outcome["activate"] = dm.activate_dispatch(draft_a, self.admin_id)
                except (dm.UnitNotEligibleError, dm.DispatchStateError) as error:
                    outcome["activate"] = error
                except mysql.connector.Error as error:
                    outcome["activate"] = error

            def add():
                barrier.wait()
                try:
                    outcome["add"] = dm.add_unit_to_draft(draft_b, shared)
                except dm.UnitNotEligibleError as error:
                    outcome["add"] = error
                except mysql.connector.Error as error:
                    outcome["add"] = error

            workers = [threading.Thread(target=activate), threading.Thread(target=add)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(60)
                self.assertFalse(worker.is_alive())
            for result in outcome.values():
                if isinstance(result, mysql.connector.Error):
                    self.assertNotEqual(result.errno, 1213, f"deadlock en ronda {round_number}")
                    self.fail(f"error inesperado: {result}")
            # La activación solo puede ganar; add es exitoso o rechazado por no elegible
            self.assertIs(outcome["activate"], True)
            self.assertEqual(self.dispatch_state(draft_a), "ACTIVO")
            self.assertEqual(self.unit_state(shared), "DESPACHADA")
            self.assertEqual(db_all(
                "SELECT COUNT(*) FROM despacho_unidades WHERE id_unidad = %s "
                "AND fecha_asignacion IS NOT NULL", (shared,))[0][0], 1)
            self.assertEqual(self.dispatch_state(draft_b), "CREADO")

    def test_seed_untouched(self):
        # El seed (despacho 1 CREADO, INC-2026-001 REPORTADO) no se activa en ningún test
        self.assertEqual(db_all(
            "SELECT e.codigo FROM despachos d JOIN estados_despacho e "
            "ON e.id_estado_despacho = d.id_estado_despacho WHERE d.id_despacho = 1")[0][0],
            "CREADO")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest import mock

import mysql.connector

from controllers import dispatch_controller as dc
from controllers import incident_controller as ic
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from models import incident_model as im
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from utils import session

DEMO_PASSWORD = "FireDemo2026!"


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class DispatchCancellationTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self._n = 0
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def new_incident(self, state="REPORTADO"):
        self._n += 1
        return self.insert_incident(f"X{self._n}", state)

    def activate_on(self, incident_id, unit_count=1, steps=None):
        """Borrador con unidades activado; steps[i] = pasos operativos de la unidad i."""
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        units = []
        for _ in range(unit_count):
            unit_id = self.temp_unit()
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        operations = (dm.mark_unit_en_route, dm.mark_unit_on_scene, dm.start_unit_return)
        for index, unit_id in enumerate(units):
            for operation in operations[:(steps or [0] * unit_count)[index]]:
                operation(dispatch_id, unit_id, self.admin_id)
        return dispatch_id, units

    def make_active(self, unit_count=1, steps=None):
        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, unit_count, steps)
        return incident_id, dispatch_id, units

    def participation(self, dispatch_id, unit_id):
        return db_all(
            "SELECT fecha_asignacion, fecha_salida, fecha_llegada_escena, fecha_liberacion, "
            "fecha_cancelacion, motivo_cancelacion FROM despacho_unidades "
            "WHERE id_despacho = %s AND id_unidad = %s", (dispatch_id, unit_id))[0]

    def fingerprint(self, incident_id):
        dispatches = self.dispatch_rows(incident_id)
        ids = [d[0] for d in dispatches]
        parts = [p for d in ids for p in self.preselections(d)]
        units = [self.unit_row(p[2]) for p in parts]
        return (self.row(incident_id), dispatches, parts, units, self.table_count("bitacora"))

    # 1
    def test_cancel_active_by_unit_state(self):
        expected = {0: "DISPONIBLE", 1: "REGRESANDO", 2: "REGRESANDO"}
        texts = {0: "DESPACHADA → DISPONIBLE", 1: "EN_RUTA → REGRESANDO",
                 2: "EN_ESCENA → REGRESANDO"}
        for steps, new_state in expected.items():
            incident_id, dispatch_id, (unit_id,) = self.make_active(1, [steps])
            incident_before = self.row(incident_id)
            reason = "m" * 250
            result = dc.cancel_active_dispatch(dispatch_id, f"  {reason}  ")
            self.assertEqual(result["state_code"], "CANCELADO")
            self.assertEqual(result["cancel_reason"], reason)
            self.assertIsNotNone(result["dispatched_at"])
            self.assertIsNone(result["finished_at"])
            moment = result["cancelled_at"]
            self.assertIsNotNone(moment)
            part = self.participation(dispatch_id, unit_id)
            self.assertEqual(part[4], moment)
            self.assertEqual(part[5], reason)
            self.assertIsNone(part[3])
            self.assertEqual(self.active_participation_count(unit_id), 0)
            self.assertEqual(self.unit_state(unit_id), new_state)
            self.assertEqual(self.row(incident_id), incident_before)
            self.assertEqual(self.unit_audit(unit_id)[-1][4],
                             f"{texts[steps]} por cancelación del despacho #{dispatch_id}.")
            self.assertEqual(self.dispatch_audit(dispatch_id)[-1][1:],
                             ("CAMBIO_ESTADO", "DESPACHO", dispatch_id,
                              f"ACTIVO → CANCELADO. Motivo: {reason}"))

    def unit_audit(self, unit_id):
        return db_all(
            "SELECT id_usuario, accion, entidad, id_entidad, detalle FROM bitacora "
            "WHERE entidad = 'UNIDAD' AND id_entidad = %s ORDER BY id_bitacora", (unit_id,))

    # 2
    def test_available_after_cancellation(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active(1, [1])
        dc.cancel_active_dispatch(dispatch_id, "Motivo")
        part_before = self.participation(dispatch_id, unit_id)
        dispatch_before = self.dispatch_row(dispatch_id)
        incident_before = self.row(incident_id)
        self.assertEqual(self.unit_state(unit_id), "REGRESANDO")
        dc.mark_unit_available(dispatch_id, unit_id)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.participation(dispatch_id, unit_id), part_before)
        self.assertEqual(self.dispatch_row(dispatch_id), dispatch_before)
        self.assertEqual(self.row(incident_id), incident_before)
        # Preselección de un borrador CREADO: nunca operable
        draft_incident = self.new_incident()
        draft = dm.create_dispatch_draft(draft_incident, None, self.admin_id)
        other = self.temp_unit()
        dm.add_unit_to_draft(draft, other)
        self.set_unit_state(other, "REGRESANDO")
        with self.assertRaises(dc.DispatchStateError):
            dc.mark_unit_available(draft, other)
        self.assertEqual(self.unit_state(other), "REGRESANDO")
        # Participación aún activa (ni liberada ni cancelada): rechazada
        incident_b, dispatch_b, (unit_b,) = self.make_active(1, [2])
        self.set_unit_state(unit_b, "REGRESANDO")
        with self.assertRaises(dc.NoActiveParticipationError):
            dc.mark_unit_available(dispatch_b, unit_b)
        self.assertEqual(self.unit_state(unit_b), "REGRESANDO")

    # 3
    def test_several_participations_each_by_state(self):
        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, 4, [0, 1, 2, 3])
        other_dispatch, (other_unit,) = self.activate_on(self.new_incident(), 1, [1])
        released_part = self.participation(dispatch_id, units[3])
        other_before = (self.dispatch_row(other_dispatch), self.unit_row(other_unit),
                        self.participation(other_dispatch, other_unit))
        dm.cancel_active_dispatch(dispatch_id, "Varias", self.admin_id)
        self.assertEqual([self.unit_state(u) for u in units],
                         ["DISPONIBLE", "REGRESANDO", "REGRESANDO", "REGRESANDO"])
        moment = self.dispatch_row(dispatch_id)[0][6]
        for unit_id in units[:3]:
            part = self.participation(dispatch_id, unit_id)
            self.assertEqual((part[4], part[5], part[3]), (moment, "Varias", None))
        # La ya liberada no se cancela ni cambia
        self.assertEqual(self.participation(dispatch_id, units[3]), released_part)
        self.assertEqual(self.dispatch_state(dispatch_id), "CANCELADO")
        self.assertEqual((self.dispatch_row(other_dispatch), self.unit_row(other_unit),
                          self.participation(other_dispatch, other_unit)), other_before)

    # 4
    def test_reason_validation(self):
        incident_id, dispatch_id, units = self.make_active()
        before = self.fingerprint(incident_id)
        for bad in (None, "", "   ", 5, ["x"], "x" * 251):
            with self.assertRaises(dc.DispatchValidationError):
                dc.cancel_active_dispatch(dispatch_id, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.cancel_active_dispatch(dispatch_id, bad, self.admin_id)
        for bad_id in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.cancel_active_dispatch(bad_id, "Motivo")
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.cancel_active_dispatch(bad_id, "Motivo", self.admin_id)
        with self.assertRaises(dm.InvalidDispatchDataError):
            dm.cancel_active_dispatch(dispatch_id, "Motivo", True)
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.cancel_active_dispatch(999999, "Motivo")
        self.assertEqual(self.fingerprint(incident_id), before)

    # 5
    def test_only_active_dispatch_can_be_cancelled_here(self):
        incident_id = self.new_incident()
        draft = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        done_dispatch, (done_unit,) = self.activate_on(self.new_incident(), 1, [3])
        self.assertEqual(self.dispatch_state(done_dispatch), "COMPLETADO")
        cancelled, _ = self.activate_on(self.new_incident(), 1)
        dc.cancel_active_dispatch(cancelled, "Primera")
        for target in (draft, done_dispatch, cancelled):
            before = self.dispatch_row(target)
            with self.assertRaises(dc.DispatchStateError):
                dc.cancel_active_dispatch(target, "Motivo")
            with self.assertRaises(dm.DispatchStateError):
                dm.cancel_active_dispatch(target, "Motivo", self.admin_id)
            self.assertEqual(self.dispatch_row(target), before)
        self.assertEqual(self.dispatch_row(cancelled)[0][7], "Primera")
        # El borrador CREADO sigue cancelándose con su propia acción (OBJ 10)
        self.assertEqual(dc.cancel_dispatch_draft(draft, "Borrador")["state_code"], "CANCELADO")

    # 6, 7
    def test_incident_cancellation_from_assigned_and_in_attention(self):
        for state, steps, expected in (("ASIGNADO", [0, 0], "DISPONIBLE"),
                                       ("EN_ATENCION", [2, 2], "REGRESANDO")):
            incident_id = self.new_incident()
            dispatch_id, units = self.activate_on(incident_id, 2, steps)
            draft = dm.create_dispatch_draft(incident_id, None, self.admin_id)
            self.assertEqual(self.state_of(incident_id), state)
            result = ic.cancel_incident(incident_id, "Cancelación global")
            self.assertEqual(result["state_code"], "CANCELADO")
            self.assertEqual(result["cancel_reason"], "Cancelación global")
            for despacho in (dispatch_id, draft):
                self.assertEqual(self.dispatch_state(despacho), "CANCELADO")
                self.assertEqual(self.dispatch_row(despacho)[0][7], "Cancelación global")
            for unit_id in units:
                self.assertEqual(self.unit_state(unit_id), expected)
                self.assertEqual(self.active_participation_count(unit_id), 0)
                self.assertIsNone(self.participation(dispatch_id, unit_id)[3])
            self.assertEqual(self.audit_rows(incident_id)[-1][4],
                             f"{state} → CANCELADO. Motivo: Cancelación global")
            # Sin despachos CREADO/ACTIVO bajo un incidente cancelado
            self.assertEqual([self.dispatch_state(r[0]) for r in self.dispatch_rows(incident_id)],
                             ["CANCELADO", "CANCELADO"])

    # 8, 9
    def test_incident_states_that_cannot_be_cancelled(self):
        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, 1, [2])
        db_exec("UPDATE incidentes SET fecha_control = DATE_ADD(fecha_inicio_atencion, "
                "INTERVAL 1 SECOND) WHERE id_incidente = %s", (incident_id,))
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        before = self.fingerprint(incident_id)
        with self.assertRaises(ic.IncidentStateError):
            ic.cancel_incident(incident_id, "Motivo")
        with self.assertRaises(im.IncidentStateError):
            im.cancel_incident(incident_id, "Motivo", self.admin_id)
        self.assertEqual(self.fingerprint(incident_id), before)
        for state in ("CONTROLADO", "CERRADO", "CANCELADO"):
            other = self.insert_incident(f"R{state}", state)
            row_before = self.row(other)
            with self.assertRaises(ic.IncidentStateError):
                ic.cancel_incident(other, "Motivo")
            self.assertEqual(self.row(other), row_before)

    # 10
    def test_rollback_on_audit_failure(self):
        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, 2, [1, 2])
        draft = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        before = self.fingerprint(incident_id)
        for module, name in ((im, "_audit"), (dm, "_audit"), (dm, "_audit_unit")):
            with mock.patch.object(module, name, side_effect=mysql.connector.Error("fallo")):
                with self.assertRaises(mysql.connector.Error):
                    im.cancel_incident(incident_id, "Motivo", self.admin_id)
            self.assertEqual(self.fingerprint(incident_id), before, name)
        with self.assertRaises(im.InvalidReferenceError):
            im.cancel_incident(incident_id, "Motivo", 999999999)
        self.assertEqual(self.fingerprint(incident_id), before)
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        for module, name in ((dm, "_audit"), (dm, "_audit_unit")):
            with mock.patch.object(module, name, side_effect=mysql.connector.Error("fallo")):
                with self.assertRaises(dc.DispatchServiceError):
                    dc.cancel_active_dispatch(dispatch_id, "Motivo")
            self.assertEqual(self.fingerprint(incident_id), before, name)
        with self.assertRaises(dm.InvalidReferenceError):
            dm.cancel_active_dispatch(dispatch_id, "Motivo", 999999999)
        self.assertEqual(self.fingerprint(incident_id), before)

    # 11
    def test_incident_with_many_dispatches_shares_one_moment(self):
        incident_id = self.new_incident()
        draft = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        active_a, units_a = self.activate_on(incident_id, 1, [1])
        active_b, units_b = self.activate_on(incident_id, 2, [0, 2])
        done, _ = self.activate_on(incident_id, 1, [3])
        cancelled, _ = self.activate_on(incident_id, 1, [0])
        dm.cancel_active_dispatch(cancelled, "Previa", self.admin_id)
        self.assertEqual(self.dispatch_state(done), "COMPLETADO")
        terminal_before = {d: (self.dispatch_row(d), self.preselections(d))
                           for d in (done, cancelled)}
        ic.cancel_incident(incident_id, "Todo")
        incident_moment = db_all("SELECT fecha_cancelacion FROM incidentes WHERE id_incidente = %s",
                                 (incident_id,))[0][0]
        for despacho in (draft, active_a, active_b):
            row = self.dispatch_row(despacho)[0]
            self.assertEqual(self.dispatch_state(despacho), "CANCELADO")
            self.assertEqual((row[6], row[7]), (incident_moment, "Todo"))
        for despacho, unit_ids in ((active_a, units_a), (active_b, units_b)):
            for unit_id in unit_ids:
                part = self.participation(despacho, unit_id)
                self.assertEqual((part[4], part[5]), (incident_moment, "Todo"))
        for despacho, before in terminal_before.items():
            self.assertEqual((self.dispatch_row(despacho), self.preselections(despacho)), before)
        self.assertEqual([self.unit_state(u) for u in units_b], ["DISPONIBLE", "REGRESANDO"])
        self.assertEqual(self.unit_state(units_a[0]), "REGRESANDO")

    # 12
    def test_authorization(self):
        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, 1, [1])
        before = self.fingerprint(incident_id)
        session.end_session()
        _login("consulta.sur")
        with self.assertRaises(AuthorizationDenied):
            dc.cancel_active_dispatch(dispatch_id, "Motivo")
        with self.assertRaises(AuthorizationDenied):
            ic.cancel_incident(incident_id, "Motivo")
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            dc.cancel_active_dispatch(dispatch_id, "Motivo")
        with self.assertRaises(NotAuthenticated):
            ic.cancel_incident(incident_id, "Motivo")
        self.assertEqual(self.fingerprint(incident_id), before)
        for username in ("operador.central", "admin.demo"):
            session.end_session()
            _login(username)
            incident_id, dispatch_id, units = self.make_active(1, [1])
            incident_before = self.row(incident_id)
            self.assertEqual(dc.cancel_active_dispatch(dispatch_id, "Motivo")["state_code"],
                             "CANCELADO")
            # Cancelar un despacho nunca cancela el incidente
            self.assertEqual(self.row(incident_id), incident_before)
            self.assertEqual(self.dispatch_audit(dispatch_id)[-1][0], self.user_id(username))
            other, other_dispatch, _ = self.make_active(1, [0])
            self.assertEqual(ic.cancel_incident(other, "Motivo")["state_code"], "CANCELADO")

    # Aislamiento: solo activación y cancelaciones de ACTIVO/incidente usan READ COMMITTED
    def test_only_selected_operations_use_read_committed(self):
        requested = []
        originals = {dm: dm._write, im: im._write}

        def spy_for(module):
            def spy(operation, isolation=None):
                requested.append((module.__name__.split(".")[-1], isolation))
                return originals[module](operation, isolation)
            return spy

        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, 1, [1])
        draft_incident = self.new_incident()
        draft = dm.create_dispatch_draft(draft_incident, None, self.admin_id)
        unit_id = self.temp_unit()
        other_incident = self.new_incident()
        self.activate_on(other_incident, 1)
        with mock.patch.object(dm, "_write", spy_for(dm)),                 mock.patch.object(im, "_write", spy_for(im)):
            dm.add_unit_to_draft(draft, unit_id)
            dm.remove_unit_from_draft(draft, unit_id)
            dm.update_dispatch_draft(draft, "x")
            dm.cancel_dispatch_draft(draft, "Borrador", self.admin_id)
            dm.cancel_active_dispatch(dispatch_id, "Activo", self.admin_id)
            im.cancel_incident(other_incident, "Incidente", self.admin_id)
        self.assertEqual(requested, [
            ("dispatch_model", None), ("dispatch_model", None), ("dispatch_model", None),
            ("dispatch_model", None), ("dispatch_model", "READ COMMITTED"),
            ("incident_model", "READ COMMITTED")])
        self.assertEqual(db_all("SELECT @@transaction_isolation")[0][0], "REPEATABLE-READ")

    # Cancelaciones frente a add_unit de un borrador de OTRO incidente (sin 1213)
    def test_cancellations_vs_add_unit_have_no_deadlock(self):
        import threading
        for round_number in range(5):
            incident_id = self.new_incident()
            dispatch_id, (unit_id,) = self.activate_on(incident_id, 1)
            other_draft = dm.create_dispatch_draft(self.new_incident(), None, self.admin_id)
            barrier = threading.Barrier(2)
            outcome = {}

            def add():
                barrier.wait()
                try:
                    outcome["add"] = dm.add_unit_to_draft(other_draft, unit_id)
                except dm.UnitNotEligibleError as error:
                    outcome["add"] = error
                except Exception as error:  # noqa: BLE001
                    outcome["add"] = error

            def cancel():
                barrier.wait()
                try:
                    if round_number % 2:
                        outcome["cancel"] = im.cancel_incident(
                            incident_id, "Concurrente", self.admin_id)
                    else:
                        outcome["cancel"] = dm.cancel_active_dispatch(
                            dispatch_id, "Concurrente", self.admin_id)
                except Exception as error:  # noqa: BLE001
                    outcome["cancel"] = error

            workers = [threading.Thread(target=add), threading.Thread(target=cancel)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(60)
                self.assertFalse(worker.is_alive())
            for result in outcome.values():
                if isinstance(result, mysql.connector.Error):
                    self.assertNotEqual(result.errno, 1213, f"deadlock en ronda {round_number}")
                if isinstance(result, Exception) and not isinstance(
                        result, dm.UnitNotEligibleError):
                    self.fail(f"error inesperado: {result!r}")
            self.assertIs(outcome["cancel"], True)
            self.assertEqual(self.dispatch_state(dispatch_id), "CANCELADO")
            self.assertEqual(self.active_participation_count(unit_id), 0)
            self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
            preselected = len(self.preselections(other_draft))
            self.assertEqual(preselected, 1 if isinstance(outcome["add"], int) else 0)


if __name__ == "__main__":
    unittest.main()

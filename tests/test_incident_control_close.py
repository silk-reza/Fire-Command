import time
import unittest
from pathlib import Path
from unittest import mock

import mysql.connector

from controllers import incident_controller as ic
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from models import incident_model as im
from tests._dispatch_support import DispatchTestBase, db_all
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class IncidentControlCloseTest(DispatchTestBase):
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
        return self.insert_incident(f"C{self._n}", state)

    def activate_on(self, incident_id, unit_count=1, steps=0):
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        units = []
        for _ in range(unit_count):
            unit_id = self.temp_unit()
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        operations = (dm.mark_unit_en_route, dm.mark_unit_on_scene, dm.start_unit_return)
        for unit_id in units:
            for operation in operations[:steps]:
                operation(dispatch_id, unit_id, self.admin_id)
        return dispatch_id, units

    def attention_incident(self):
        """Incidente EN_ATENCION real con un despacho ACTIVO (unidad en escena)."""
        incident_id = self.new_incident()
        dispatch_id, units = self.activate_on(incident_id, 1, 2)
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        return incident_id, dispatch_id, units

    def dates(self, incident_id):
        return db_all(
            "SELECT fecha_asignacion, fecha_inicio_atencion, fecha_control, fecha_cierre, "
            "fecha_cancelacion FROM incidentes WHERE id_incidente = %s", (incident_id,))[0]

    def side_effects(self, incident_id):
        dispatches = self.dispatch_rows(incident_id)
        parts = [p for d in dispatches for p in self.preselections(d[0])]
        return dispatches, parts, [self.unit_row(p[2]) for p in parts]

    # 1, 3
    def test_mark_controlled(self):
        incident_id, dispatch_id, units = self.attention_incident()
        before_dates = self.dates(incident_id)
        effects = self.side_effects(incident_id)
        result = ic.mark_incident_controlled(incident_id)
        self.assertEqual(result["state_code"], "CONTROLADO")
        self.assertIsNotNone(result["controlled_at"])
        self.assertIsNone(result["closed_at"])
        after = self.dates(incident_id)
        self.assertEqual((after[0], after[1], after[3], after[4]),
                         (before_dates[0], before_dates[1], None, None))
        self.assertGreaterEqual(after[2], after[1])
        # Control no exige cerrar despachos: el despacho sigue ACTIVO y nada más cambia
        self.assertEqual(self.side_effects(incident_id), effects)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertEqual(self.audit_rows(incident_id)[-1], (
            self.admin_id, "CAMBIO_ESTADO", "INCIDENTE", incident_id, "EN_ATENCION → CONTROLADO"))

    # 2, terminalidad
    def test_control_rejected_from_other_states(self):
        for state in ("REPORTADO", "ASIGNADO", "CONTROLADO", "CERRADO", "CANCELADO"):
            incident_id = self.new_incident(state)
            before = (self.row(incident_id), self.table_count("bitacora"))
            with self.assertRaises(ic.IncidentStateError, msg=state):
                ic.mark_incident_controlled(incident_id)
            self.assertEqual((self.row(incident_id), self.table_count("bitacora")), before)
        with self.assertRaises(ic.IncidentNotFoundError):
            ic.mark_incident_controlled(999999)
        for bad in (True, "1", None):
            with self.assertRaises(ic.IncidentValidationError):
                ic.mark_incident_controlled(bad)
            with self.assertRaises(im.InvalidIncidentDataError):
                im.mark_incident_controlled(bad, self.admin_id)

    # reactivación genuina y control repetido
    def test_reactivation_and_second_control(self):
        incident_id, dispatch_id, units = self.attention_incident()
        ic.mark_incident_controlled(incident_id)
        first = self.dates(incident_id)
        result = ic.reactivate_incident(incident_id, "  Refuerzo en curso  ")
        self.assertEqual(result["state_code"], "EN_ATENCION")
        again = self.dates(incident_id)
        self.assertEqual(again, first)  # control, inicio y cierre se conservan
        self.assertEqual(self.audit_rows(incident_id)[-1][4],
                         "CONTROLADO → EN_ATENCION (reactivación): Refuerzo en curso")
        time.sleep(1.1)
        ic.mark_incident_controlled(incident_id)
        second = self.dates(incident_id)
        self.assertGreater(second[2], first[2])
        self.assertEqual((second[0], second[1]), (first[0], first[1]))

    def test_reactivation_not_genuine(self):
        # Sin despachos
        empty = self.new_incident("CONTROLADO")
        # Solo CREADO
        drafted = self.new_incident("CONTROLADO")
        dm.create_dispatch_draft(drafted, None, self.admin_id)
        # Solo COMPLETADO y CANCELADO
        finished = self.new_incident()
        done_dispatch, _ = self.activate_on(finished, 1, 3)
        cancelled_dispatch, _ = self.activate_on(finished, 1, 0)
        dm.cancel_active_dispatch(cancelled_dispatch, "Motivo", self.admin_id)
        self.assertEqual(self.dispatch_state(done_dispatch), "COMPLETADO")
        ic.mark_incident_controlled(finished)
        for incident_id in (empty, drafted, finished):
            before = (self.row(incident_id), self.table_count("bitacora"))
            with self.assertRaises(ic.IncidentReactivationError) as ctx:
                ic.reactivate_incident(incident_id, "Motivo")
            self.assertIsInstance(ctx.exception, ic.IncidentStateError)
            self.assertEqual((self.row(incident_id), self.table_count("bitacora")), before)
        # Estados incorrectos y motivo inválido
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CERRADO", "CANCELADO"):
            other = self.new_incident(state)
            with self.assertRaises(ic.IncidentStateError, msg=state):
                ic.reactivate_incident(other, "Motivo")
        before = self.row(finished)
        for bad in (None, "", "   ", 5, "x" * 251):
            with self.assertRaises(ic.IncidentValidationError):
                ic.reactivate_incident(finished, bad)
            with self.assertRaises(im.InvalidIncidentDataError):
                im.reactivate_incident(finished, bad, self.admin_id)
        self.assertEqual(self.row(finished), before)

    # cierre
    def test_close_success_ignores_terminal_dispatches(self):
        incident_id = self.new_incident()
        done_dispatch, (done_unit,) = self.activate_on(incident_id, 1, 3)
        cancelled_dispatch, _ = self.activate_on(incident_id, 1, 0)
        dm.cancel_active_dispatch(cancelled_dispatch, "Motivo", self.admin_id)
        self.assertEqual(self.unit_state(done_unit), "REGRESANDO")
        ic.mark_incident_controlled(incident_id)
        effects = self.side_effects(incident_id)
        result = ic.close_incident(incident_id)
        self.assertEqual(result["state_code"], "CERRADO")
        self.assertIsNotNone(result["closed_at"])
        self.assertGreaterEqual(result["closed_at"], result["controlled_at"])
        self.assertEqual(self.side_effects(incident_id), effects)
        self.assertEqual(self.audit_rows(incident_id)[-1][4], "CONTROLADO → CERRADO")
        # Terminal: ya no se controla, reactiva, cierra ni cancela
        for operation in (lambda: ic.mark_incident_controlled(incident_id),
                          lambda: ic.reactivate_incident(incident_id, "Motivo"),
                          lambda: ic.close_incident(incident_id),
                          lambda: ic.cancel_incident(incident_id, "Motivo")):
            with self.assertRaises(ic.IncidentStateError):
                operation()
        self.assertEqual(self.state_of(incident_id), "CERRADO")

    def test_close_blocked(self):
        # Borrador CREADO
        drafted = self.new_incident("CONTROLADO")
        dm.create_dispatch_draft(drafted, None, self.admin_id)
        # Despacho ACTIVO con participación activa (flujo real)
        busy, busy_dispatch, _ = self.attention_incident()
        ic.mark_incident_controlled(busy)
        # Despacho ACTIVO sin participaciones activas (armado por SQL)
        hollow = self.new_incident("CONTROLADO")
        self.add_dispatch(hollow, "ACTIVO")
        cases = ((drafted, "borrador"), (busy, "participación activa"), (hollow, "activos"))
        for incident_id, word in cases:
            before = (self.row(incident_id), self.dispatch_rows(incident_id),
                      self.table_count("bitacora"))
            with self.assertRaises(ic.IncidentCloseBlockedError) as ctx:
                ic.close_incident(incident_id)
            self.assertIn(word, str(ctx.exception))
            self.assertEqual((self.row(incident_id), self.dispatch_rows(incident_id),
                              self.table_count("bitacora")), before)
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CANCELADO"):
            with self.assertRaises(ic.IncidentStateError, msg=state):
                ic.close_incident(self.new_incident(state))
        # Cancelar sigue rechazado desde CONTROLADO (OBJ 14 intacto)
        with self.assertRaises(ic.IncidentStateError):
            ic.cancel_incident(drafted, "Motivo")

    # rollback
    def test_rollback_on_audit_failure(self):
        controlled_source, _, _ = self.attention_incident()
        reactivable, _, _ = self.attention_incident()
        ic.mark_incident_controlled(reactivable)
        closable = self.new_incident("CONTROLADO")
        cases = (
            (controlled_source, lambda i: im.mark_incident_controlled(i, self.admin_id)),
            (reactivable, lambda i: im.reactivate_incident(i, "Motivo", self.admin_id)),
            (closable, lambda i: im.close_incident(i, self.admin_id)),
        )
        for incident_id, operation in cases:
            before = (self.row(incident_id), self.table_count("bitacora"))
            with mock.patch.object(im, "_audit", side_effect=mysql.connector.Error("fallo")):
                with self.assertRaises(mysql.connector.Error):
                    operation(incident_id)
            self.assertEqual((self.row(incident_id), self.table_count("bitacora")), before)
            with self.assertRaises(im.InvalidReferenceError):
                if operation is cases[0][1]:
                    im.mark_incident_controlled(incident_id, 999999999)
                elif operation is cases[1][1]:
                    im.reactivate_incident(incident_id, "Motivo", 999999999)
                else:
                    im.close_incident(incident_id, 999999999)
            self.assertEqual((self.row(incident_id), self.table_count("bitacora")), before)

    # autorización
    def test_authorization(self):
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            incident_id, dispatch_id, _ = self.attention_incident()
            ic.mark_incident_controlled(incident_id)
            ic.reactivate_incident(incident_id, "Motivo")
            ic.mark_incident_controlled(incident_id)
            self.assertEqual(self.audit_rows(incident_id)[-1][0], self.user_id(username))
        closable = self.new_incident("CONTROLADO")
        ic.close_incident(closable)
        target = self.new_incident("CONTROLADO")
        before = self.row(target)
        session.end_session()
        _login("consulta.sur")
        for operation in (lambda: ic.mark_incident_controlled(target),
                          lambda: ic.reactivate_incident(target, "Motivo"),
                          lambda: ic.close_incident(target),
                          # La autorización se revisa antes que la validación
                          lambda: ic.mark_incident_controlled(True),
                          lambda: ic.reactivate_incident(True, None),
                          lambda: ic.close_incident("x")):
            with self.assertRaises(AuthorizationDenied):
                operation()
        session.end_session()
        for operation in (lambda: ic.mark_incident_controlled(target),
                          lambda: ic.reactivate_incident(target, "Motivo"),
                          lambda: ic.close_incident(target)):
            with self.assertRaises(NotAuthenticated):
                operation()
        self.assertEqual(self.row(target), before)

    # flujo de extremo a extremo
    def test_end_to_end_flow(self):
        incident_id = self.new_incident()
        dispatch_id, (unit_id,) = self.activate_on(incident_id)
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        dm.mark_unit_en_route(dispatch_id, unit_id, self.admin_id)
        dm.mark_unit_on_scene(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
        dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO")
        ic.mark_incident_controlled(incident_id)
        result = ic.close_incident(incident_id)
        self.assertEqual(result["state_code"], "CERRADO")
        for field in ("reported_at", "assigned_at", "attention_started_at", "controlled_at",
                      "closed_at"):
            self.assertIsNotNone(result[field], field)
        self.assertLessEqual(result["controlled_at"], result["closed_at"])

    def test_reactivation_branch_flow(self):
        incident_id = self.new_incident()
        first_dispatch, (first_unit,) = self.activate_on(incident_id, 1, 2)
        ic.mark_incident_controlled(incident_id)
        # Refuerzo: nuevo despacho sobre el incidente CONTROLADO
        reinforcement, (second_unit,) = self.activate_on(incident_id, 1, 0)
        self.assertEqual(self.state_of(incident_id), "CONTROLADO")
        ic.reactivate_incident(incident_id, "Llega refuerzo")
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        ic.mark_incident_controlled(incident_id)
        with self.assertRaises(ic.IncidentCloseBlockedError):
            ic.close_incident(incident_id)
        for dispatch_id, unit_id, done in ((first_dispatch, first_unit, 2),
                                           (reinforcement, second_unit, 0)):
            operations = (dm.mark_unit_en_route, dm.mark_unit_on_scene)[done:]
            for operation in operations:
                operation(dispatch_id, unit_id, self.admin_id)
            dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
            dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.dispatch_state(first_dispatch), "COMPLETADO")
        self.assertEqual(self.dispatch_state(reinforcement), "COMPLETADO")
        self.assertEqual(ic.close_incident(incident_id)["state_code"], "CERRADO")

    # el ciclo de vida solo escribe incidentes
    def test_source_guard_only_updates_incidents(self):
        source = (ROOT / "models" / "incident_model.py").read_text(encoding="utf-8")
        for name in ("mark_incident_controlled", "reactivate_incident", "close_incident",
                     "_guarded_state_update"):
            start = source.index(chr(10) + "def " + name + "(")
            ends = [source.find(chr(10) + marker, start + 1)
                    for marker in ("def ", "class ", "_", "# ")]
            body = source[start:min(e for e in ends if e != -1)] if any(
                e != -1 for e in ends) else source[start:]
            for forbidden in ("UPDATE despachos", "UPDATE unidades", "UPDATE despacho_unidades",
                              "despacho_personal", "DELETE", "INSERT INTO despachos"):
                self.assertNotIn(forbidden, body, name)
        self.assertEqual(source.count("UPDATE incidentes SET id_estado_incidente = %s{extra_sql}"), 1)


if __name__ == "__main__":
    unittest.main()

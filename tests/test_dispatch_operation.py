import unittest
from unittest import mock

import mysql.connector

from config.database import get_connection
from controllers import access_controller
from controllers import dispatch_controller as dc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from models import unit_model
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from utils import session
from utils.permissions import PERMISSION_OPERATE_DISPATCHES

DEMO_PASSWORD = "FireDemo2026!"
OPERATIONS = (
    ("DESPACHADA", dm.mark_unit_en_route),
    ("EN_RUTA", dm.mark_unit_on_scene),
    ("EN_ESCENA", dm.start_unit_return),
    ("REGRESANDO", dm.mark_unit_available),
)


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class DispatchOperationTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self._n = 0
        session.end_session()

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def make_active(self, unit_count=1, incident_state="REPORTADO"):
        """Despacho ACTIVO real: borrador con unidades y activate_dispatch."""
        self._n += 1
        incident_id = self.insert_incident(f"O{self._n}", incident_state)
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        units = []
        for _ in range(unit_count):
            unit_id = self.temp_unit()
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        return incident_id, dispatch_id, units

    def advance(self, dispatch_id, unit_id, steps):
        for _, operation in OPERATIONS[:steps]:
            operation(dispatch_id, unit_id, self.admin_id)

    def participation(self, dispatch_id, unit_id):
        return db_all(
            "SELECT fecha_asignacion, fecha_salida, fecha_llegada_escena, fecha_liberacion, "
            "fecha_cancelacion, id_estacion_origen FROM despacho_unidades "
            "WHERE id_despacho = %s AND id_unidad = %s", (dispatch_id, unit_id))[0]

    def fingerprint(self, incident_id, dispatch_id, units):
        return (self.dispatch_row(dispatch_id), self.preselections(dispatch_id),
                [self.unit_row(u) for u in units], self.row(incident_id),
                self.table_count("bitacora"))

    def unit_audit(self, unit_id):
        return db_all(
            "SELECT id_usuario, accion, entidad, id_entidad, detalle FROM bitacora "
            "WHERE entidad = 'UNIDAD' AND id_entidad = %s ORDER BY id_bitacora", (unit_id,))

    def has_active_participation(self, unit_id):
        connection = get_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            result = unit_model._has_active_participation(cursor, unit_id)
            connection.rollback()
            cursor.close()
            return result
        finally:
            connection.close()

    # 1
    def test_en_route(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        incident_before = self.row(incident_id)
        dispatch_before = self.dispatch_row(dispatch_id)
        self.assertTrue(dm.mark_unit_en_route(dispatch_id, unit_id, self.admin_id))
        part = self.participation(dispatch_id, unit_id)
        self.assertIsNotNone(part[1])
        self.assertGreaterEqual(part[1], part[0])
        self.assertEqual(part[2:5], (None, None, None))
        self.assertEqual(self.unit_state(unit_id), "EN_RUTA")
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(self.dispatch_row(dispatch_id), dispatch_before)
        self.assertEqual(self.unit_audit(unit_id), [(
            self.admin_id, "CAMBIO_ESTADO", "UNIDAD", unit_id,
            f"DESPACHADA → EN_RUTA; salida registrada (despacho #{dispatch_id}).")])

    # 2
    def test_on_scene(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.advance(dispatch_id, unit_id, 1)
        dm.mark_unit_on_scene(dispatch_id, unit_id, self.admin_id)
        part = self.participation(dispatch_id, unit_id)
        self.assertIsNotNone(part[2])
        self.assertGreaterEqual(part[2], part[1])
        self.assertEqual(self.unit_state(unit_id), "EN_ESCENA")
        self.assertEqual(self.unit_audit(unit_id)[-1][4],
                         f"EN_RUTA → EN_ESCENA; llegada registrada (despacho #{dispatch_id}).")

    # 3
    def test_first_arrival_moves_incident(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")
        self.advance(dispatch_id, unit_id, 1)
        dm.mark_unit_on_scene(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        arrived = self.participation(dispatch_id, unit_id)[2]
        started = db_all("SELECT fecha_inicio_atencion FROM incidentes WHERE id_incidente = %s",
                         (incident_id,))[0][0]
        self.assertEqual(started, arrived)
        self.assertEqual(self.audit_rows(incident_id)[-1], (
            self.admin_id, "CAMBIO_ESTADO", "INCIDENTE", incident_id,
            "ASIGNADO → EN_ATENCION por primera llegada a escena."))

    # 4
    def test_later_arrivals_keep_incident(self):
        incident_id, dispatch_id, (first, second) = self.make_active(2)
        for unit_id in (first, second):
            self.advance(dispatch_id, unit_id, 1)
        dm.mark_unit_on_scene(dispatch_id, first, self.admin_id)
        incident_after_first = self.row(incident_id)
        audit_count = len(self.audit_rows(incident_id))
        dm.mark_unit_on_scene(dispatch_id, second, self.admin_id)
        self.assertEqual(self.row(incident_id), incident_after_first)
        self.assertEqual(len(self.audit_rows(incident_id)), audit_count)
        self.assertEqual(self.unit_state(second), "EN_ESCENA")
        # CONTROLADO tampoco se toca
        incident_c, dispatch_c, (unit_c,) = self.make_active()
        self.advance(dispatch_c, unit_c, 1)
        self.set_state(incident_c, "CONTROLADO")
        controlled = self.row(incident_c)
        audit_c = len(self.audit_rows(incident_c))
        dm.mark_unit_on_scene(dispatch_c, unit_c, self.admin_id)
        self.assertEqual(self.row(incident_c), controlled)
        self.assertEqual(len(self.audit_rows(incident_c)), audit_c)
        self.assertEqual(self.unit_state(unit_c), "EN_ESCENA")

    # 5
    def test_start_return_releases_participation(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.advance(dispatch_id, unit_id, 2)
        self.assertTrue(self.has_active_participation(unit_id))
        self.assertEqual(self.active_participation_count(unit_id), 1)
        dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
        part = self.participation(dispatch_id, unit_id)
        self.assertIsNotNone(part[3])
        self.assertGreaterEqual(part[3], part[2])
        self.assertIsNone(part[4])
        self.assertEqual(self.unit_state(unit_id), "REGRESANDO")
        self.assertFalse(self.has_active_participation(unit_id))
        self.assertEqual(self.active_participation_count(unit_id), 0)
        # Era la única unidad: al liberarla el despacho se completa (OBJ 13)
        self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO")
        self.assertEqual(self.unit_audit(unit_id)[-1][4], (
            f"EN_ESCENA → REGRESANDO por liberación normal (despacho #{dispatch_id})."))

    # 6
    def test_available_keeps_everything_else(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.advance(dispatch_id, unit_id, 3)
        part_before = self.participation(dispatch_id, unit_id)
        station_before = db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s",
                                (unit_id,))[0][0]
        incident_before = self.row(incident_id)
        dispatch_before = self.dispatch_row(dispatch_id)
        dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.participation(dispatch_id, unit_id), part_before)
        self.assertEqual(db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s",
                                (unit_id,))[0][0], station_before)
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(self.dispatch_row(dispatch_id), dispatch_before)
        self.assertEqual(self.unit_audit(unit_id)[-1][4],
                         f"REGRESANDO → DISPONIBLE (despacho #{dispatch_id}).")

    # 7
    def test_invalid_jumps_rejected(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        wrong_ops = {
            0: dm.mark_unit_on_scene,       # DESPACHADA -> EN_ESCENA
            1: dm.start_unit_return,        # EN_RUTA -> REGRESANDO
            2: dm.mark_unit_available,      # EN_ESCENA -> DISPONIBLE
            3: dm.mark_unit_en_route,       # REGRESANDO -> EN_RUTA
        }
        for steps, wrong in wrong_ops.items():
            self.advance_from(dispatch_id, unit_id, steps)
            before = self.fingerprint(incident_id, dispatch_id, [unit_id])
            with self.assertRaises(dm.DispatchStateError):
                wrong(dispatch_id, unit_id, self.admin_id)
            self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)

    def advance_from(self, dispatch_id, unit_id, target_steps):
        """Avanza solo los pasos que faltan hasta target_steps."""
        done = {"DESPACHADA": 0, "EN_RUTA": 1, "EN_ESCENA": 2, "REGRESANDO": 3}[
            self.unit_state(unit_id)]
        for _, operation in OPERATIONS[done:target_steps]:
            operation(dispatch_id, unit_id, self.admin_id)

    # 8
    def test_non_active_dispatch_rejected(self):
        # Borrador CREADO: la preselección nunca es operable
        incident = self.insert_incident("DRAFT")
        draft = dm.create_dispatch_draft(incident, None, self.admin_id)
        unit_id = self.temp_unit()
        dm.add_unit_to_draft(draft, unit_id)
        for _, operation in OPERATIONS:
            before = self.fingerprint(incident, draft, [unit_id])
            with self.assertRaises(dm.DispatchStateError):
                operation(draft, unit_id, self.admin_id)
            self.assertEqual(self.fingerprint(incident, draft, [unit_id]), before)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        # COMPLETADO y CANCELADO armados por SQL con participación asignada
        for state in ("COMPLETADO", "CANCELADO"):
            incident = self.insert_incident(f"S{state}", "ASIGNADO")
            dispatch_id = self.add_dispatch(incident, state)
            unit_id = self.temp_unit()
            self.add_assigned_participation(dispatch_id, unit_id)
            self.set_unit_state(unit_id, "DESPACHADA")
            for operation in (dm.mark_unit_en_route, dm.mark_unit_on_scene,
                              dm.start_unit_return):
                before = self.fingerprint(incident, dispatch_id, [unit_id])
                with self.assertRaises(dm.DispatchStateError):
                    operation(dispatch_id, unit_id, self.admin_id)
                self.assertEqual(self.fingerprint(incident, dispatch_id, [unit_id]), before)
        # REGRESANDO -> DISPONIBLE tampoco con despacho CANCELADO
        self.set_unit_state(unit_id, "REGRESANDO")
        with self.assertRaises(dm.DispatchStateError):
            dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.unit_state(unit_id), "REGRESANDO")

    # 9
    def test_available_when_dispatch_completed(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.advance(dispatch_id, unit_id, 3)
        db_exec(
            "UPDATE despachos SET id_estado_despacho = (SELECT id_estado_despacho FROM "
            "estados_despacho WHERE codigo = 'COMPLETADO'), "
            "fecha_finalizacion = DATE_ADD(fecha_despacho, INTERVAL 1 SECOND) "
            "WHERE id_despacho = %s", (dispatch_id,))
        dispatch_before = self.dispatch_row(dispatch_id)
        dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.dispatch_row(dispatch_id), dispatch_before)

    def test_available_requires_released_participation(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.advance(dispatch_id, unit_id, 2)
        # Unidad REGRESANDO por harness pero sin fecha_liberacion: no se libera sola
        self.set_unit_state(unit_id, "REGRESANDO")
        before = self.fingerprint(incident_id, dispatch_id, [unit_id])
        with self.assertRaises(dm.NoActiveParticipationError):
            dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)

    # 10
    def test_authorization_and_membership(self):
        for username in ("operador.central", "admin.demo"):
            session.end_session()
            _login(username)
            incident_id, dispatch_id, (unit_id,) = self.make_active()
            with mock.patch.object(access_controller, "require",
                                   wraps=access_controller.require) as spy:
                result = dc.mark_unit_en_route(dispatch_id, unit_id)
            self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_OPERATE_DISPATCHES))
            self.assertEqual(result["units"][0]["state_code"], "EN_RUTA")
            self.assertIsNotNone(result["units"][0]["departed_at"])
            self.assertIsNotNone(result["units"][0]["assigned_at"])
            self.assertIsNotNone(result["units"][0]["origin_station_code"])
            self.assertEqual(self.unit_audit(unit_id)[-1][0], self.user_id(username))
            for operation in (dc.mark_unit_on_scene, dc.start_unit_return,
                              dc.mark_unit_available):
                result = operation(dispatch_id, unit_id)
            self.assertEqual(result["units"][0]["state_code"], "DISPONIBLE")
            self.assertIsNotNone(result["units"][0]["released_at"])
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        before = self.fingerprint(incident_id, dispatch_id, [unit_id])
        session.end_session()
        _login("consulta.sur")
        for operation in (dc.mark_unit_en_route, dc.mark_unit_on_scene,
                          dc.start_unit_return, dc.mark_unit_available):
            with self.assertRaises(AuthorizationDenied):
                operation(dispatch_id, unit_id)
        session.end_session()
        for operation in (dc.mark_unit_en_route, dc.mark_unit_on_scene,
                          dc.start_unit_return, dc.mark_unit_available):
            with self.assertRaises(NotAuthenticated):
                operation(dispatch_id, unit_id)
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)
        _login("admin.demo")
        stranger = self.temp_unit()
        with self.assertRaises(dc.DispatchUnitNotFoundError):
            dc.mark_unit_en_route(dispatch_id, stranger)
        with self.assertRaises(dc.UnitNotFoundError):
            dc.mark_unit_en_route(dispatch_id, 999999)
        with self.assertRaises(dc.DispatchNotFoundError):
            dc.mark_unit_en_route(999999, unit_id)
        with self.assertRaises(dc.UnitStateError):
            dc.mark_unit_on_scene(dispatch_id, unit_id)
        for bad in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.mark_unit_en_route(bad, unit_id)
            with self.assertRaises(dc.DispatchValidationError):
                dc.mark_unit_en_route(dispatch_id, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.mark_unit_en_route(bad, unit_id, self.admin_id)
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)

    # 11
    def test_forced_audit_failure_rolls_back(self):
        for steps, (_, operation) in enumerate(OPERATIONS):
            incident_id, dispatch_id, (unit_id,) = self.make_active()
            self.advance_from(dispatch_id, unit_id, steps)
            before = self.fingerprint(incident_id, dispatch_id, [unit_id])
            part_before = self.participation(dispatch_id, unit_id)
            with mock.patch.object(dm, "_audit_unit", side_effect=mysql.connector.Error("x")):
                with self.assertRaises(mysql.connector.Error):
                    operation(dispatch_id, unit_id, self.admin_id)
            self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)
            with self.assertRaises(dm.InvalidReferenceError):
                operation(dispatch_id, unit_id, 999999999)
            self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)
            self.assertEqual(self.participation(dispatch_id, unit_id), part_before)
        # En la primera llegada el fallo posterior también revierte el incidente
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        self.advance(dispatch_id, unit_id, 1)
        before = self.fingerprint(incident_id, dispatch_id, [unit_id])
        with mock.patch.object(dm, "_audit_incident", side_effect=mysql.connector.Error("x")):
            with self.assertRaises(mysql.connector.Error):
                dm.mark_unit_on_scene(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)
        self.assertEqual(self.state_of(incident_id), "ASIGNADO")


if __name__ == "__main__":
    unittest.main()

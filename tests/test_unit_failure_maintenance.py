import inspect
import unittest
from unittest import mock

import mysql.connector

from controllers import access_controller
from controllers import dispatch_controller as dc
from controllers import unit_controller as uc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from models import unit_model as um
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from utils import session
from utils.permissions import PERMISSION_MANAGE_RESOURCES, PERMISSION_OPERATE_DISPATCHES

DEMO_PASSWORD = "FireDemo2026!"
COMPLETION_TEXT = "ACTIVO → COMPLETADO: sin participaciones activas."


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class UnitFailureMaintenanceTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self._n = 0
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def new_incident(self):
        self._n += 1
        return self.insert_incident(f"F{self._n}")

    def activate_on(self, incident_id, unit_count=1, steps=None):
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

    def unit_audit(self, unit_id):
        return db_all(
            "SELECT id_usuario, accion, entidad, id_entidad, detalle FROM bitacora "
            "WHERE entidad = 'UNIDAD' AND id_entidad = %s ORDER BY id_bitacora", (unit_id,))

    def completion_audit(self, dispatch_id):
        return [a for a in self.dispatch_audit(dispatch_id) if a[4] == COMPLETION_TEXT]

    def fingerprint(self, incident_id, dispatch_id, units):
        return (self.row(incident_id), self.dispatch_row(dispatch_id),
                self.preselections(dispatch_id), [self.unit_row(u) for u in units],
                self.table_count("bitacora"))

    # 1-4, 7
    def test_failure_from_each_active_state(self):
        for steps, source in ((0, "DESPACHADA"), (1, "EN_RUTA"), (2, "EN_ESCENA")):
            incident_id, dispatch_id, (unit_id, other) = self.make_active(2, [steps, 0])
            incident_before = self.row(incident_id)
            reason = "f" * 250
            result = dc.report_unit_failure(dispatch_id, unit_id, f" {reason} ")
            self.assertEqual(self.unit_state(unit_id), "FUERA_DE_SERVICIO")
            part = self.participation(dispatch_id, unit_id)
            self.assertIsNotNone(part[4])
            self.assertEqual(part[5], reason)
            self.assertIsNone(part[3])
            self.assertEqual(self.active_participation_count(unit_id), 0)
            self.assertEqual(self.row(incident_id), incident_before)
            self.assertEqual(self.active_unit(unit_id), 1)
            self.assertEqual(self.unit_audit(unit_id)[-1][4], (
                f"{source} → FUERA_DE_SERVICIO por falla (despacho #{dispatch_id}): {reason}"))
            states = {u["unit_id"]: u["state_code"] for u in result["units"]}
            self.assertEqual(states[unit_id], "FUERA_DE_SERVICIO")
            self.assertEqual(states[other], "DESPACHADA")

    def active_unit(self, unit_id):
        return db_all("SELECT activo FROM unidades WHERE id_unidad = %s", (unit_id,))[0][0]

    # 5, 6
    def test_dispatch_stays_active_or_completes(self):
        incident_id, dispatch_id, (first, second) = self.make_active(2, [1, 2])
        dm.report_unit_failure(dispatch_id, first, "Motor", self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertIsNone(self.dispatch_row(dispatch_id)[0][5])
        self.assertEqual(self.completion_audit(dispatch_id), [])
        self.assertEqual(self.unit_state(second), "EN_ESCENA")
        dm.report_unit_failure(dispatch_id, second, "Frenos", self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO")
        self.assertEqual(self.dispatch_row(dispatch_id)[0][5],
                         self.participation(dispatch_id, second)[4])
        self.assertEqual(len(self.completion_audit(dispatch_id)), 1)
        self.assertIsNone(self.dispatch_row(dispatch_id)[0][6])
        # Unidad ya liberada + una falla en la última activa: también completa
        incident_b, dispatch_b, (released, failing) = self.make_active(2, [3, 1])
        self.assertEqual(self.dispatch_state(dispatch_b), "ACTIVO")
        dm.report_unit_failure(dispatch_b, failing, "Falla", self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_b), "COMPLETADO")
        self.assertEqual(self.unit_state(released), "REGRESANDO")

    # 8
    def test_failure_rejected_in_other_states(self):
        incident_id, dispatch_id, (released, wrong_state, keep) = self.make_active(3, [3, 1, 1])
        self.set_unit_state(wrong_state, "MANTENIMIENTO")
        for state in ("DISPONIBLE", "MANTENIMIENTO", "FUERA_DE_SERVICIO", "REGRESANDO"):
            self.set_unit_state(wrong_state, state)
            before = self.fingerprint(incident_id, dispatch_id, [released, wrong_state, keep])
            with self.assertRaises(dm.UnitStateError):
                dm.report_unit_failure(dispatch_id, wrong_state, "Falla", self.admin_id)
            self.assertEqual(
                self.fingerprint(incident_id, dispatch_id, [released, wrong_state, keep]), before)
        before = self.fingerprint(incident_id, dispatch_id, [released, wrong_state, keep])
        with self.assertRaises(dm.NoActiveParticipationError):
            dm.report_unit_failure(dispatch_id, released, "Falla", self.admin_id)
        # Unidad de un borrador CREADO y de otro despacho
        draft_incident = self.new_incident()
        draft = dm.create_dispatch_draft(draft_incident, None, self.admin_id)
        draft_unit = self.temp_unit()
        dm.add_unit_to_draft(draft, draft_unit)
        with self.assertRaises(dm.DispatchStateError):
            dm.report_unit_failure(draft, draft_unit, "Falla", self.admin_id)
        other_incident, other_dispatch, (stranger,) = self.make_active(1)
        with self.assertRaises(dm.DispatchUnitNotFoundError):
            dm.report_unit_failure(dispatch_id, stranger, "Falla", self.admin_id)
        with self.assertRaises(dm.DispatchNotFoundError):
            dm.report_unit_failure(999999, stranger, "Falla", self.admin_id)
        self.assertEqual(self.unit_state(stranger), "DESPACHADA")
        self.assertEqual(self.unit_state(draft_unit), "DISPONIBLE")
        # Los borradores y despachos auxiliares añaden bitácora propia: se compara sin ella
        self.assertEqual(
            self.fingerprint(incident_id, dispatch_id, [released, wrong_state, keep])[:4],
            before[:4])

    # 9
    def test_failure_reason_and_ids_validation(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        before = self.fingerprint(incident_id, dispatch_id, [unit_id])
        for bad in (None, "", "   ", 5, ["x"], "x" * 251):
            with self.assertRaises(dc.DispatchValidationError):
                dc.report_unit_failure(dispatch_id, unit_id, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.report_unit_failure(dispatch_id, unit_id, bad, self.admin_id)
        for bad_id in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.report_unit_failure(bad_id, unit_id, "Falla")
            with self.assertRaises(dc.DispatchValidationError):
                dc.report_unit_failure(dispatch_id, bad_id, "Falla")
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.report_unit_failure(dispatch_id, bad_id, "Falla", self.admin_id)
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)

    # 10
    def test_failure_rolls_back_on_audit_failure(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active(1, [2])
        before = self.fingerprint(incident_id, dispatch_id, [unit_id])
        # _audit solo lo usa el cierre automático (última participación)
        for name in ("_audit_unit", "_audit"):
            with mock.patch.object(dm, name, side_effect=mysql.connector.Error("fallo")):
                with self.assertRaises(mysql.connector.Error):
                    dm.report_unit_failure(dispatch_id, unit_id, "Falla", self.admin_id)
            self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before, name)
        with self.assertRaises(dm.InvalidReferenceError):
            dm.report_unit_failure(dispatch_id, unit_id, "Falla", 999999999)
        self.assertEqual(self.fingerprint(incident_id, dispatch_id, [unit_id]), before)
        self.assertEqual(self.unit_state(unit_id), "EN_ESCENA")
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")

    # 11
    def test_send_to_maintenance(self):
        unit_id = self.temp_unit()
        station = db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s", (unit_id,))
        before = self.unit_row(unit_id)
        result = uc.send_unit_to_maintenance(unit_id)
        self.assertEqual(result["state_code"], "MANTENIMIENTO")
        after = self.unit_row(unit_id)[0]
        self.assertEqual(after[1], station[0][0])
        self.assertEqual(self.active_unit(unit_id), 1)
        self.assertEqual(self.unit_audit(unit_id), [(
            self.admin_id, "CAMBIO_ESTADO", "UNIDAD", unit_id, "DISPONIBLE → MANTENIMIENTO.")])
        self.assertNotEqual(before, [after])

    # 12
    def test_maintenance_rejections(self):
        for state in ("DESPACHADA", "EN_RUTA", "EN_ESCENA", "REGRESANDO", "MANTENIMIENTO",
                      "FUERA_DE_SERVICIO"):
            unit_id = self.temp_unit(state)
            before = self.unit_row(unit_id)
            with self.assertRaises(uc.UnitTransitionError, msg=state) as ctx:
                uc.send_unit_to_maintenance(unit_id)
            self.assertIn("Disponible", str(ctx.exception))
            self.assertEqual(self.unit_row(unit_id), before)
        inactive = self.temp_unit(active=False)
        before = self.unit_row(inactive)
        with self.assertRaises(uc.UnitTransitionError):
            uc.send_unit_to_maintenance(inactive)
        self.assertEqual(self.unit_row(inactive), before)
        # Parece DISPONIBLE pero participa en un despacho ACTIVO (armado por SQL)
        busy = self.temp_unit()
        other = self.add_dispatch(self.insert_incident("BUSY", "ASIGNADO"), "ACTIVO")
        self.add_assigned_participation(other, busy)
        before = self.unit_row(busy)
        with self.assertRaises(uc.UnitBusyError):
            uc.send_unit_to_maintenance(busy)
        self.assertEqual(self.unit_row(busy), before)
        with self.assertRaises(uc.UnitNotFoundError):
            uc.send_unit_to_maintenance(999999)
        for bad in (True, "1", None):
            with self.assertRaises(uc.UnitValidationError):
                uc.send_unit_to_maintenance(bad)

    # 13
    def test_return_from_maintenance(self):
        unit_id = self.temp_unit("MANTENIMIENTO")
        before = self.unit_row(unit_id)[0]
        result = uc.return_unit_to_service(unit_id)
        self.assertEqual(result["state_code"], "DISPONIBLE")
        after = self.unit_row(unit_id)[0]
        # Solo cambia el estado (y actualizado_en): estación y activo intactos
        self.assertEqual(after[1:3], before[1:3])
        self.assertEqual(after[4:11], before[4:11])
        self.assertEqual(self.unit_audit(unit_id)[-1][4], "MANTENIMIENTO → DISPONIBLE.")

    # 14, 15
    def test_return_from_out_of_service_and_activo_rules(self):
        unit_id = self.temp_unit()
        uc.deactivate_unit(unit_id)
        self.assertEqual(self.unit_state(unit_id), "FUERA_DE_SERVICIO")
        self.assertEqual(self.active_unit(unit_id), 0)
        before = self.unit_row(unit_id)
        with self.assertRaises(uc.UnitTransitionError) as ctx:
            uc.return_unit_to_service(unit_id)
        self.assertIn("inactiva", str(ctx.exception))
        self.assertEqual(self.unit_row(unit_id), before)
        uc.activate_unit(unit_id)
        self.assertEqual(self.unit_state(unit_id), "FUERA_DE_SERVICIO")
        self.assertEqual(self.active_unit(unit_id), 1)
        result = uc.return_unit_to_service(unit_id)
        self.assertEqual(result["state_code"], "DISPONIBLE")
        self.assertEqual(self.active_unit(unit_id), 1)
        self.assertEqual(self.unit_audit(unit_id)[-1][4], "FUERA_DE_SERVICIO → DISPONIBLE.")
        # Una unidad que ya está disponible o en operación no vuelve "a servicio"
        for state in ("DISPONIBLE", "DESPACHADA", "EN_RUTA", "EN_ESCENA", "REGRESANDO"):
            other = self.temp_unit(state)
            before = self.unit_row(other)
            with self.assertRaises(uc.UnitTransitionError, msg=state):
                uc.return_unit_to_service(other)
            self.assertEqual(self.unit_row(other), before)

    # 16, 17
    def test_return_blocked_by_station_and_participation(self):
        station = self.temp_station()
        unit_id = self.temp_unit("MANTENIMIENTO", station_id=station)
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station,))
        before = self.unit_row(unit_id)
        with self.assertRaises(uc.UnitTransitionError) as ctx:
            uc.return_unit_to_service(unit_id)
        self.assertIn("estación", str(ctx.exception))
        self.assertEqual(self.unit_row(unit_id), before)
        busy = self.temp_unit("MANTENIMIENTO")
        other = self.add_dispatch(self.insert_incident("BUSY2", "ASIGNADO"), "ACTIVO")
        self.add_assigned_participation(other, busy)
        before = self.unit_row(busy)
        with self.assertRaises(uc.UnitBusyError):
            uc.return_unit_to_service(busy)
        self.assertEqual(self.unit_row(busy), before)
        with self.assertRaises(uc.UnitNotFoundError):
            uc.return_unit_to_service(999999)

    # 18-20
    def test_authorization(self):
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            incident_id, dispatch_id, (unit_id,) = self.make_active()
            with mock.patch.object(access_controller, "require",
                                   wraps=access_controller.require) as spy:
                dc.report_unit_failure(dispatch_id, unit_id, "Falla")
            self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_OPERATE_DISPATCHES))
            with mock.patch.object(access_controller, "require",
                                   wraps=access_controller.require) as spy:
                uc.return_unit_to_service(unit_id)
            self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_MANAGE_RESOURCES))
            with mock.patch.object(access_controller, "require",
                                   wraps=access_controller.require) as spy:
                uc.send_unit_to_maintenance(unit_id)
            self.assertEqual(spy.call_args_list[0], mock.call(PERMISSION_MANAGE_RESOURCES))
            self.assertEqual(self.unit_state(unit_id), "MANTENIMIENTO")
            self.assertEqual(self.unit_audit(unit_id)[-1][0], self.user_id(username))
        incident_id, dispatch_id, (active_unit,) = self.make_active()
        free = self.temp_unit()
        parked = self.temp_unit("MANTENIMIENTO")
        before = (self.fingerprint(incident_id, dispatch_id, [active_unit]),
                  self.unit_row(free), self.unit_row(parked))
        operations = (
            lambda: dc.report_unit_failure(dispatch_id, active_unit, "Falla"),
            lambda: uc.send_unit_to_maintenance(free),
            lambda: uc.return_unit_to_service(parked),
        )
        session.end_session()
        _login("consulta.sur")
        for operation in operations:
            with self.assertRaises(AuthorizationDenied):
                operation()
        session.end_session()
        for operation in operations:
            with self.assertRaises(NotAuthenticated):
                operation()
        self.assertEqual(before, (self.fingerprint(incident_id, dispatch_id, [active_unit]),
                                  self.unit_row(free), self.unit_row(parked)))

    # Sin setter genérico de estado
    def test_no_generic_state_setter(self):
        state_names = {"state", "state_code", "state_id", "status", "new_state", "target_state"}
        writers = {
            um: {"set_unit_active", "_apply_service_transition"},
            dm: {"_operate_unit", "activate_dispatch", "_cancel_active_dispatch_in_tx",
                 "report_unit_failure"},
        }
        for module, expected in writers.items():
            found = set()
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ != module.__name__:
                    continue
                source = inspect.getsource(function)
                if "UPDATE unidades" in source and "id_estado_unidad" in source:
                    found.add(name)
                    self.assertFalse(
                        state_names & set(inspect.signature(function).parameters), name)
            self.assertEqual(found, expected, module.__name__)
        # Mantenimiento y retorno a servicio solo delegan en el helper guardado
        for name in ("send_unit_to_maintenance", "return_unit_to_service"):
            self.assertIn("_apply_service_transition(", inspect.getsource(getattr(um, name)))
        for module in (uc, dc):
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ != module.__name__ or name.startswith("_"):
                    continue
                if name.startswith(("filter_", "list_", "search_", "get_")):
                    continue
                self.assertFalse(
                    state_names & set(inspect.signature(function).parameters), name)


if __name__ == "__main__":
    unittest.main()

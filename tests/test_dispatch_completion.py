import threading
import unittest
from unittest import mock

import mysql.connector

from models import dispatch_model as dm
from tests._dispatch_support import DispatchTestBase, db_all

COMPLETION_TEXT = "ACTIVO → COMPLETADO: sin participaciones activas."


class DispatchCompletionTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self._n = 0

    def make_on_scene(self, unit_count=1):
        """Despacho ACTIVO real con todas sus unidades EN_ESCENA."""
        self._n += 1
        incident_id = self.insert_incident(f"K{self._n}")
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        units = []
        for _ in range(unit_count):
            unit_id = self.temp_unit()
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        for unit_id in units:
            dm.mark_unit_en_route(dispatch_id, unit_id, self.admin_id)
            dm.mark_unit_on_scene(dispatch_id, unit_id, self.admin_id)
        return incident_id, dispatch_id, units

    def release_date(self, dispatch_id, unit_id):
        return db_all("SELECT fecha_liberacion FROM despacho_unidades "
                      "WHERE id_despacho = %s AND id_unidad = %s", (dispatch_id, unit_id))[0][0]

    def completion_audit(self, dispatch_id):
        return [a for a in self.dispatch_audit(dispatch_id) if a[4] == COMPLETION_TEXT]

    # 1, 5
    def test_single_unit_completes_dispatch(self):
        incident_id, dispatch_id, (unit_id,) = self.make_on_scene()
        dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
        released = self.release_date(dispatch_id, unit_id)
        self.assertIsNotNone(released)
        dispatch = self.dispatch_row(dispatch_id)[0]
        self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO")
        # fecha_despacho, fecha_finalizacion, fecha_cancelacion, motivo
        self.assertEqual(dispatch[5], released)
        self.assertGreaterEqual(dispatch[5], dispatch[4])
        self.assertIsNone(dispatch[6])
        self.assertIsNone(dispatch[7])

    # 2, 6
    def test_only_last_release_completes(self):
        incident_id, dispatch_id, (first, second) = self.make_on_scene(2)
        dm.start_unit_return(dispatch_id, first, self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertIsNone(self.dispatch_row(dispatch_id)[0][5])
        self.assertEqual(self.completion_audit(dispatch_id), [])
        self.assertEqual(self.active_participation_count(second), 1)
        dm.start_unit_return(dispatch_id, second, self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO")
        self.assertEqual(self.dispatch_row(dispatch_id)[0][5],
                         self.release_date(dispatch_id, second))
        audit = self.completion_audit(dispatch_id)
        self.assertEqual(audit, [(self.admin_id, "CAMBIO_ESTADO", "DESPACHO",
                                  dispatch_id, COMPLETION_TEXT)])

    # 3, 4, 8
    def test_units_incident_and_later_availability(self):
        incident_id, dispatch_id, (unit_id,) = self.make_on_scene()
        incident_before = self.row(incident_id)
        incident_audit = self.audit_rows(incident_id)
        station = db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s",
                         (unit_id,))[0][0]
        dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.unit_state(unit_id), "REGRESANDO")
        self.assertEqual(db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s",
                                (unit_id,))[0][0], station)
        self.assertEqual(self.state_of(incident_id), "EN_ATENCION")
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(self.audit_rows(incident_id), incident_audit)
        completed = self.dispatch_row(dispatch_id)
        audit_total = len(self.dispatch_audit(dispatch_id))
        dm.mark_unit_available(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.dispatch_row(dispatch_id), completed)
        self.assertEqual(len(self.dispatch_audit(dispatch_id)), audit_total)
        self.assertEqual(len(self.completion_audit(dispatch_id)), 1)
        self.assertEqual(self.row(incident_id), incident_before)

    # 7
    def test_completion_audit_failure_rolls_back_everything(self):
        incident_id, dispatch_id, (unit_id,) = self.make_on_scene()
        before = (self.dispatch_row(dispatch_id), self.preselections(dispatch_id),
                  self.unit_row(unit_id), self.row(incident_id), self.table_count("bitacora"))
        with mock.patch.object(dm, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(mysql.connector.Error):
                dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
        self.assertEqual(
            (self.dispatch_row(dispatch_id), self.preselections(dispatch_id),
             self.unit_row(unit_id), self.row(incident_id), self.table_count("bitacora")),
            before)
        self.assertEqual(self.unit_state(unit_id), "EN_ESCENA")
        self.assertIsNone(self.release_date(dispatch_id, unit_id))
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertEqual(self.completion_audit(dispatch_id), [])
        # Con un usuario inválido tampoco queda nada
        with self.assertRaises(dm.InvalidReferenceError):
            dm.start_unit_return(dispatch_id, unit_id, 999999999)
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertEqual(self.unit_state(unit_id), "EN_ESCENA")
        self.assertEqual(self.table_count("bitacora"), before[4])

    # Otras operaciones no completan
    def test_other_operations_never_complete(self):
        incident_id, dispatch_id, (unit_id,) = self.make_on_scene()
        self.assertEqual(self.dispatch_state(dispatch_id), "ACTIVO")
        self.assertEqual(self.completion_audit(dispatch_id), [])

    # 9
    def test_simultaneous_last_releases_complete_exactly_once(self):
        for round_number in range(5):
            incident_id, dispatch_id, units = self.make_on_scene(2)
            barrier = threading.Barrier(2)
            errors = []

            def release(unit_id):
                barrier.wait()
                try:
                    dm.start_unit_return(dispatch_id, unit_id, self.admin_id)
                except Exception as error:  # noqa: BLE001 (se inspecciona abajo)
                    errors.append(error)

            workers = [threading.Thread(target=release, args=(u,)) for u in units]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(60)
                self.assertFalse(worker.is_alive())
            for error in errors:
                if isinstance(error, mysql.connector.Error):
                    self.assertNotEqual(error.errno, 1213, f"deadlock en ronda {round_number}")
                self.fail(f"error inesperado: {error!r}")
            self.assertEqual(self.dispatch_state(dispatch_id), "COMPLETADO", round_number)
            self.assertEqual(len(self.completion_audit(dispatch_id)), 1)
            self.assertEqual(self.active_participation_count(units[0]), 0)
            for unit_id in units:
                self.assertEqual(self.unit_state(unit_id), "REGRESANDO")
                self.assertIsNotNone(self.release_date(dispatch_id, unit_id))
            finished = self.dispatch_row(dispatch_id)[0][5]
            self.assertIn(finished, [self.release_date(dispatch_id, u) for u in units])


if __name__ == "__main__":
    unittest.main()

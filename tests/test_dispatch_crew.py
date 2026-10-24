import re
import unittest
from pathlib import Path
from unittest import mock

import mysql.connector

from controllers import dispatch_controller as dc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import dispatch_model as dm
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from tests.test_dispatch_model import function_body
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent
CREW_KEYS = {
    "crew_id", "unit_id", "dispatch_unit_id", "personnel_id", "code", "first_name",
    "last_name", "full_name", "role", "active", "station_id", "station_code", "notes",
}


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class DispatchCrewTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self.person_prefix = f"TDP-{self.prefix[-8:]}"
        self._n = 0
        self._p = 0
        self.crew_before = db_all("SELECT * FROM despacho_personal ORDER BY id_despacho_personal")
        session.end_session()
        _login("admin.demo")

    def cleanup_steps(self):
        steps = [
            ("DELETE FROM despacho_personal WHERE id_personal IN "
             "(SELECT id_personal FROM personal WHERE codigo LIKE %s)",
             (self.person_prefix + "%",)),
            ("DELETE FROM despacho_personal WHERE id_despacho_unidad IN "
             "(SELECT id_despacho_unidad FROM despacho_unidades WHERE id_unidad IN "
             "(SELECT id_unidad FROM unidades WHERE codigo LIKE %s))",
             (self.unit_prefix + "%",)),
        ]
        steps += super().cleanup_steps()
        steps.append(("DELETE FROM personal WHERE codigo LIKE %s", (self.person_prefix + "%",)))
        return steps

    def tearDown(self):
        session.end_session()
        super().tearDown()
        self.assertEqual(
            db_all("SELECT * FROM despacho_personal ORDER BY id_despacho_personal"),
            self.crew_before)

    def person(self, active=True):
        self._p += 1
        person_id = db_exec(
            "INSERT INTO personal (id_estacion, codigo, nombres, apellidos, cargo, activo) "
            "VALUES (%s, %s, %s, %s, 'Bombero', %s)",
            (self.seed_station, f"{self.person_prefix}-{self._p}", f"Nombre{self._p}",
             f"Apellido{self._p}", 1 if active else 0))
        return person_id

    def activate_on(self, incident_id, unit_count=1, steps=None, capacity=None):
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        units = []
        for _ in range(unit_count):
            unit_id = self.temp_unit()
            db_exec("UPDATE unidades SET capacidad_personal = %s WHERE id_unidad = %s",
                    (capacity, unit_id))
            dm.add_unit_to_draft(dispatch_id, unit_id)
            units.append(unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        operations = (dm.mark_unit_en_route, dm.mark_unit_on_scene, dm.start_unit_return)
        for index, unit_id in enumerate(units):
            for operation in operations[:(steps or [0] * unit_count)[index]]:
                operation(dispatch_id, unit_id, self.admin_id)
        return dispatch_id, units

    def make_active(self, unit_count=1, steps=None, capacity=4):
        self._n += 1
        incident_id = self.insert_incident(f"W{self._n}")
        dispatch_id, units = self.activate_on(incident_id, unit_count, steps, capacity)
        return incident_id, dispatch_id, units

    def links(self, dispatch_id=None):
        return db_all(
            "SELECT dp.id_despacho_personal, dp.id_despacho_unidad, dp.id_personal "
            "FROM despacho_personal dp JOIN personal p ON p.id_personal = dp.id_personal "
            "WHERE p.codigo LIKE %s ORDER BY dp.id_despacho_personal", (self.person_prefix + "%",))

    def crew_audit(self, dispatch_id):
        return [a for a in self.dispatch_audit(dispatch_id) if a[4].startswith("Tripulación")]

    # 1
    def test_assign_several_members(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        people = [self.person() for _ in range(3)]
        for person_id in people:
            crew = dm.assign_crew_member(dispatch_id, unit_id, person_id, self.admin_id)
        self.assertEqual(len(crew), 3)
        self.assertEqual(set(crew[0]), CREW_KEYS)
        self.assertEqual({m["personnel_id"] for m in crew}, set(people))
        self.assertTrue(all(m["unit_id"] == unit_id and m["active"] for m in crew))
        self.assertEqual(crew[0]["full_name"], "Nombre1 Apellido1")
        audit = self.crew_audit(dispatch_id)
        self.assertEqual(len(audit), 3)
        self.assertEqual(audit[0][1:3], ("MODIFICAR", "DESPACHO"))
        self.assertIn(f"asignado {self.person_prefix}-1 a unidad", audit[0][4])
        self.assertIn(f"(despacho #{dispatch_id})", audit[0][4])
        grouped = dc.get_dispatch_crew(dispatch_id)
        self.assertEqual(list(grouped), [unit_id])
        self.assertEqual(len(grouped[unit_id]), 3)
        unit = dc.get_dispatch(dispatch_id)["units"][0]
        self.assertEqual((unit["crew_count"], unit["capacity"]), (3, 4))

    # 2
    def test_assign_rejections(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        person_id = self.person()
        dm.assign_crew_member(dispatch_id, unit_id, person_id, self.admin_id)
        with self.assertRaises(dm.DuplicateCrewMemberError):
            dm.assign_crew_member(dispatch_id, unit_id, person_id, self.admin_id)
        with self.assertRaises(dm.PersonnelInactiveError):
            dm.assign_crew_member(dispatch_id, unit_id, self.person(active=False), self.admin_id)
        with self.assertRaises(dm.PersonnelNotFoundError):
            dm.assign_crew_member(dispatch_id, unit_id, 999999, self.admin_id)
        self.assertEqual(len(self.links()), 1)
        self.assertEqual(len(self.crew_audit(dispatch_id)), 1)

    # busy person
    def test_busy_person_rules(self):
        incident_id, dispatch_id, (unit_a, unit_b) = self.make_active(2)
        other_incident, other_dispatch, (other_unit,) = self.make_active()
        person_id = self.person()
        dm.assign_crew_member(dispatch_id, unit_a, person_id, self.admin_id)
        for target_dispatch, target_unit in ((dispatch_id, unit_b), (other_dispatch, other_unit)):
            with self.assertRaises(dm.PersonnelBusyError):
                dm.assign_crew_member(target_dispatch, target_unit, person_id, self.admin_id)
            with self.assertRaises(dc.PersonnelBusyError):
                dc.assign_crew_member(target_dispatch, target_unit, person_id)
        self.assertEqual(len(self.links()), 1)
        # Participación cancelada: la persona queda libre
        dm.cancel_active_dispatch(dispatch_id, "Cancelado", self.admin_id)
        crew = dm.assign_crew_member(other_dispatch, other_unit, person_id, self.admin_id)
        self.assertEqual(len(crew), 1)
        # Participación liberada: también queda libre
        dm.remove_crew_member(other_dispatch, other_unit, person_id, self.admin_id)
        incident_c, dispatch_c, (unit_c,) = self.make_active(1, [3])
        released_person = self.person()
        # La tripulación se arma antes de salir; se simula con SQL el vínculo histórico
        du_c = db_all("SELECT id_despacho_unidad FROM despacho_unidades WHERE id_despacho = %s",
                      (dispatch_c,))[0][0]
        db_exec("INSERT INTO despacho_personal (id_despacho_unidad, id_personal) VALUES (%s, %s)",
                (du_c, released_person))
        dm.assign_crew_member(other_dispatch, other_unit, released_person, self.admin_id)
        # Candidatos: ocupados e inactivos fuera; los libres y los de participación cerrada dentro
        inactive = self.person(active=False)
        free = self.person()
        ids = [c["personnel_id"] for c in dm.list_available_crew_candidates(
            other_dispatch, other_unit)]
        self.assertIn(free, ids)
        self.assertNotIn(inactive, ids)
        self.assertNotIn(released_person, ids)
        self.assertIn(person_id, ids)

    # 3
    def test_capacity(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active(1, capacity=2)
        first, second, third = self.person(), self.person(), self.person()
        dm.assign_crew_member(dispatch_id, unit_id, first, self.admin_id)
        dm.assign_crew_member(dispatch_id, unit_id, second, self.admin_id)
        with self.assertRaises(dm.CrewCapacityError) as ctx:
            dm.assign_crew_member(dispatch_id, unit_id, third, self.admin_id)
        self.assertIn("capacidad de 2", str(ctx.exception))
        self.assertEqual(len(self.links()), 2)
        incident_b, dispatch_b, (free_unit,) = self.make_active(1, capacity=None)
        for _ in range(6):
            dm.assign_crew_member(dispatch_b, free_unit, self.person(), self.admin_id)
        self.assertEqual(len(dc.get_dispatch_crew(dispatch_b)[free_unit]), 6)

    # 4
    def test_remove_member(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        keep, drop = self.person(), self.person()
        dm.assign_crew_member(dispatch_id, unit_id, keep, self.admin_id)
        dm.assign_crew_member(dispatch_id, unit_id, drop, self.admin_id)
        crew = dc.remove_crew_member(dispatch_id, unit_id, drop)
        self.assertEqual([m["personnel_id"] for m in crew], [keep])
        self.assertEqual(len(self.links()), 1)
        with self.assertRaises(dc.CrewMemberNotFoundError):
            dc.remove_crew_member(dispatch_id, unit_id, drop)
        self.assertIn("retirado", self.crew_audit(dispatch_id)[-1][4])
        # Tras retirar, la persona vuelve a estar libre
        dm.assign_crew_member(dispatch_id, unit_id, drop, self.admin_id)
        self.assertEqual(len(self.links()), 2)

    # 5
    def test_crew_frozen_after_departure(self):
        for steps in (1, 2, 3):
            incident_id, dispatch_id, (unit_id, other) = self.make_active(2, [steps, 0])
            # La tripulación se armó mientras estaba Despachada; se congela al salir
            person_id, extra = self.person(), self.person()
            du_id = db_all("SELECT id_despacho_unidad FROM despacho_unidades "
                           "WHERE id_despacho = %s AND id_unidad = %s", (dispatch_id, unit_id))[0][0]
            db_exec("INSERT INTO despacho_personal (id_despacho_unidad, id_personal) "
                    "VALUES (%s, %s)", (du_id, person_id))
            before = self.links()
            with self.assertRaises(dm.DispatchStateError):
                dm.assign_crew_member(dispatch_id, unit_id, extra, self.admin_id)
            with self.assertRaises(dm.DispatchStateError):
                dm.remove_crew_member(dispatch_id, unit_id, person_id, self.admin_id)
            self.assertEqual(self.links(), before, steps)
            # La otra unidad, todavía Despachada, sigue editable
            dm.assign_crew_member(dispatch_id, other, extra, self.admin_id)
            dm.remove_crew_member(dispatch_id, other, extra, self.admin_id)
            self.assertEqual(self.links(), before)

    # 6
    def test_not_editable_contexts_and_history(self):
        person_id = self.person()
        extra = self.person()
        # Unidad que no pertenece al despacho y despacho inexistente
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        stranger = self.temp_unit()
        with self.assertRaises(dm.DispatchUnitNotFoundError):
            dm.assign_crew_member(dispatch_id, stranger, person_id, self.admin_id)
        with self.assertRaises(dm.DispatchNotFoundError):
            dm.assign_crew_member(999999, unit_id, person_id, self.admin_id)
        with self.assertRaises(dm.UnitNotFoundError):
            dm.assign_crew_member(dispatch_id, 999999, person_id, self.admin_id)
        # Borrador CREADO: la tripulación no se gestiona
        draft = dm.create_dispatch_draft(self.insert_incident("DRF"), None, self.admin_id)
        draft_unit = self.temp_unit()
        dm.add_unit_to_draft(draft, draft_unit)
        with self.assertRaises(dm.DispatchStateError):
            dm.assign_crew_member(draft, draft_unit, person_id, self.admin_id)
        # Historia: cancelación, falla y completado conservan los vínculos
        crew_ids = {}
        for label in ("cancel", "failure", "complete"):
            incident_x, dispatch_x, (unit_x,) = self.make_active()
            member = self.person()
            dm.assign_crew_member(dispatch_x, unit_x, member, self.admin_id)
            crew_ids[label] = (dispatch_x, unit_x, member)
        dm.cancel_active_dispatch(crew_ids["cancel"][0], "Motivo", self.admin_id)
        dm.report_unit_failure(crew_ids["failure"][0], crew_ids["failure"][1], "Falla",
                               self.admin_id)
        dispatch_x, unit_x, member = crew_ids["complete"]
        for operation in (dm.mark_unit_en_route, dm.mark_unit_on_scene, dm.start_unit_return,
                          dm.mark_unit_available):
            operation(dispatch_x, unit_x, self.admin_id)
        self.assertEqual(self.dispatch_state(dispatch_x), "COMPLETADO")
        for label, (dispatch_y, unit_y, member_y) in crew_ids.items():
            grouped = dc.get_dispatch_crew(dispatch_y)
            self.assertEqual([m["personnel_id"] for m in grouped[unit_y]], [member_y], label)
            for operation in (
                    lambda: dm.assign_crew_member(dispatch_y, unit_y, extra, self.admin_id),
                    lambda: dm.remove_crew_member(dispatch_y, unit_y, member_y, self.admin_id)):
                with self.assertRaises(dm.DispatchStateError, msg=label):
                    operation()
        self.assertEqual(len(self.links()), 3)

    # 7
    def test_authorization(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        person_id = self.person()
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            extra = self.person()
            self.assertEqual(
                len(dc.assign_crew_member(dispatch_id, unit_id, extra)) >= 1, True)
            dc.remove_crew_member(dispatch_id, unit_id, extra)
            self.assertEqual(self.crew_audit(dispatch_id)[-1][0], self.user_id(username))
        dc.assign_crew_member(dispatch_id, unit_id, person_id)
        before = self.links()
        session.end_session()
        _login("consulta.sur")
        # La autorización se revisa antes que la validación
        for operation in (
                lambda: dc.assign_crew_member(dispatch_id, unit_id, person_id),
                lambda: dc.assign_crew_member(True, "x", None),
                lambda: dc.remove_crew_member(dispatch_id, unit_id, person_id)):
            with self.assertRaises(AuthorizationDenied):
                operation()
        self.assertEqual(self.links(), before)
        # CONSULTA sí puede leer
        self.assertEqual(len(dc.get_dispatch_crew(dispatch_id)[unit_id]), 1)
        self.assertIsInstance(dc.list_available_crew_candidates(dispatch_id, unit_id), list)
        session.end_session()
        for operation in (
                lambda: dc.assign_crew_member(dispatch_id, unit_id, person_id),
                lambda: dc.remove_crew_member(dispatch_id, unit_id, person_id),
                lambda: dc.get_dispatch_crew(dispatch_id),
                lambda: dc.list_available_crew_candidates(dispatch_id, unit_id)):
            with self.assertRaises(NotAuthenticated):
                operation()
        _login("admin.demo")
        for bad in (True, "1", None):
            with self.assertRaises(dc.DispatchValidationError):
                dc.assign_crew_member(dispatch_id, unit_id, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.assign_crew_member(dispatch_id, unit_id, bad, self.admin_id)

    # 8
    def test_rollback_on_audit_failure(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        member, newcomer = self.person(), self.person()
        dm.assign_crew_member(dispatch_id, unit_id, member, self.admin_id)
        before = (self.links(), self.table_count("bitacora"))
        with mock.patch.object(dm, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(mysql.connector.Error):
                dm.assign_crew_member(dispatch_id, unit_id, newcomer, self.admin_id)
            with self.assertRaises(mysql.connector.Error):
                dm.remove_crew_member(dispatch_id, unit_id, member, self.admin_id)
        self.assertEqual((self.links(), self.table_count("bitacora")), before)
        with self.assertRaises(dm.InvalidReferenceError):
            dm.assign_crew_member(dispatch_id, unit_id, newcomer, 999999999)
        with self.assertRaises(dm.InvalidReferenceError):
            dm.remove_crew_member(dispatch_id, unit_id, member, 999999999)
        self.assertEqual((self.links(), self.table_count("bitacora")), before)

    def test_unique_violation_is_translated(self):
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        person_id = self.person()
        error = mysql.connector.IntegrityError(
            msg="Duplicate entry '1-2' for key 'despacho_personal.uq_dp_despacho_unidad_personal'",
            errno=1062)
        self.assertIsInstance(dm._translate_error(error), dm.DuplicateCrewMemberError)
        # El INSERT falla con la violación UNIQUE (como si otra transacción ganara la carrera)
        original = dm._write

        def failing_insert(operation, isolation=None):
            def wrapped(cursor):
                real_execute = cursor.execute

                class Proxy:
                    def execute(self, query, params=()):
                        if query.startswith("INSERT INTO despacho_personal"):
                            raise error
                        return real_execute(query, params)

                    def __getattr__(self, name):
                        return getattr(cursor, name)

                return operation(Proxy())
            return original(wrapped, isolation)

        with mock.patch.object(dm, "_write", failing_insert):
            with self.assertRaises(dm.DuplicateCrewMemberError):
                dm.assign_crew_member(dispatch_id, unit_id, person_id, self.admin_id)
        self.assertEqual(self.links(), [])
        self.assertEqual(self.crew_audit(dispatch_id), [])

    # 9
    def test_source_guards(self):
        writers = {}
        for path in list((ROOT / "models").glob("*.py")) + list((ROOT / "controllers").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for statement in ("INSERT INTO despacho_personal", "DELETE FROM despacho_personal",
                              "UPDATE despacho_personal"):
                if statement in source:
                    writers.setdefault(path.name, []).append(statement)
        self.assertEqual(writers, {"dispatch_model.py": [
            "INSERT INTO despacho_personal", "DELETE FROM despacho_personal"]})
        source = (ROOT / "models" / "dispatch_model.py").read_text(encoding="utf-8")
        # Cancelación, falla, liberación y completado nunca tocan la tripulación
        for name in ("_operate_unit", "_cancel_active_dispatch_in_tx", "report_unit_failure",
                     "_complete_dispatch_if_no_active_participations", "activate_dispatch",
                     "cancel_dispatch_draft", "cancel_active_dispatch"):
            body = function_body(source, name)
            self.assertNotIn("despacho_personal", body, name)

    def test_seed_crew_untouched_by_operations(self):
        seed_rows = db_all("SELECT COUNT(*) FROM despacho_personal")[0][0]
        incident_id, dispatch_id, (unit_id,) = self.make_active()
        dm.assign_crew_member(dispatch_id, unit_id, self.person(), self.admin_id)
        self.assertEqual(db_all("SELECT COUNT(*) FROM despacho_personal")[0][0], seed_rows + 1)
        seed_after = db_all("SELECT * FROM despacho_personal ORDER BY id_despacho_personal")
        self.assertEqual(seed_after[:len(self.crew_before)], self.crew_before)


if __name__ == "__main__":
    unittest.main()

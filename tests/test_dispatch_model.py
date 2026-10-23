import re
import threading
import unittest
from pathlib import Path
from unittest import mock

import mysql.connector

from models import dispatch_model as dm
from models import incident_model, unit_model
from config.database import get_connection
from tests._dispatch_support import DispatchTestBase, db_exec

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_KEYS = {
    "dispatch_id", "incident_id", "incident_code", "incident_state_code",
    "incident_state_name", "incident_address", "state_id", "state_code", "state_name",
    "created_by_id", "created_by_username", "dispatched_at", "finished_at",
    "cancelled_at", "cancel_reason", "observations", "unit_count", "created_at",
}
DRAFT_UNIT_KEYS = {
    "dispatch_unit_id", "unit_id", "unit_code", "unit_name", "plate", "type_name",
    "station_id", "station_code", "station_name", "station_active", "unit_active",
    "state_code", "state_name", "eligible_now", "assigned_at", "departed_at",
    "arrived_at", "released_at", "origin_station_code", "origin_station_name",
}
AVAILABLE_KEYS = {
    "unit_id", "unit_code", "unit_name", "plate", "type_id", "type_name", "station_id",
    "station_code", "station_name", "state_code", "state_name",
}


def function_body(source, name):
    """Texto de una función de nivel módulo hasta la siguiente definición."""
    start = source.index(chr(10) + "def " + name + "(")
    end = source.find(chr(10) + "def ", start + 1)
    return source[start:end if end != -1 else len(source)]


class DispatchModelTest(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")

    def incident(self, suffix=None, state="REPORTADO"):
        self._incident_counter = getattr(self, "_incident_counter", 0) + 1
        return self.insert_incident(suffix or f"N{self._incident_counter}", state)

    def draft(self, incident_id=None, observations=None):
        incident_id = incident_id or self.incident()
        return dm.create_dispatch_draft(incident_id, observations, self.admin_id)

    # 1
    def test_create_draft(self):
        incident_id = self.incident("X")
        unit_id = self.temp_unit()
        incident_before = self.row(incident_id)
        unit_before = self.unit_row(unit_id)
        dispatch_id = dm.create_dispatch_draft(incident_id, "  Obs  ", self.admin_id)
        dispatch = dm.get_dispatch(dispatch_id)
        self.assertEqual(set(dispatch), DISPATCH_KEYS)
        self.assertEqual(dispatch["state_code"], "CREADO")
        self.assertEqual(dispatch["incident_id"], incident_id)
        self.assertEqual(dispatch["incident_code"], f"{self.prefix}-X")
        self.assertEqual(dispatch["incident_state_code"], "REPORTADO")
        self.assertIsNone(dispatch["dispatched_at"])
        self.assertIsNone(dispatch["finished_at"])
        self.assertIsNone(dispatch["cancelled_at"])
        self.assertEqual(dispatch["observations"], "Obs")
        self.assertEqual(dispatch["created_by_username"], "admin.demo")
        self.assertEqual(dispatch["unit_count"], 0)
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual(self.dispatch_audit(dispatch_id), [
            (self.admin_id, "CREAR", "DESPACHO", dispatch_id,
             f"Borrador creado para {self.prefix}-X.")])
        self.assertEqual(self.preselections(dispatch_id), [])

    # 2
    def test_create_terminal_rejected_and_other_states_allowed(self):
        for state in ("CERRADO", "CANCELADO"):
            incident_id = self.incident(state, state)
            audit_before = self.table_count("bitacora")
            with self.assertRaises(dm.IncidentTerminalError):
                dm.create_dispatch_draft(incident_id, None, self.admin_id)
            self.assertEqual(self.dispatch_rows(incident_id), [])
            self.assertEqual(self.table_count("bitacora"), audit_before)
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            incident_id = self.incident(state, state)
            dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
            self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
            self.assertEqual(self.state_of(incident_id), state)

    def test_create_missing_incident(self):
        before = self.table_count("despachos")
        for bad in (999999, 0, -1):
            with self.assertRaises(dm.DispatchIncidentNotFoundError):
                dm.create_dispatch_draft(bad, None, self.admin_id)
        self.assertEqual(self.table_count("despachos"), before)

    def test_create_invalid_observations(self):
        incident_id = self.incident()
        for bad in (5, ["x"], "x" * 65536):
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.create_dispatch_draft(incident_id, bad, self.admin_id)
        self.assertEqual(self.dispatch_rows(incident_id), [])

    def test_empty_observations_become_null(self):
        dispatch_id = self.draft(observations="   ")
        self.assertIsNone(dm.get_dispatch(dispatch_id)["observations"])

    # 3
    def test_one_to_many(self):
        incident_id = self.incident()
        ids = [self.draft(incident_id) for _ in range(3)]
        listed = dm.list_dispatches(incident_id=incident_id)
        self.assertEqual([d["dispatch_id"] for d in listed], sorted(ids, reverse=True))
        self.assertTrue(all(d["state_code"] == "CREADO" for d in listed))
        states = {s["code"]: s["state_id"] for s in dm.list_dispatch_states()}
        self.assertEqual(len(dm.list_dispatches(incident_id, states["CREADO"])), 3)
        self.assertEqual(dm.list_dispatches(incident_id, states["ACTIVO"]), [])
        drafts = dm.list_dispatches(incident_id, state_code="CREADO")
        self.assertEqual(len(drafts), 3)
        self.assertEqual(dm.list_dispatches(incident_id, state_code="ACTIVO"), [])
        self.assertEqual(len(self.dispatch_rows(incident_id)), 3)

    def test_list_dispatches_unit_count_and_seed(self):
        dispatch_id = self.draft()
        for _ in range(2):
            dm.add_unit_to_draft(dispatch_id, self.temp_unit())
        mine = [d for d in dm.list_dispatches() if d["dispatch_id"] == dispatch_id]
        self.assertEqual(mine[0]["unit_count"], 2)
        all_dispatches = dm.list_dispatches()
        ids = [d["dispatch_id"] for d in all_dispatches]
        self.assertEqual(ids, sorted(ids, reverse=True))
        seed_one = [d for d in all_dispatches if d["dispatch_id"] == 1][0]
        self.assertEqual(seed_one["state_code"], "CREADO")
        self.assertEqual([s["code"] for s in dm.list_dispatch_states()],
                         ["CREADO", "ACTIVO", "COMPLETADO", "CANCELADO"])
        self.assertIsNone(dm.get_dispatch(999999))

    # 4
    def test_add_unit(self):
        dispatch_id = self.draft()
        unit_id = self.temp_unit(name="Unidad X", plate=f"{self.plate_prefix}-1")
        unit_before = self.unit_row(unit_id)
        incident_before = self.row(self.dispatch_rows_incident(dispatch_id))
        du_id = dm.add_unit_to_draft(dispatch_id, unit_id)
        rows = self.preselections(dispatch_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], du_id)
        # id, despacho, unidad y todo lo demás NULL
        self.assertEqual(rows[0][1:3], (dispatch_id, unit_id))
        self.assertTrue(all(value is None for value in rows[0][3:]))
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.row(self.dispatch_rows_incident(dispatch_id)), incident_before)
        draft_units = dm.list_draft_units(dispatch_id)
        self.assertEqual(len(draft_units), 1)
        self.assertEqual(set(draft_units[0]), DRAFT_UNIT_KEYS)
        self.assertTrue(draft_units[0]["eligible_now"])
        self.assertIs(draft_units[0]["unit_active"], True)
        self.assertEqual(draft_units[0]["dispatch_unit_id"], du_id)
        self.assertEqual(dm.get_dispatch(dispatch_id)["unit_count"], 1)
        self.assertEqual(self.dispatch_audit(dispatch_id)[0][1], "CREAR")
        self.assertEqual(len(self.dispatch_audit(dispatch_id)), 1)

    def dispatch_rows_incident(self, dispatch_id):
        return self.dispatch_row(dispatch_id)[0][1]

    def test_add_unit_missing_dispatch(self):
        unit_id = self.temp_unit()
        with self.assertRaises(dm.DispatchNotFoundError):
            dm.add_unit_to_draft(999999, unit_id)

    # 5
    def test_remove_unit(self):
        dispatch_id = self.draft()
        other_dispatch = self.draft()
        keep, drop = self.temp_unit(), self.temp_unit()
        dm.add_unit_to_draft(dispatch_id, keep)
        dm.add_unit_to_draft(dispatch_id, drop)
        dm.add_unit_to_draft(other_dispatch, drop)
        keep_before, drop_before = self.unit_row(keep), self.unit_row(drop)
        other_rows = self.preselections(other_dispatch)
        self.assertTrue(dm.remove_unit_from_draft(dispatch_id, drop))
        self.assertEqual([r[2] for r in self.preselections(dispatch_id)], [keep])
        self.assertEqual(self.preselections(other_dispatch), other_rows)
        self.assertEqual(self.unit_row(keep), keep_before)
        self.assertEqual(self.unit_row(drop), drop_before)
        with self.assertRaises(dm.DraftUnitNotFoundError):
            dm.remove_unit_from_draft(dispatch_id, drop)
        with self.assertRaises(dm.DraftUnitNotFoundError):
            dm.remove_unit_from_draft(dispatch_id, 999999)
        self.assertFalse(dm.remove_unit_from_draft(999999, drop))
        self.assertEqual(len(self.preselections(dispatch_id)), 1)

    # 6
    def test_duplicate_rejected(self):
        dispatch_id = self.draft()
        unit_id = self.temp_unit()
        dm.add_unit_to_draft(dispatch_id, unit_id)
        with self.assertRaises(dm.DuplicateDraftUnitError):
            dm.add_unit_to_draft(dispatch_id, unit_id)
        self.assertEqual(len(self.preselections(dispatch_id)), 1)

    def test_unique_violation_is_mapped(self):
        # Fuerza la ruta de la restricción UNIQUE sin pasar por el pre-chequeo
        error = mysql.connector.IntegrityError(
            msg="Duplicate entry '1-2' for key 'despacho_unidades.uq_despacho_unidades_despacho_unidad'",
            errno=1062)
        self.assertIsInstance(dm._translate_error(error), dm.DuplicateDraftUnitError)
        other = mysql.connector.IntegrityError(msg="Duplicate entry for key 'x'", errno=1062)
        self.assertIsNone(dm._translate_error(other))

    # 7
    def test_ineligible_units(self):
        dispatch_id = self.draft()
        cases = {}
        for state in ("DESPACHADA", "EN_RUTA", "EN_ESCENA", "REGRESANDO",
                      "MANTENIMIENTO", "FUERA_DE_SERVICIO"):
            cases[state] = self.temp_unit(state)
        inactive = self.temp_unit(active=False)
        station_id = self.temp_station()
        inactive_station = self.temp_unit(station_id=station_id)
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station_id,))
        for state, unit_id in cases.items():
            before = self.unit_row(unit_id)
            with self.assertRaises(dm.UnitNotEligibleError) as ctx:
                dm.add_unit_to_draft(dispatch_id, unit_id)
            self.assertIn("no está disponible", str(ctx.exception))
            self.assertEqual(self.unit_row(unit_id), before)
        with self.assertRaises(dm.UnitNotEligibleError) as ctx:
            dm.add_unit_to_draft(dispatch_id, inactive)
        self.assertIn("inactiva", str(ctx.exception))
        with self.assertRaises(dm.UnitNotEligibleError) as ctx:
            dm.add_unit_to_draft(dispatch_id, inactive_station)
        self.assertIn("estación", str(ctx.exception))
        for missing in (999999, 0, -1):
            with self.assertRaises(dm.UnitNotFoundError):
                dm.add_unit_to_draft(dispatch_id, missing)
        self.assertEqual(self.preselections(dispatch_id), [])

    # 8
    def test_preselection_is_not_a_reservation(self):
        draft_a, draft_b = self.draft(), self.draft()
        unit_id = self.temp_unit()
        dm.add_unit_to_draft(draft_a, unit_id)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.active_participation_count(unit_id), 0)
        connection = get_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            self.assertFalse(unit_model._has_active_participation(cursor, unit_id))
            connection.rollback()
            cursor.close()
        finally:
            connection.close()
        ids_a = [u["unit_id"] for u in dm.list_available_units(dispatch_id=draft_a)]
        ids_b = [u["unit_id"] for u in dm.list_available_units(dispatch_id=draft_b)]
        ids_none = [u["unit_id"] for u in dm.list_available_units()]
        self.assertNotIn(unit_id, ids_a)
        self.assertIn(unit_id, ids_b)
        self.assertIn(unit_id, ids_none)
        dm.add_unit_to_draft(draft_b, unit_id)
        self.assertEqual(len(self.preselections(draft_a)), 1)
        self.assertEqual(len(self.preselections(draft_b)), 1)
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        self.assertEqual(self.active_participation_count(unit_id), 0)

    # 9
    def test_stale_draft_keeps_real_state(self):
        dispatch_id = self.draft()
        station_id = self.temp_station()
        unit_id = self.temp_unit(station_id=station_id)
        dm.add_unit_to_draft(dispatch_id, unit_id)
        rows_before = self.preselections(dispatch_id)
        for state in ("MANTENIMIENTO", "EN_RUTA"):
            self.set_unit_state(unit_id, state)
            unit = dm.list_draft_units(dispatch_id)[0]
            self.assertEqual(unit["state_code"], state)
            self.assertFalse(unit["eligible_now"])
            self.assertEqual(self.preselections(dispatch_id), rows_before)
        self.set_unit_state(unit_id, "DISPONIBLE")
        self.assertTrue(dm.list_draft_units(dispatch_id)[0]["eligible_now"])
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station_id,))
        unit = dm.list_draft_units(dispatch_id)[0]
        self.assertFalse(unit["station_active"])
        self.assertFalse(unit["eligible_now"])
        db_exec("UPDATE estaciones SET activo = 1 WHERE id_estacion = %s", (station_id,))
        db_exec("UPDATE unidades SET activo = 0 WHERE id_unidad = %s", (unit_id,))
        unit = dm.list_draft_units(dispatch_id)[0]
        self.assertIs(unit["unit_active"], False)
        self.assertFalse(unit["eligible_now"])
        self.assertEqual(self.preselections(dispatch_id), rows_before)
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertEqual(self.active_participation_count(unit_id), 0)
        self.assertNotIn(unit_id, [u["unit_id"] for u in dm.list_available_units()])

    # 11
    def test_update_observations(self):
        dispatch_id = self.draft(observations="Inicial")
        self.assertTrue(dm.update_dispatch_draft(dispatch_id, " Nueva "))
        self.assertEqual(dm.get_dispatch(dispatch_id)["observations"], "Nueva")
        before = self.dispatch_row(dispatch_id)
        self.assertTrue(dm.update_dispatch_draft(dispatch_id, "Nueva"))
        self.assertEqual(self.dispatch_row(dispatch_id), before)
        self.assertTrue(dm.update_dispatch_draft(dispatch_id, ""))
        self.assertIsNone(dm.get_dispatch(dispatch_id)["observations"])
        self.assertTrue(dm.update_dispatch_draft(dispatch_id, None))
        self.assertFalse(dm.update_dispatch_draft(999999, "x"))
        with self.assertRaises(dm.InvalidDispatchDataError):
            dm.update_dispatch_draft(dispatch_id, 5)
        row = self.dispatch_row(dispatch_id)[0]
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertIsNone(row[4])
        self.assertEqual(len(self.dispatch_audit(dispatch_id)), 1)

    def test_non_draft_rejected(self):
        unit_id = self.temp_unit()
        for state in ("ACTIVO", "COMPLETADO", "CANCELADO"):
            incident_id = self.incident(state, "REPORTADO")
            dispatch_id = self.add_dispatch(incident_id, state)
            held = self.temp_unit()
            du_id = self.add_assigned_participation(dispatch_id, held)
            before = self.dispatch_row(dispatch_id)
            rows_before = self.preselections(dispatch_id)
            with self.assertRaises(dm.DispatchStateError):
                dm.update_dispatch_draft(dispatch_id, "x")
            with self.assertRaises(dm.DispatchStateError):
                dm.add_unit_to_draft(dispatch_id, unit_id)
            with self.assertRaises(dm.DispatchStateError):
                dm.remove_unit_from_draft(dispatch_id, held)
            with self.assertRaises(dm.DispatchStateError):
                dm.cancel_dispatch_draft(dispatch_id, "Motivo", self.admin_id)
            self.assertEqual(self.dispatch_row(dispatch_id), before)
            self.assertEqual(self.preselections(dispatch_id), rows_before)
            self.assertEqual(rows_before[0][0], du_id)
            self.assertEqual(self.dispatch_audit(dispatch_id), [])

    # 12
    def test_cancel_draft(self):
        incident_id = self.incident()
        dispatch_id = self.draft(incident_id)
        unit_id = self.temp_unit()
        dm.add_unit_to_draft(dispatch_id, unit_id)
        incident_before = self.row(incident_id)
        unit_before = self.unit_row(unit_id)
        rows_before = self.preselections(dispatch_id)
        reason = "  " + "m" * 250 + "  "
        self.assertTrue(dm.cancel_dispatch_draft(dispatch_id, reason, self.admin_id))
        dispatch = dm.get_dispatch(dispatch_id)
        self.assertEqual(dispatch["state_code"], "CANCELADO")
        self.assertEqual(dispatch["cancel_reason"], "m" * 250)
        self.assertIsNotNone(dispatch["cancelled_at"])
        self.assertIsNone(dispatch["dispatched_at"])
        self.assertEqual(self.row(incident_id), incident_before)
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual(self.preselections(dispatch_id), rows_before)
        audit = self.dispatch_audit(dispatch_id)
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[1], (self.admin_id, "CAMBIO_ESTADO", "DESPACHO", dispatch_id,
                                    "CREADO → CANCELADO. Motivo: " + "m" * 250))
        # No se puede cancelar dos veces ni editar después
        with self.assertRaises(dm.DispatchStateError):
            dm.cancel_dispatch_draft(dispatch_id, "otra vez", self.admin_id)
        with self.assertRaises(dm.DispatchStateError):
            dm.add_unit_to_draft(dispatch_id, self.temp_unit())
        self.assertFalse(dm.cancel_dispatch_draft(999999, "x", self.admin_id))
        self.assertEqual(len(self.dispatch_audit(dispatch_id)), 2)

    # 13
    def test_cancel_invalid_reason(self):
        dispatch_id = self.draft()
        before = self.dispatch_row(dispatch_id)
        audit_before = self.table_count("bitacora")
        for bad in (None, "", "   ", 5, b"x", ["x"], "x" * 251, " " + "x" * 251):
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.cancel_dispatch_draft(dispatch_id, bad, self.admin_id)
        self.assertEqual(self.dispatch_row(dispatch_id), before)
        self.assertEqual(self.table_count("bitacora"), audit_before)

    # 17
    def test_rollback_create(self):
        incident_id = self.incident()
        audit_before = self.table_count("bitacora")
        with mock.patch.object(dm, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(mysql.connector.Error):
                dm.create_dispatch_draft(incident_id, None, self.admin_id)
        self.assertEqual(self.dispatch_rows(incident_id), [])
        with self.assertRaises(dm.InvalidReferenceError):
            dm.create_dispatch_draft(incident_id, None, 999999999)
        self.assertEqual(self.dispatch_rows(incident_id), [])
        self.assertEqual(self.table_count("bitacora"), audit_before)

    def test_rollback_cancel(self):
        dispatch_id = self.draft()
        before = self.dispatch_row(dispatch_id)
        audit_before = self.table_count("bitacora")
        with mock.patch.object(dm, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(mysql.connector.Error):
                dm.cancel_dispatch_draft(dispatch_id, "Motivo", self.admin_id)
        with self.assertRaises(dm.InvalidReferenceError):
            dm.cancel_dispatch_draft(dispatch_id, "Motivo", 999999999)
        self.assertEqual(self.dispatch_row(dispatch_id), before)
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertEqual(self.table_count("bitacora"), audit_before)

    def test_rollback_after_insert_of_add_unit(self):
        dispatch_id = self.draft()
        unit_id = self.temp_unit()
        original = dm._write

        def failing_write(operation):
            def wrapped(cursor):
                operation(cursor)
                raise RuntimeError("fallo tras el INSERT")
            return original(wrapped)

        with mock.patch.object(dm, "_write", failing_write):
            with self.assertRaises(RuntimeError):
                dm.add_unit_to_draft(dispatch_id, unit_id)
            with self.assertRaises(RuntimeError):
                dm.cancel_dispatch_draft(dispatch_id, "Motivo", self.admin_id)
        self.assertEqual(self.preselections(dispatch_id), [])
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertEqual(len(self.dispatch_audit(dispatch_id)), 1)

    # Consultas de apoyo
    def test_available_units_filters(self):
        station_id = self.temp_station()
        type_id = self.temp_unit_type()
        match = self.temp_unit(station_id=station_id, type_id=type_id,
                               name="Zzbomba_100%", plate=f"{self.plate_prefix}-Z")
        plain = self.temp_unit(station_id=station_id, name="Otra")
        busy = self.temp_unit("EN_RUTA", station_id=station_id)
        inactive = self.temp_unit(active=False, station_id=station_id)
        found = dm.list_available_units(station_id=station_id)
        self.assertEqual({u["unit_id"] for u in found}, {match, plain})
        self.assertEqual(set(found[0]), AVAILABLE_KEYS)
        self.assertTrue(all(u["state_code"] == "DISPONIBLE" for u in found))
        self.assertEqual([u["unit_id"] for u in dm.list_available_units(type_id=type_id)],
                         [match])
        for term in ("zzbomba", "_100%", f"{self.plate_prefix}-Z", "%", "_"):
            ids = [u["unit_id"] for u in dm.list_available_units(
                station_id=station_id, term=term)]
            self.assertIn(match, ids, term)
            if term in ("%", "_", "zzbomba"):
                self.assertNotIn(plain, ids, term)
        self.assertEqual(dm.list_available_units(station_id=station_id, term="\\"), [])
        self.assertNotIn(busy, [u["unit_id"] for u in dm.list_available_units()])
        self.assertNotIn(inactive, [u["unit_id"] for u in dm.list_available_units()])
        db = self.table_count("unidades")
        self.assertGreaterEqual(db, 11)
        ordered = dm.list_available_units()
        keys = [(u["station_code"], u["unit_code"]) for u in ordered]
        self.assertEqual(keys, sorted(keys, key=lambda k: (k[0].lower(), k[1].lower())))
        # Estación inactiva: sus unidades no se ofrecen
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (station_id,))
        self.assertEqual(dm.list_available_units(station_id=station_id), [])

    def test_selectable_incidents(self):
        ids = {state: self.incident(state, state) for state in (
            "REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO", "CERRADO", "CANCELADO")}
        found = {i["incident_id"]: i for i in dm.list_selectable_incidents()}
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            self.assertIn(ids[state], found)
            self.assertEqual(found[ids[state]]["state_code"], state)
        self.assertNotIn(ids["CERRADO"], found)
        self.assertNotIn(ids["CANCELADO"], found)
        self.assertTrue(all(i["state_code"] not in ("CERRADO", "CANCELADO")
                            for i in found.values()))
        self.assertTrue({"incident_id", "code", "address", "state_code",
                         "state_name"} <= set(next(iter(found.values()))))

    # 14
    def test_incident_cancellation_still_cancels_drafts(self):
        incident_id = self.incident()
        draft_a, draft_b = self.draft(incident_id), self.draft(incident_id)
        active = self.add_dispatch(self.incident(), "ACTIVO")
        unit_id = self.temp_unit()
        dm.add_unit_to_draft(draft_a, unit_id)
        rows_before = self.preselections(draft_a)
        self.assertTrue(incident_model.cancel_incident(incident_id, "Falsa alarma", self.admin_id))
        for dispatch_id in (draft_a, draft_b):
            dispatch = dm.get_dispatch(dispatch_id)
            self.assertEqual(dispatch["state_code"], "CANCELADO")
            self.assertEqual(dispatch["cancel_reason"], "Falsa alarma")
            self.assertIsNone(dispatch["dispatched_at"])
            audit = self.dispatch_audit(dispatch_id)
            self.assertEqual(audit[-1][1:3], ("CAMBIO_ESTADO", "DESPACHO"))
            self.assertIn("CREADO → CANCELADO", audit[-1][4])
        self.assertEqual(self.preselections(draft_a), rows_before)
        self.assertEqual(self.dispatch_state(active), "ACTIVO")
        self.assertEqual(self.unit_state(unit_id), "DISPONIBLE")
        # Sobre un incidente cancelado ya no se crean borradores
        with self.assertRaises(dm.IncidentTerminalError):
            dm.create_dispatch_draft(incident_id, None, self.admin_id)

    def test_concurrent_create_vs_incident_cancel(self):
        for round_number in range(3):
            incident_id = self.incident(f"C{round_number}")
            outcome = {}

            def create():
                try:
                    outcome["id"] = dm.create_dispatch_draft(incident_id, None, self.admin_id)
                except dm.IncidentTerminalError as error:
                    outcome["error"] = error

            worker = threading.Thread(target=create)
            worker.start()
            incident_model.cancel_incident(incident_id, "Concurrente", self.admin_id)
            worker.join(30)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.state_of(incident_id), "CANCELADO")
            states = [self.dispatch_state(r[0]) for r in self.dispatch_rows(incident_id)]
            self.assertNotIn("CREADO", states)
            self.assertEqual(len(states), 1 if "id" in outcome else 0)

    # Endurecimiento: borrador CREADO bajo un incidente terminal
    def terminal_draft(self, terminal_state):
        """Borrador con una unidad preseleccionada cuyo incidente pasa a terminal."""
        incident_id = self.incident()
        dispatch_id = self.draft(incident_id, "Orig")
        kept = self.temp_unit()
        dm.add_unit_to_draft(dispatch_id, kept)
        self.set_state(incident_id, terminal_state)
        return incident_id, dispatch_id, kept

    def state_fingerprint(self, incident_id, dispatch_id, unit_ids):
        return (self.dispatch_row(dispatch_id), self.preselections(dispatch_id),
                [self.unit_row(u) for u in unit_ids], self.row(incident_id),
                self.table_count("bitacora"), self.table_count("despacho_unidades"))

    def test_terminal_incident_blocks_draft_changes(self):
        for terminal in ("CERRADO", "CANCELADO"):
            incident_id, dispatch_id, kept = self.terminal_draft(terminal)
            other = self.temp_unit()
            before = self.state_fingerprint(incident_id, dispatch_id, (kept, other))
            attempts = (
                lambda: dm.update_dispatch_draft(dispatch_id, "Nueva"),
                # Sin cambios reales: la regla aplica antes del atajo idempotente
                lambda: dm.update_dispatch_draft(dispatch_id, "Orig"),
                lambda: dm.add_unit_to_draft(dispatch_id, other),
                lambda: dm.remove_unit_from_draft(dispatch_id, kept),
            )
            for attempt in attempts:
                with self.assertRaises(dm.DraftIncidentClosedError) as ctx:
                    attempt()
                self.assertIsInstance(ctx.exception, dm.DispatchStateError)
                self.assertIn("cerrado o cancelado", str(ctx.exception))
                self.assertEqual(self.state_fingerprint(
                    incident_id, dispatch_id, (kept, other)), before, terminal)
            self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")

    def test_terminal_incident_draft_can_still_be_cancelled(self):
        for terminal in ("CERRADO", "CANCELADO"):
            incident_id, dispatch_id, kept = self.terminal_draft(terminal)
            incident_before = self.row(incident_id)
            unit_before = self.unit_row(kept)
            rows_before = self.preselections(dispatch_id)
            audit_before = len(self.dispatch_audit(dispatch_id))
            reason = "t" * 250
            self.assertTrue(dm.cancel_dispatch_draft(dispatch_id, reason, self.admin_id))
            dispatch = dm.get_dispatch(dispatch_id)
            self.assertEqual(dispatch["state_code"], "CANCELADO")
            self.assertEqual(dispatch["cancel_reason"], reason)
            self.assertIsNotNone(dispatch["cancelled_at"])
            self.assertIsNone(dispatch["dispatched_at"])
            audit = self.dispatch_audit(dispatch_id)
            self.assertEqual(len(audit), audit_before + 1)
            self.assertEqual(audit[-1][1:3], ("CAMBIO_ESTADO", "DESPACHO"))
            self.assertEqual(self.preselections(dispatch_id), rows_before)
            self.assertEqual(self.row(incident_id), incident_before)
            self.assertEqual(self.unit_row(kept), unit_before)

    def test_non_terminal_incidents_keep_working(self):
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            incident_id = self.incident(state=state)
            dispatch_id = self.draft(incident_id)
            unit_id = self.temp_unit()
            self.assertTrue(dm.update_dispatch_draft(dispatch_id, state))
            self.assertEqual(dm.get_dispatch(dispatch_id)["observations"], state)
            self.assertTrue(dm.update_dispatch_draft(dispatch_id, state))
            dm.add_unit_to_draft(dispatch_id, unit_id)
            self.assertEqual(len(self.preselections(dispatch_id)), 1)
            self.assertTrue(dm.remove_unit_from_draft(dispatch_id, unit_id))
            self.assertEqual(self.preselections(dispatch_id), [])
            self.assertEqual(self.state_of(incident_id), state)

    def test_model_rejects_non_int_ids(self):
        dispatch_id = self.draft()
        unit_id = self.temp_unit()
        dispatches_before = self.table_count("despachos")
        audit_before = self.table_count("bitacora")
        for bad in (True, False, "1", 1.0, None):
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.create_dispatch_draft(bad, None, self.admin_id)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.create_dispatch_draft(self.incident(), None, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.update_dispatch_draft(bad, "x")
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.add_unit_to_draft(bad, unit_id)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.add_unit_to_draft(dispatch_id, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.remove_unit_from_draft(bad, unit_id)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.remove_unit_from_draft(dispatch_id, bad)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.cancel_dispatch_draft(bad, "Motivo", self.admin_id)
            with self.assertRaises(dm.InvalidDispatchDataError):
                dm.cancel_dispatch_draft(dispatch_id, "Motivo", bad)
        # True no se interpreta como el id 1 (incidente y despacho del seed)
        self.assertEqual(self.table_count("despachos"), dispatches_before)
        self.assertEqual(self.table_count("bitacora"), audit_before)
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertEqual(self.preselections(dispatch_id), [])

    # Revisión del código fuente: sin activación ni estados arbitrarios
    def test_source_state_changes_are_restricted(self):
        model_source = (ROOT / "models" / "dispatch_model.py").read_text(encoding="utf-8")
        controller_source = (ROOT / "controllers" / "dispatch_controller.py").read_text(
            encoding="utf-8")
        activation = function_body(model_source, "activate_dispatch")
        operation = function_body(model_source, "_operate_unit")
        completion = function_body(
            model_source, "_complete_dispatch_if_no_active_participations")
        outside = (model_source.replace(activation, "").replace(operation, "")
                   .replace(completion, ""))
        # El helper de finalización: único UPDATE a COMPLETADO, llamado solo al iniciar regreso
        self.assertEqual(completion.count("UPDATE despachos"), 1)
        self.assertEqual(completion.count("fecha_finalizacion = %s"), 1)
        self.assertEqual(completion.count("_state_id_by_code(cursor, COMPLETED_STATE_CODE)"), 1)
        self.assertEqual(completion.count("UPDATE unidades") + completion.count("UPDATE incidentes"), 0)
        self.assertEqual(completion.count("DELETE"), 0)
        self.assertEqual(
            model_source.count("_complete_dispatch_if_no_active_participations("), 2)
        call_index = operation.index("_complete_dispatch_if_no_active_participations(")
        self.assertIn('if kind == "return":', operation[max(0, call_index - 120):call_index])
        # Solo activate_dispatch y _operate_unit (OBJ 12) escriben unidades e incidentes
        self.assertEqual(operation.count("UPDATE unidades"), 1)
        self.assertEqual(operation.count("UPDATE incidentes"), 1)
        self.assertIn('if arrival and incident["state_code"] == "ASIGNADO":',
                      operation[:operation.index("UPDATE incidentes")])
        self.assertEqual(operation.count("UPDATE despacho_unidades"), 1)
        for forbidden in ("UPDATE despachos", "DELETE", "INSERT INTO despachos",
                          "INSERT INTO despacho_unidades"):
            self.assertEqual(operation.count(forbidden), 0, forbidden)
        # Las cuatro funciones públicas solo delegan en _operate_unit
        for name in ("mark_unit_en_route", "mark_unit_on_scene", "start_unit_return",
                     "mark_unit_available"):
            public = function_body(model_source, name)
            self.assertIn("_operate_unit(", public)
            self.assertNotIn("UPDATE", public)
        # Fuera de ellas no hay escrituras sobre unidades ni incidentes
        for forbidden in ("UPDATE unidades", "UPDATE incidentes"):
            self.assertEqual(outside.count(forbidden), 0, forbidden)
        # Las constantes de activación solo aparecen en su definición
        for constant in ("ACTIVATED_STATE_CODE", "DISPATCHED_UNIT_STATE_CODE",
                         "INCIDENT_ASSIGNED_CODE"):
            self.assertEqual(outside.count(constant), 1, constant)
        # Solo queda la definición de la constante con el literal ACTIVO
        self.assertEqual(outside.count('"ACTIVO"'), 1)
        self.assertNotIn("ACTIVO", controller_source)
        self.assertEqual(activation.count("UPDATE unidades"), 1)
        self.assertIn("SET id_estado_unidad = %s ", activation)
        self.assertEqual(activation.count("UPDATE incidentes"), 1)
        self.assertEqual(activation.count("_state_id_by_code(cursor, ACTIVATED_STATE_CODE)"), 1)
        self.assertEqual(activation.count("SET id_estado_despacho"), 1)
        self.assertEqual(activation.count("INSERT INTO"), 0)
        self.assertEqual(sorted(re.findall(r"INSERT INTO (\w+)", model_source)),
                         ["bitacora", "bitacora", "bitacora", "despacho_unidades", "despachos"])
        # Único INSERT en despachos usa el estado CREADO
        self.assertEqual(outside.count("_state_id_by_code(cursor, DRAFT_STATE_CODE)"), 1)
        # En la activación solo sirve como guarda del UPDATE (estado de origen)
        self.assertEqual(activation.count("_state_id_by_code(cursor, DRAFT_STATE_CODE)"), 1)
        self.assertIn("AND id_estado_despacho = %s", activation)
        # Estados de despacho: CANCELADO en el borrador y ACTIVO solo al activar
        self.assertEqual(model_source.count("SET id_estado_despacho"), 3)
        self.assertEqual(model_source.count("_state_id_by_code(cursor, CANCELLED_STATE_CODE)"), 1)
        self.assertEqual(model_source.count("UPDATE despachos"), 4)
        self.assertEqual(model_source.count("DELETE"), 1)
        delete_block = model_source[model_source.index("DELETE du FROM"):]
        delete_block = delete_block[:delete_block.index('"""')]
        self.assertIn("du.fecha_asignacion IS NULL", delete_block)
        self.assertIn("s.codigo = %s", delete_block)
        self.assertIn("DRAFT_STATE_CODE", model_source[model_source.index("DELETE du FROM"):][:900])


if __name__ == "__main__":
    unittest.main()

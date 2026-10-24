import datetime
import unittest
from decimal import Decimal
from unittest import mock

import mysql.connector

from models import incident_model as im
from tests._incident_support import IncidentTestBase, db_all

KEYS = {
    "incident_id", "code", "type_id", "type_name", "priority_id", "priority_name",
    "priority_level", "state_id", "state_code", "state_name", "reported_at", "address",
    "reference", "latitude", "longitude", "description", "affected_people",
    "registered_by_id", "registered_by_username", "assigned_at", "attention_started_at",
    "controlled_at", "closed_at", "cancelled_at", "cancel_reason",
}


class IncidentModelTest(IncidentTestBase):
    def _create(self, suffix="A", **overrides):
        values = {
            "code": f"{self.prefix}-{suffix}", "type_id": self.seed_type,
            "priority_id": self.seed_priority, "address": "Calle 1",
            "reference": "Ref", "latitude": Decimal("14.6"), "longitude": Decimal("-90.5"),
            "description": "Descripción de prueba", "affected_people": 2,
            "user_id": self.user_id("admin.demo"),
        }
        values.update(overrides)
        return im.create_incident(**values)

    def _fields(self, **overrides):
        values = {
            "type_id": self.seed_type, "priority_id": self.seed_priority,
            "address": "Calle 1", "reference": "Ref", "latitude": Decimal("14.6"),
            "longitude": Decimal("-90.5"), "description": "Descripción de prueba",
            "affected_people": 2,
        }
        values.update(overrides)
        return values

    def _ids(self, incidents):
        return [i["incident_id"] for i in incidents]

    def _user(self):
        return self.user_id("operador.central")

    def test_list_seed_and_keys(self):
        incidents = im.search_incidents()
        seed = [i for i in incidents if not i["code"].startswith(("TIN-", "TUI-"))]
        self.assertEqual(len(seed), 8)
        self.assertTrue(all(set(i) == KEYS for i in incidents))
        keys_order = [(i["reported_at"], i["incident_id"]) for i in incidents]
        self.assertEqual(keys_order, sorted(keys_order, reverse=True))
        self.assertEqual(len(im.list_incident_states()), 6)
        self.assertEqual(len([t for t in im.list_incident_types()
                              if not t["name"].startswith("TIT-")]), 6)
        priorities = [p for p in im.list_priorities() if not p["name"].startswith("TIP-")]
        self.assertEqual(len(priorities), 4)
        self.assertTrue(all({"priority_id", "name", "level", "active"} <= set(p)
                            for p in priorities))
        self.assertIsNone(im.get_incident(0))

    def test_search_and_filters(self):
        iid = self._create(description="Zzincendio especial", address="Zzcalle rara")
        for term in (f"{self.prefix}-A", "zzincendio", "ZZCALLE"):
            self.assertEqual(self._ids(im.search_incidents(term)), [iid], term)
        self.assertEqual(im.search_incidents("inexistente-xyz"), [])
        self.assertEqual(self._ids(im.search_incidents(self.prefix, self.seed_type,
                                                       self.seed_priority)), [iid])
        other_type = self.temp_type()
        self.assertEqual(im.search_incidents(self.prefix, type_id=other_type), [])
        self.assertEqual(im.search_incidents(self.prefix, priority_id=self.temp_priority()), [])
        reported = db_all("SELECT id_estado_incidente FROM estados_incidente "
                          "WHERE codigo = 'REPORTADO'")[0][0]
        self.assertEqual(self._ids(im.search_incidents(self.prefix, state_id=reported)), [iid])
        self.assertEqual(im.search_incidents(self.prefix, state_id=reported + 1000), [])
        by_type = im.search_incidents(type_id=self.seed_type)
        self.assertTrue(all(i["type_id"] == self.seed_type for i in by_type))
        self.assertIn(iid, self._ids(by_type))

    def test_wildcards_literal(self):
        special = self._create("S", description="100%_x\\y")
        plain = self._create("P", description="abc")
        for term in ("%", "_", "\\", "100%_x\\y"):
            found = self._ids(im.search_incidents(term))
            self.assertIn(special, found, term)
            self.assertNotIn(plain, found, term)
        self.assertNotIn(plain, self._ids(im.search_incidents("a%")))
        # todo resultado debe contener el literal buscado, sin depender de los datos del seed
        for term in ("%", "_", "\\"):
            for incident in im.search_incidents(term):
                texts = (incident["code"], incident["description"], incident["address"])
                self.assertTrue(any(term in text for text in texts), (term, incident["code"]))

    def test_create_valid(self):
        iid = self._create(reference="", latitude=None, longitude=None, affected_people=None)
        incident = im.get_incident(iid)
        self.assertEqual(incident["state_code"], "REPORTADO")
        self.assertEqual(incident["registered_by_username"], "admin.demo")
        self.assertIsNone(incident["reference"])
        self.assertIsNone(incident["latitude"])
        self.assertEqual(incident["affected_people"], 0)
        self.assertIsNone(incident["cancelled_at"])
        delta = datetime.datetime.now() - incident["reported_at"]
        self.assertLess(abs(delta.total_seconds()), 300)
        self.assertEqual(self.audit_rows(iid), [
            (self.user_id("admin.demo"), "CREAR", "INCIDENTE", iid,
             f"Registro inicial de {self.prefix}-A.")])
        full = im.get_incident(self._create("B", user_id=self._user()))
        self.assertEqual(full["registered_by_username"], "operador.central")
        self.assertIsInstance(full["latitude"], float)
        self.assertEqual(full["latitude"], 14.6)

    def test_create_invalid_references(self):
        for bad in (999999, 0, -1, "abc", None):
            with self.assertRaises(im.InvalidTypeError, msg=repr(bad)):
                self._create("T", type_id=bad)
            with self.assertRaises(im.InvalidPriorityError, msg=repr(bad)):
                self._create("P", priority_id=bad)
        self.assertEqual(self.count_prefix(), 0)
        self.assertEqual(self.count_audit_prefix(), 0)

    def test_create_inactive_catalogs(self):
        inactive_type = self.temp_type(active=False)
        inactive_priority = self.temp_priority(active=False)
        with self.assertRaises(im.InactiveTypeError):
            self._create("T", type_id=inactive_type)
        with self.assertRaises(im.InactivePriorityError):
            self._create("P", priority_id=inactive_priority)
        self.assertEqual(self.count_prefix(), 0)

    def test_create_duplicate_and_invalid_data(self):
        self._create()
        with self.assertRaises(im.DuplicateIncidentCodeError):
            self._create()
        self.assertEqual(self.count_prefix(), 1)
        with self.assertRaises(im.InvalidReferenceError):
            self._create("U", user_id=999999)
        with self.assertRaises(im.InvalidIncidentDataError):
            self._create("L", latitude=Decimal("91"), longitude=Decimal("0"))
        with self.assertRaises(im.InvalidIncidentDataError):
            self._create("O", latitude=Decimal("10"), longitude=None)
        self.assertEqual(self.count_prefix(), 1)
        self.assertEqual(self.count_audit_prefix(), 1)

    def test_create_rollback_when_audit_fails(self):
        with mock.patch.object(im, "_audit", side_effect=mysql.connector.Error("falla")):
            with self.assertRaises(mysql.connector.Error):
                self._create()
        self.assertEqual(self.count_prefix(), 0)
        self.assertEqual(self.count_audit_prefix(), 0)

    def test_update_persists_and_preserves_machine_fields(self):
        iid = self._create()
        before = self.fixed_row(iid)
        new_type = self.temp_type()
        new_priority = self.temp_priority()
        fields = self._fields(
            type_id=new_type, priority_id=new_priority, address="Otra calle",
            reference=None, latitude=Decimal("1.5"), longitude=Decimal("2.5"),
            description="Nueva descripción", affected_people=7)
        self.assertTrue(im.update_incident(iid, fields, self._user()))
        incident = im.get_incident(iid)
        self.assertEqual(
            (incident["type_id"], incident["priority_id"], incident["address"],
             incident["reference"], incident["latitude"], incident["longitude"],
             incident["description"], incident["affected_people"]),
            (new_type, new_priority, "Otra calle", None, 1.5, 2.5, "Nueva descripción", 7))
        self.assertEqual(self.fixed_row(iid), before)
        audit = self.audit_rows(iid)
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[1][:4], (self._user(), "MODIFICAR", "INCIDENTE", iid))
        self.assertEqual(audit[1][4], "Campos modificados: id_tipo_incidente, id_prioridad, "
                         "direccion, referencia, latitud, longitud, descripcion, "
                         "personas_afectadas.")

    def test_update_lists_only_changed_fields(self):
        iid = self._create()
        im.update_incident(iid, self._fields(address="Cambio", affected_people=2), self._user())
        self.assertEqual(self.audit_rows(iid)[-1][4], "Campos modificados: direccion.")
        # coordenadas iguales con distinta representación no cuentan como cambio
        im.update_incident(iid, self._fields(address="Cambio", latitude=Decimal("14.600000")),
                           self._user())
        self.assertEqual(len(self.audit_rows(iid)), 2)

    def test_update_noop_writes_nothing(self):
        iid = self._create()
        before = self.row(iid)
        self.assertTrue(im.update_incident(iid, self._fields(), self._user()))
        self.assertEqual(self.row(iid), before)
        self.assertEqual(len(self.audit_rows(iid)), 1)

    def test_update_each_state(self):
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            iid = self.insert_incident(suffix=state, state_code=state)
            before = self.fixed_row(iid)
            self.assertTrue(im.update_incident(iid, self._fields(address=f"Dir {state}"),
                                               self._user()), state)
            self.assertEqual(im.get_incident(iid)["address"], f"Dir {state}")
            self.assertEqual(im.get_incident(iid)["state_code"], state)
            self.assertEqual(self.fixed_row(iid), before)
            self.assertEqual(len(self.audit_rows(iid)), 1)

    def test_update_rejected_in_terminal_states(self):
        for state in ("CERRADO", "CANCELADO"):
            iid = self.insert_incident(suffix=state, state_code=state)
            before = self.row(iid)
            with self.assertRaises(im.IncidentStateError, msg=state):
                im.update_incident(iid, self._fields(address="No debe"), self._user())
            self.assertEqual(self.row(iid), before)
            self.assertEqual(self.audit_rows(iid), [])

    def test_update_unknown_field_and_missing_incident(self):
        iid = self._create()
        for key in ("state_id", "id_estado_incidente", "code", "fecha_cierre", "user_id"):
            with self.assertRaises(ValueError, msg=key):
                im.update_incident(iid, {key: 1}, self._user())
        self.assertFalse(im.update_incident(0, self._fields(), self._user()))
        self.assertEqual(im.get_incident(iid)["state_code"], "REPORTADO")

    def test_update_inactive_catalogs(self):
        type_id = self.temp_type()
        priority_id = self.temp_priority()
        iid = self._create(type_id=type_id, priority_id=priority_id)
        self.deactivate_type(type_id)
        self.deactivate_priority(priority_id)
        # conservar la referencia histórica inactiva está permitido
        self.assertTrue(im.update_incident(
            iid, self._fields(type_id=type_id, priority_id=priority_id, address="Nueva"),
            self._user()))
        self.assertEqual(im.get_incident(iid)["address"], "Nueva")
        self.assertEqual(im.get_incident(iid)["type_id"], type_id)
        self.assertIn(iid, self._ids(im.search_incidents(self.prefix)))
        # cambiar hacia un catálogo inactivo se rechaza
        other_type = self.temp_type(active=False)
        other_priority = self.temp_priority(active=False)
        with self.assertRaises(im.InactiveTypeError):
            im.update_incident(iid, self._fields(type_id=other_type), self._user())
        with self.assertRaises(im.InactivePriorityError):
            im.update_incident(iid, self._fields(type_id=type_id, priority_id=other_priority),
                               self._user())
        with self.assertRaises(im.InvalidTypeError):
            im.update_incident(iid, self._fields(type_id=999999), self._user())
        with self.assertRaises(im.InvalidPriorityError):
            im.update_incident(iid, self._fields(priority_id=999999), self._user())
        self.assertEqual(im.get_incident(iid)["type_id"], type_id)
        self.assertEqual(im.get_incident(iid)["priority_id"], priority_id)

    def test_update_rollback_when_audit_fails(self):
        iid = self._create()
        before = self.row(iid)
        with mock.patch.object(im, "_audit", side_effect=mysql.connector.Error("falla")):
            with self.assertRaises(mysql.connector.Error):
                im.update_incident(iid, self._fields(address="Cambio"), self._user())
        self.assertEqual(self.row(iid), before)
        self.assertEqual(len(self.audit_rows(iid)), 1)
        # usuario inexistente: la bitácora falla por FK y revierte el UPDATE
        with self.assertRaises(im.InvalidReferenceError):
            im.update_incident(iid, self._fields(address="Cambio"), 999999)
        self.assertEqual(self.row(iid), before)
        self.assertEqual(len(self.audit_rows(iid)), 1)

    def test_cancel_reported(self):
        iid = self._create()
        self.assertTrue(im.cancel_incident(iid, "Falsa alarma", self._user()))
        incident = im.get_incident(iid)
        self.assertEqual(incident["state_code"], "CANCELADO")
        self.assertEqual(incident["cancel_reason"], "Falsa alarma")
        self.assertGreaterEqual(incident["cancelled_at"], incident["reported_at"])
        audit = self.audit_rows(iid)
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[1], (self._user(), "CAMBIO_ESTADO", "INCIDENTE", iid,
                                    "REPORTADO → CANCELADO. Motivo: Falsa alarma"))
        self.assertIsNone(incident["assigned_at"])
        self.assertIsNone(incident["closed_at"])

    def test_cancel_rejected_states_and_twice(self):
        # ASIGNADO y EN_ATENCION sin control ya se pueden cancelar desde OBJ 14
        for state in ("CONTROLADO", "CERRADO", "CANCELADO"):
            iid = self.insert_incident(suffix=state, state_code=state)
            before = self.row(iid)
            with self.assertRaises(im.IncidentStateError, msg=state) as ctx:
                im.cancel_incident(iid, "Motivo", self._user())
            if state == "CANCELADO":
                self.assertEqual(str(ctx.exception), "El incidente ya está cancelado.")
            self.assertEqual(self.row(iid), before)
            self.assertEqual(self.audit_rows(iid), [])
        iid = self._create("T")
        im.cancel_incident(iid, "Una vez", self._user())
        with self.assertRaises(im.IncidentStateError):
            im.cancel_incident(iid, "Dos veces", self._user())
        self.assertEqual(im.get_incident(iid)["cancel_reason"], "Una vez")
        self.assertEqual(len(self.audit_rows(iid)), 2)
        with self.assertRaises(im.IncidentStateError):
            im.update_incident(iid, self._fields(address="Tras cancelar"), self._user())

    def _unit_state(self):
        return db_all("SELECT * FROM unidades WHERE codigo NOT LIKE 'TUN-%' ORDER BY id_unidad")

    def test_cancel_resolves_created_dispatches(self):
        iid = self.insert_incident("D2")
        unit_id = db_all("SELECT MIN(id_unidad) FROM unidades")[0][0]
        d1 = self.add_dispatch(iid)
        d2 = self.add_dispatch(iid)
        d_done = self.add_dispatch(iid, "COMPLETADO")
        d_cancelled = self.add_dispatch(iid, "CANCELADO")
        self.add_preselection(d2, unit_id)
        history = self.dispatch_rows(iid)[2:]
        units_before = self._unit_state()
        presel_before = self.preselections(d2)
        self.assertTrue(im.cancel_incident(iid, "Falsa alarma", self._user()))
        self.assertEqual(self.state_of(iid), "CANCELADO")
        for dispatch_id in (d1, d2):
            self.assertEqual(self.dispatch_state(dispatch_id), "CANCELADO")
            row = db_all("SELECT fecha_despacho, fecha_cancelacion, motivo_cancelacion "
                         "FROM despachos WHERE id_despacho = %s", (dispatch_id,))[0]
            self.assertIsNone(row[0])
            self.assertIsNotNone(row[1])
            self.assertEqual(row[2], "Falsa alarma")
            self.assertEqual(self.dispatch_audit(dispatch_id), [(
                self._user(), "CAMBIO_ESTADO", "DESPACHO", dispatch_id,
                f"CREADO → CANCELADO por cancelación del incidente {self.prefix}-D2.")])
        self.assertEqual(self.dispatch_rows(iid)[2:], history)
        self.assertEqual(self.dispatch_state(d_done), "COMPLETADO")
        self.assertEqual(self.dispatch_state(d_cancelled), "CANCELADO")
        self.assertEqual(self.dispatch_audit(d_done), [])
        self.assertEqual(self.preselections(d2), presel_before)
        self.assertEqual(self._unit_state(), units_before)
        self.assertEqual(len(self.audit_rows(iid)), 1)
        self.assertEqual(db_all(
            "SELECT COUNT(*) FROM despachos d JOIN estados_despacho e "
            "ON e.id_estado_despacho = d.id_estado_despacho JOIN incidentes i "
            "ON i.id_incidente = d.id_incidente JOIN estados_incidente s "
            "ON s.id_estado_incidente = i.id_estado_incidente WHERE i.codigo LIKE %s "
            "AND s.codigo = 'CANCELADO' AND e.codigo IN ('CREADO', 'ACTIVO')",
            (self.prefix + "%",))[0][0], 0)

    def test_cancel_with_single_and_no_dispatch(self):
        iid = self.insert_incident("D1")
        d1 = self.add_dispatch(iid)
        # Motivo de exactamente 250 caracteres con contenido distinguible
        long_reason = ("Motivo-" + "0123456789" * 25)[:250]
        self.assertEqual(len(long_reason), 250)
        im.cancel_incident(iid, long_reason, self._user())
        incident_reason = db_all(
            "SELECT motivo_cancelacion FROM incidentes WHERE id_incidente = %s",
            (iid,))[0][0]
        dispatch_reason = db_all(
            "SELECT motivo_cancelacion FROM despachos WHERE id_despacho = %s",
            (d1,))[0][0]
        self.assertEqual(len(incident_reason), 250)
        self.assertEqual(incident_reason, long_reason)
        self.assertEqual(dispatch_reason, long_reason)
        self.assertEqual(dispatch_reason, incident_reason)
        empty = self.insert_incident("D0")
        im.cancel_incident(empty, "Motivo", self._user())
        self.assertEqual(self.dispatch_rows(empty), [])
        self.assertEqual(len(self.audit_rows(empty)), 1)

    def test_cancel_rejected_with_active_dispatch(self):
        iid = self.insert_incident("DA")
        created = self.add_dispatch(iid)
        self.add_dispatch(iid, "ACTIVO")
        before = (self.row(iid), self.dispatch_rows(iid))
        with self.assertRaises(im.IncidentStateError) as ctx:
            im.cancel_incident(iid, "Motivo", self._user())
        self.assertEqual(str(ctx.exception), "El incidente tiene despachos activos y no "
                         "puede cancelarse desde este módulo.")
        self.assertEqual((self.row(iid), self.dispatch_rows(iid)), before)
        self.assertEqual(self.dispatch_state(created), "CREADO")
        self.assertEqual(self.audit_rows(iid), [])
        self.assertEqual(self.dispatch_audit(created), [])

    def test_cancel_dispatch_rollback(self):
        iid = self.insert_incident("DR")
        d1 = self.add_dispatch(iid)
        d2 = self.add_dispatch(iid)
        before = (self.row(iid), self.dispatch_rows(iid))
        # falla la bitácora del despacho (usuario inexistente)
        with self.assertRaises(im.InvalidReferenceError):
            im.cancel_incident(iid, "Motivo", 999999)
        # falla la bitácora del incidente, después de resolver los despachos
        with mock.patch.object(im, "_audit", side_effect=mysql.connector.Error("falla")):
            with self.assertRaises(mysql.connector.Error):
                im.cancel_incident(iid, "Motivo", self._user())
        self.assertEqual((self.row(iid), self.dispatch_rows(iid)), before)
        self.assertEqual(self.state_of(iid), "REPORTADO")
        self.assertEqual(self.dispatch_state(d1), "CREADO")
        self.assertEqual(self.dispatch_state(d2), "CREADO")
        self.assertEqual(self.dispatch_audit(d1) + self.dispatch_audit(d2), [])
        self.assertEqual(self.audit_rows(iid), [])

    def test_cancel_invalid_reason_at_model(self):
        iid = self.insert_incident("RM")
        before = self.row(iid)
        for bad in ("", "   ", None, 5, "x" * 251):
            with self.assertRaises(im.InvalidIncidentDataError, msg=repr(bad)):
                im.cancel_incident(iid, bad, self._user())
        self.assertEqual(self.row(iid), before)
        self.assertEqual(self.audit_rows(iid), [])

    def test_cancel_lock_scope_is_limited_to_the_incident(self):
        from config.database import get_connection
        mine = self.insert_incident("LA")
        mine_dispatch = self.add_dispatch(mine)
        other = self.insert_incident("LB")
        other_dispatch = self.add_dispatch(other)
        conn_a = get_connection()
        conn_b = get_connection()
        try:
            cur_a = conn_a.cursor(dictionary=True)
            cur_b = conn_b.cursor()
            cur_b.execute("SET SESSION innodb_lock_wait_timeout = 1")
            # A recorre el camino de bloqueo de la cancelación sin confirmar
            self.assertIsNotNone(im._lock_incident(cur_a, mine))
            im._cancel_pending_dispatches(cur_a, mine, f"{self.prefix}-LA", "x", self._user())
            # B actualiza un despacho CREADO de otro incidente: no debe bloquearse
            cur_b.execute("UPDATE despachos SET observaciones = 'lock-test' "
                          "WHERE id_despacho = %s", (other_dispatch,))
            conn_b.commit()
            # control: el despacho del propio incidente sí está bloqueado
            with self.assertRaises(mysql.connector.Error) as ctx:
                cur_b.execute("UPDATE despachos SET observaciones = 'lock-test' "
                              "WHERE id_despacho = %s", (mine_dispatch,))
            self.assertEqual(ctx.exception.errno, 1205)
        finally:
            for conn in (conn_b, conn_a):
                try:
                    conn.rollback()
                finally:
                    conn.close()
        self.assertEqual(self.dispatch_state(mine_dispatch), "CREADO")
        self.assertEqual(self.dispatch_state(other_dispatch), "CREADO")

    def test_cancel_missing_incident(self):
        self.assertFalse(im.cancel_incident(0, "Motivo", self._user()))

    def test_cancel_rollback_when_audit_fails(self):
        iid = self._create()
        before = self.row(iid)
        with mock.patch.object(im, "_audit", side_effect=mysql.connector.Error("falla")):
            with self.assertRaises(mysql.connector.Error):
                im.cancel_incident(iid, "Motivo", self._user())
        self.assertEqual(self.row(iid), before)
        self.assertEqual(self.state_of(iid), "REPORTADO")
        with self.assertRaises(im.InvalidReferenceError):
            im.cancel_incident(iid, "Motivo", 999999)
        self.assertEqual(self.row(iid), before)
        self.assertEqual(len(self.audit_rows(iid)), 1)

    def test_no_arbitrary_state_function(self):
        for name in ("set_incident_state", "update_incident_state", "change_state"):
            self.assertFalse(hasattr(im, name), name)
        self.assertFalse(hasattr(im, "delete_incident"))


if __name__ == "__main__":
    unittest.main()

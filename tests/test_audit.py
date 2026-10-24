import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import mysql.connector

from controllers import audit_controller as ac
from controllers import dispatch_controller as dc
from controllers import incident_controller as ic
from controllers import unit_controller as uc
from controllers import user_controller as usc
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate, login
from models import audit_model as am
from models import dispatch_model as dm
from models import incident_model as im
from models import unit_model as um
from models import user_model
from tests._dispatch_support import DispatchTestBase, db_all, db_exec
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
ROOT = Path(__file__).resolve().parent.parent


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class AuditBase(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.admin_id = self.user_id("admin.demo")
        self.max_audit = db_all("SELECT COALESCE(MAX(id_bitacora), 0) FROM bitacora")[0][0]
        self.extra_audit_ids = []
        session.end_session()
        _login("admin.demo")

    def cleanup_steps(self):
        # Solo las filas capturadas por id; nunca borrados globales de la bitácora
        steps = [("DELETE FROM bitacora WHERE id_bitacora = %s", (i,))
                 for i in self.extra_audit_ids]
        return steps + super().cleanup_steps()

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def entity_audit(self, entity, entity_id):
        return db_all(
            "SELECT accion, detalle FROM bitacora WHERE entidad = %s AND id_entidad = %s "
            "ORDER BY id_bitacora", (entity, entity_id))

    def capture_new_rows(self, **conditions):
        """Ids de bitácora posteriores al inicio del test que cumplen las condiciones."""
        where = " AND ".join(f"{column} = %s" for column in conditions)
        rows = db_all(f"SELECT id_bitacora FROM bitacora WHERE id_bitacora > %s AND {where}",
                      (self.max_audit, *conditions.values()))
        ids = [r[0] for r in rows]
        self.extra_audit_ids.extend(ids)
        return ids


class AuditCoverageTest(AuditBase):
    # 1
    def test_login_is_audited_without_secrets(self):
        user = login("operador.central", DEMO_PASSWORD)
        ids = self.capture_new_rows(accion="LOGIN", id_usuario=user["user_id"])
        self.assertEqual(len(ids), 1)
        row = db_all("SELECT accion, entidad, id_entidad, id_usuario, detalle, fecha_hora "
                     "FROM bitacora WHERE id_bitacora = %s", (ids[0],))[0]
        self.assertEqual(row[:4], ("LOGIN", "USUARIO", user["user_id"], user["user_id"]))
        self.assertIsNotNone(row[5])
        for secret in (DEMO_PASSWORD, "bcrypt", "$2b$"):
            self.assertNotIn(secret, row[4])
        # Un intento fallido y authenticate() a secas no escriben nada
        from controllers.auth_controller import AccessDenied
        before = self.table_count("bitacora")
        with self.assertRaises(AccessDenied):
            login("operador.central", "incorrecta")
        authenticate("operador.central", DEMO_PASSWORD)
        self.assertEqual(self.table_count("bitacora"), before)

    def test_login_audit_failure_does_not_block(self):
        before = self.table_count("bitacora")
        with mock.patch.object(user_model, "record_login",
                               side_effect=mysql.connector.Error("fallo")):
            user = login("operador.central", DEMO_PASSWORD)
        self.assertEqual(user["username"], "operador.central")
        self.assertEqual(self.table_count("bitacora"), before)

    # 2-8, 9
    def test_official_flow_leaves_an_audit_trail(self):
        type_id, priority_id = self.seed_type, self.seed_priority
        incident = ic.create_incident({
            "code": f"{self.prefix}-AU", "type_id": type_id, "priority_id": priority_id,
            "address": "Calle 1", "description": "Descripción"})
        incident_id = incident["incident_id"]
        new_priority = self.temp_priority()
        ic.update_incident(incident_id, {
            "type_id": type_id, "priority_id": new_priority, "address": "Calle 2",
            "description": "Descripción"})
        draft = dc.create_dispatch_draft({"incident_id": incident_id})
        dispatch_id = draft["dispatch_id"]
        unit_id = self.temp_unit()
        dc.add_unit_to_draft(dispatch_id, unit_id)
        dc.activate_dispatch(dispatch_id)
        for operation in (dc.mark_unit_en_route, dc.mark_unit_on_scene, dc.start_unit_return):
            operation(dispatch_id, unit_id)
        ic.mark_incident_controlled(incident_id)
        ic.close_incident(incident_id)

        trail = self.entity_audit("INCIDENTE", incident_id)
        self.assertEqual([a for a, _ in trail],
                         ["CREAR", "MODIFICAR", "CAMBIO_ESTADO", "CAMBIO_ESTADO",
                          "CAMBIO_ESTADO", "CAMBIO_ESTADO"])
        details = [d for _, d in trail]
        # El cambio de prioridad es identificable en la entrada de modificación
        self.assertIn("id_prioridad", details[1])
        self.assertRegex(details[1], r"Prioridad: .+ → TIP-")
        self.assertIn("REPORTADO → ASIGNADO", details[2])
        self.assertIn("ASIGNADO → EN_ATENCION", details[3])
        self.assertEqual(details[4], "EN_ATENCION → CONTROLADO")
        self.assertEqual(details[5], "CONTROLADO → CERRADO")

        dispatch_trail = self.entity_audit("DESPACHO", dispatch_id)
        self.assertEqual([a for a, _ in dispatch_trail], ["CREAR", "CAMBIO_ESTADO", "CAMBIO_ESTADO"])
        self.assertIn("CREADO → ACTIVO", dispatch_trail[1][1])
        self.assertIn("ACTIVO → COMPLETADO", dispatch_trail[2][1])
        unit_trail = [d for _, d in self.entity_audit("UNIDAD", unit_id)]
        self.assertEqual(len(unit_trail), 3)
        self.assertIn("DESPACHADA → EN_RUTA", unit_trail[0])
        self.assertIn("EN_RUTA → EN_ESCENA", unit_trail[1])
        self.assertIn("EN_ESCENA → REGRESANDO", unit_trail[2])

    def test_update_without_priority_change_and_noop(self):
        incident = ic.create_incident({
            "code": f"{self.prefix}-NP", "type_id": self.seed_type,
            "priority_id": self.seed_priority, "address": "Calle 1",
            "description": "Descripción"})
        incident_id = incident["incident_id"]
        data = {"type_id": self.seed_type, "priority_id": self.seed_priority,
                "address": "Otra", "description": "Descripción"}
        ic.update_incident(incident_id, data)
        ic.update_incident(incident_id, data)  # sin cambios: no audita
        trail = self.entity_audit("INCIDENTE", incident_id)
        self.assertEqual(len(trail), 2)
        self.assertEqual(trail[1][1], "Campos modificados: direccion.")

    def test_cancel_failure_and_maintenance_are_audited(self):
        incident_id = self.insert_incident("CF")
        dispatch_id = dm.create_dispatch_draft(incident_id, None, self.admin_id)
        failing, cancelled = self.temp_unit(), self.temp_unit()
        for unit_id in (failing, cancelled):
            dm.add_unit_to_draft(dispatch_id, unit_id)
        dm.activate_dispatch(dispatch_id, self.admin_id)
        dc.report_unit_failure(dispatch_id, failing, "Motor averiado")
        self.assertIn("por falla", self.entity_audit("UNIDAD", failing)[-1][1])
        dc.cancel_active_dispatch(dispatch_id, "Cancelado por prueba")
        self.assertIn("ACTIVO → CANCELADO", self.entity_audit("DESPACHO", dispatch_id)[-1][1])
        self.assertIn("por cancelación", self.entity_audit("UNIDAD", cancelled)[-1][1])
        spare = self.temp_unit()
        uc.send_unit_to_maintenance(spare)
        uc.return_unit_to_service(spare)
        self.assertEqual([d for _, d in self.entity_audit("UNIDAD", spare)],
                         ["DISPONIBLE → MANTENIMIENTO.", "MANTENIMIENTO → DISPONIBLE."])
        cancel_target = self.insert_incident("CI")
        ic.cancel_incident(cancel_target, "Falsa alarma")
        self.assertIn("REPORTADO → CANCELADO", self.entity_audit("INCIDENTE", cancel_target)[-1][1])

    # 10
    def test_business_and_audit_roll_back_together(self):
        incident_id = self.insert_incident("RB", "CONTROLADO")
        before = (self.row(incident_id), self.entity_audit("INCIDENTE", incident_id))
        with mock.patch.object(im, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(ic.IncidentServiceError):
                ic.close_incident(incident_id)
        self.assertEqual((self.row(incident_id), self.entity_audit("INCIDENTE", incident_id)),
                         before)
        draft_incident = self.insert_incident("RA")
        dispatch_id = dm.create_dispatch_draft(draft_incident, None, self.admin_id)
        unit_id = self.temp_unit()
        dm.add_unit_to_draft(dispatch_id, unit_id)
        unit_before = self.unit_row(unit_id)
        audit_before = [self.entity_audit(e, i) for e, i in (
            ("DESPACHO", dispatch_id), ("UNIDAD", unit_id), ("INCIDENTE", draft_incident))]
        with mock.patch.object(dm, "_audit", side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(dc.DispatchServiceError):
                dc.activate_dispatch(dispatch_id)
        self.assertEqual(self.dispatch_state(dispatch_id), "CREADO")
        self.assertEqual(self.unit_row(unit_id), unit_before)
        self.assertEqual([self.entity_audit(e, i) for e, i in (
            ("DESPACHO", dispatch_id), ("UNIDAD", unit_id), ("INCIDENTE", draft_incident))],
            audit_before)


class SensitiveDataTest(AuditBase):
    def setUp(self):
        super().setUp()
        self.user_prefix = f"tua{uuid4().hex[:8]}"

    def cleanup_steps(self):
        steps = [("DELETE FROM bitacora WHERE id_usuario IN "
                  "(SELECT id_usuario FROM usuarios WHERE nombre_usuario LIKE %s) "
                  "AND id_bitacora > %s", (self.user_prefix + "%", self.max_audit)),
                 ("DELETE FROM bitacora WHERE entidad = 'USUARIO' AND id_entidad IN "
                  "(SELECT id_usuario FROM usuarios WHERE nombre_usuario LIKE %s) "
                  "AND id_bitacora > %s", (self.user_prefix + "%", self.max_audit))]
        steps += super().cleanup_steps()
        steps.append(("DELETE FROM usuarios WHERE nombre_usuario LIKE %s",
                      (self.user_prefix + "%",)))
        return steps

    def test_user_events_never_store_secrets(self):
        password, new_password = "Secreto!2026x", "OtraClave!2027y"
        role_id = usc.list_roles()[0]["role_id"]
        user = usc.create_user({
            "username": f"{self.user_prefix}a", "first_name": "Temp", "last_name": "Audit",
            "email": None, "role_id": role_id, "password": password})
        usc.update_user(user["user_id"], {
            "username": f"{self.user_prefix}a", "first_name": "Temp", "last_name": "Audit",
            "email": None, "role_id": role_id, "password": new_password})
        usc.deactivate_user(user["user_id"])
        usc.activate_user(user["user_id"])
        hash_value = db_all("SELECT password_hash FROM usuarios WHERE id_usuario = %s",
                            (user["user_id"],))[0][0]
        login(f"{self.user_prefix}a", new_password)
        rows = db_all("SELECT detalle FROM bitacora WHERE id_bitacora > %s", (self.max_audit,))
        self.assertTrue(rows)
        for (detail,) in rows:
            text = detail or ""
            for secret in (password, new_password, "bcrypt", "$2b$", hash_value):
                self.assertNotIn(secret, text)
        # Ni siquiera el esquema de la entrada del login menciona credenciales
        login_rows = db_all("SELECT detalle FROM bitacora WHERE accion = 'LOGIN' "
                            "AND id_bitacora > %s", (self.max_audit,))
        self.assertEqual(len(login_rows), 1)


class AuditQueryTest(AuditBase):
    def setUp(self):
        super().setUp()
        self.marker = uuid4().hex[:8]
        self.action = f"ZZA{self.marker}"
        self.entity = f"ZZE{self.marker}"
        self.other_action = f"ZZB{self.marker}"

    def add(self, when, user_id=None, action=None, entity=None, entity_id=7, detail="temp"):
        audit_id = db_exec(
            "INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle, fecha_hora) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, action or self.action, entity or self.entity, entity_id, detail, when))
        self.extra_audit_ids.append(audit_id)
        return audit_id

    def search(self, **filters):
        filters.setdefault("entity", self.entity)
        return ac.search_audit(filters)

    # 11, 12, 13
    def test_order_join_and_null_user(self):
        first = self.add("2001-01-01 10:00:00", self.admin_id)
        tie_a = self.add("2001-01-02 09:00:00", None)
        tie_b = self.add("2001-01-02 09:00:00", self.admin_id)
        result = self.search()
        self.assertEqual([r["audit_id"] for r in result["rows"]], [tie_b, tie_a, first])
        self.assertFalse(result["truncated"])
        by_id = {r["audit_id"]: r for r in result["rows"]}
        self.assertEqual(by_id[first]["username"], "admin.demo")
        self.assertEqual(by_id[first]["user_id"], self.admin_id)
        self.assertIsNone(by_id[tie_a]["username"])
        self.assertIsNone(by_id[tie_a]["user_id"])
        self.assertEqual(set(by_id[first]), {
            "audit_id", "occurred_at", "user_id", "username", "action", "entity", "entity_id",
            "detail"})
        # Sin filtros devuelve la bitácora real, de la más reciente a la más antigua
        everything = ac.search_audit()
        stamps = [(r["occurred_at"], r["audit_id"]) for r in everything["rows"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertLessEqual(len(everything["rows"]), am.AUDIT_LIMIT)

    # 14, 15
    def test_date_filters(self):
        a = self.add("2001-01-01 10:00:00")
        b = self.add("2001-01-02 23:59:59")
        c = self.add("2001-01-03 00:00:00")
        ids = lambda **f: {r["audit_id"] for r in self.search(**f)["rows"]}  # noqa: E731
        self.assertEqual(ids(date_from="2001-01-02"), {b, c})
        self.assertEqual(ids(date_to="2001-01-02"), {a, b})  # inclusivo por día completo
        self.assertEqual(ids(date_from="2001-01-02", date_to="2001-01-02"), {b})
        self.assertEqual(ids(date_from="", date_to=None), {a, b, c})

    # 16-19
    def test_user_action_entity_and_combined(self):
        mine = self.add("2001-02-01 10:00:00", self.admin_id)
        other_user = self.add("2001-02-01 11:00:00", self.user_id("operador.central"))
        other_action = self.add("2001-02-01 12:00:00", self.admin_id, action=self.other_action)
        ids = lambda **f: {r["audit_id"] for r in self.search(**f)["rows"]}  # noqa: E731
        self.assertEqual(ids(username="ADMIN.d"), {mine, other_action})
        self.assertEqual(ids(username="operador"), {other_user})
        self.assertEqual(ids(action=self.action), {mine, other_user})
        self.assertEqual(ids(action=self.other_action), {other_action})
        self.assertEqual(ids(action=self.action[:-1]), set())  # acción exacta
        self.assertEqual(ids(username="admin", action=self.action), {mine})
        self.assertEqual(ids(username="admin", action=self.action, date_from="2001-02-01",
                             date_to="2001-02-01"), {mine})
        elsewhere = ac.search_audit({"entity": self.entity + "x"})
        self.assertEqual(elsewhere["rows"], [])

    # 20
    def test_sql_is_parameterized(self):
        self.add("2001-03-01 10:00:00", self.admin_id)
        for payload in ("' OR 1=1 --", "x'; DROP TABLE bitacora; --", "%", "_", "\\"):
            for key in ("username", "action", "entity"):
                self.assertEqual(ac.search_audit({key: payload})["rows"], [], (key, payload))
        # Los comodines del usuario se escapan: '%' no coincide con todos
        self.assertEqual(ac.search_audit({"username": "%"})["rows"], [])
        self.assertEqual(ac.search_audit({"username": "admin_demo"})["rows"], [])
        self.assertTrue(db_all("SELECT COUNT(*) FROM bitacora")[0][0] > 0)

    def test_truncation_flag(self):
        for n in range(3):
            self.add(f"2001-04-0{n + 1} 10:00:00")
        with mock.patch.object(am, "AUDIT_LIMIT", 2):
            result = self.search()
            self.assertTrue(result["truncated"])
            self.assertEqual(len(result["rows"]), 2)
        with mock.patch.object(am, "AUDIT_LIMIT", 3):
            result = self.search()
            self.assertFalse(result["truncated"])
            self.assertEqual(len(result["rows"]), 3)

    def test_invalid_filters_and_service_error(self):
        for bad in ({"date_from": "abc"}, {"date_to": 5}, {"username": 3}, {"action": []},
                    {"unknown": 1}, "texto",
                    {"date_from": "2001-02-02", "date_to": "2001-02-01"}):
            with self.assertRaises(ac.AuditValidationError, msg=bad):
                ac.search_audit(bad)
        failing = mysql.connector.Error("SELECT secreto FROM tabla")
        for function, args in ((ac.search_audit, ()), (ac.list_audit_filter_options, ())):
            with mock.patch.object(
                    am, "search_audit" if function is ac.search_audit
                    else "list_audit_filter_options", side_effect=failing):
                with self.assertRaises(ac.AuditServiceError) as ctx:
                    function(*args)
            self.assertNotIn("SELECT", str(ctx.exception))

    def test_filter_options(self):
        self.add("2001-05-01 10:00:00", self.admin_id)
        options = ac.list_audit_filter_options()
        self.assertIn(self.action, options["actions"])
        self.assertIn(self.entity, options["entities"])
        self.assertIn("admin.demo", options["usernames"])
        self.assertEqual(options["actions"], sorted(options["actions"]))

    # permisos
    def test_permissions(self):
        for username in ("operador.central", "consulta.sur"):
            session.end_session()
            _login(username)
            with self.assertRaises(AuthorizationDenied):
                ac.search_audit()
            with self.assertRaises(AuthorizationDenied):
                ac.list_audit_filter_options()
            with self.assertRaises(AuthorizationDenied):
                ac.search_audit({"date_from": "mal"})  # autorización antes que validación
        session.end_session()
        with self.assertRaises(NotAuthenticated):
            ac.search_audit()
        with self.assertRaises(NotAuthenticated):
            ac.list_audit_filter_options()
        _login("admin.demo")
        self.assertIn("rows", ac.search_audit({"entity": self.entity}))

    # solo lectura
    def test_read_only_source(self):
        for relative in ("models/audit_model.py", "controllers/audit_controller.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for forbidden in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE", "FOR SHARE",
                              "commit(", "start_transaction"):
                self.assertNotIn(forbidden, source, relative)
        before = db_all("SELECT COUNT(*), MAX(id_bitacora) FROM bitacora")[0]
        ac.search_audit({"entity": self.entity})
        ac.list_audit_filter_options()
        self.assertEqual(db_all("SELECT COUNT(*), MAX(id_bitacora) FROM bitacora")[0], before)


class UnitAdminAuditTest(AuditBase):
    def fields(self, unit_id, **overrides):
        unit = um.get_unit(unit_id)
        values = {"code": unit["code"], "name": unit["name"], "plate": unit["plate"],
                  "capacity": unit["capacity"], "description": unit["description"],
                  "station_id": unit["station_id"], "type_id": unit["type_id"]}
        values.update(overrides)
        return values

    def unit_trail(self, unit_id):
        return db_all(
            "SELECT accion, detalle, id_usuario FROM bitacora WHERE entidad = 'UNIDAD' "
            "AND id_entidad = %s ORDER BY id_bitacora", (unit_id,))

    def station_code(self, station_id):
        return db_all("SELECT codigo FROM estaciones WHERE id_estacion = %s",
                      (station_id,))[0][0]

    # (a)
    def test_administrative_reactivation_is_audited(self):
        unit_id = self.temp_unit()
        uc.deactivate_unit(unit_id)
        uc.activate_unit(unit_id)
        trail = self.unit_trail(unit_id)
        self.assertEqual([t[0] for t in trail], ["DESACTIVAR", "REACTIVAR"])
        self.assertEqual(trail[1][1], "Reactivación administrativa (activo: 0 → 1). La unidad "
                         "permanece en FUERA_DE_SERVICIO hasta un retorno a servicio explícito.")
        self.assertEqual(trail[1][2], self.admin_id)
        self.assertEqual(self.unit_state(unit_id), "FUERA_DE_SERVICIO")
        uc.activate_unit(unit_id)  # ya activa: sin cambios ni fila nueva
        self.assertEqual(len(self.unit_trail(unit_id)), 2)

    # (b)
    def test_update_is_audited_once_with_changed_fields_only(self):
        station_a, station_b = self.temp_station(), self.temp_station()
        unit_id = self.temp_unit(station_id=station_a)
        um.update_unit(unit_id, self.fields(unit_id, station_id=station_b, name="Nueva"),
                       self.admin_id)
        trail = self.unit_trail(unit_id)
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0][0], "MODIFICAR")
        self.assertEqual(trail[0][1], "Campos modificados: nombre, id_estacion. "
                         f"Traslado de estación: {self.station_code(station_a)} → "
                         f"{self.station_code(station_b)}.")
        self.assertEqual(trail[0][2], self.admin_id)
        # Edición de un solo campo: una entrada con solo ese campo
        uc.update_unit(unit_id, {**self.fields(unit_id), "plate": f"{self.plate_prefix}-9"})
        trail = self.unit_trail(unit_id)
        self.assertEqual(len(trail), 2)
        self.assertEqual(trail[1][1], "Campos modificados: placa.")
        # Sin cambios, o rechazada, no deja entrada
        um.update_unit(unit_id, self.fields(unit_id), self.admin_id)
        inactive = self.temp_station()
        db_exec("UPDATE estaciones SET activo = 0 WHERE id_estacion = %s", (inactive,))
        with self.assertRaises(um.InactiveStationError):
            um.update_unit(unit_id, self.fields(unit_id, station_id=inactive), self.admin_id)
        self.assertEqual(len(self.unit_trail(unit_id)), 2)

    # (c)
    def test_unit_admin_audit_rolls_back_with_the_change(self):
        station_a, station_b = self.temp_station(), self.temp_station()
        unit_id = self.temp_unit(station_id=station_a)
        real_write = um._write

        def failing_after(operation):
            def wrapped(cursor):
                operation(cursor)
                raise RuntimeError("fallo tras la bitácora")
            return real_write(wrapped)

        before = (self.unit_row(unit_id), self.unit_trail(unit_id))
        with mock.patch.object(um, "_write", failing_after):
            with self.assertRaises(RuntimeError):
                um.update_unit(unit_id, self.fields(unit_id, station_id=station_b), self.admin_id)
        self.assertEqual((self.unit_row(unit_id), self.unit_trail(unit_id)), before)
        uc.deactivate_unit(unit_id)
        before = (self.unit_row(unit_id), self.unit_trail(unit_id))
        with mock.patch.object(um, "_write", failing_after):
            with self.assertRaises(RuntimeError):
                um.set_unit_active(unit_id, True, self.admin_id)
        self.assertEqual((self.unit_row(unit_id), self.unit_trail(unit_id)), before)
        # Y con el auxiliar de bitácora fallando de verdad
        with mock.patch.object(um, "_audit_modification",
                               side_effect=mysql.connector.Error("fallo")):
            with self.assertRaises(mysql.connector.Error):
                um.update_unit(unit_id, self.fields(unit_id, station_id=station_b), self.admin_id)
        self.assertEqual((self.unit_row(unit_id), self.unit_trail(unit_id)), before)
        with self.assertRaises(um.InvalidUnitDataError):
            um.update_unit(unit_id, self.fields(unit_id), True)


if __name__ == "__main__":
    unittest.main()

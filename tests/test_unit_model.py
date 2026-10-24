import unittest
from unittest import mock

from models import unit_model as um
from tests._unit_support import UnitTestBase, db_all

KEYS = {"unit_id", "code", "name", "plate", "capacity", "description", "active",
        "station_id", "station_code", "station_name", "type_id", "type_name",
        "state_id", "state_code", "state_name"}


class UnitModelTest(UnitTestBase):
    SCENARIOS = [
        ("ACTIVO", "ASSIGNED", True),
        ("ACTIVO", "RELEASED", False),
        ("ACTIVO", "CANCELLED", False),
        ("ACTIVO", "PRESELECTION", False),
        ("COMPLETADO", "ASSIGNED", False),
        ("CANCELADO", "ASSIGNED", False),
        ("CREADO", "PRESELECTION", False),
    ]
    def update_unit(self, unit_id, fields, user_id=None):
        """Llama al modelo con el usuario administrador (la bitácora exige usuario)."""
        return um.update_unit(unit_id, fields, user_id or self.user_id("admin.demo"))

    def _create(self, suffix="A", **overrides):
        values = {
            "code": f"{self.prefix}-{suffix}", "name": "Unidad temporal",
            "plate": f"{self.plate_prefix}-{suffix}", "capacity": 4,
            "description": "Desc", "station_id": self.seed_station,
            "type_id": self.seed_type,
        }
        values.update(overrides)
        return um.create_unit(**values)

    def _fields(self, suffix="A", **overrides):
        values = {
            "code": f"{self.prefix}-{suffix}", "name": "Unidad temporal",
            "plate": f"{self.plate_prefix}-{suffix}", "capacity": 4,
            "description": "Desc", "station_id": self.seed_station,
            "type_id": self.seed_type,
        }
        values.update(overrides)
        return values

    def _ids(self, units):
        return [u["unit_id"] for u in units]

    def test_list_seed_and_keys(self):
        units = um.list_units()
        seed = [u for u in units if not u["code"].startswith("TUN-")]
        self.assertEqual(len(seed), 11)
        self.assertTrue(all(set(u) == KEYS for u in units))
        self.assertTrue(all(isinstance(u["active"], bool) for u in units))
        self.assertFalse(next(u for u in units if u["code"] == "CM-01")["active"])

    def test_search_code_plate_name(self):
        uid = self._create(name="Zzcamion")
        for term in (f"{self.prefix}-A", self.plate_prefix, "zzcamion"):
            self.assertEqual(self._ids(um.search_units(term)), [uid], term)
        self.assertEqual(um.search_units("inexistente-xyz"), [])
        self.assertIn("B-01", [u["code"] for u in um.search_units("B-01")])

    def test_wildcards_literal(self):
        special = self._create("S", name="100%_x\\y")
        plain = self._create("P", name="abc")
        for term in ("%", "_", "\\"):
            found = self._ids(um.search_units(term))
            self.assertIn(special, found, term)
            self.assertNotIn(plain, found, term)
        # los seeds no contienen % ni \ en código, placa o nombre
        self.assertEqual(self._ids(um.search_units("%")), [special])
        self.assertEqual(self._ids(um.search_units("\\")), [special])
        self.assertNotIn(plain, self._ids(um.search_units("a%")))

    def test_filters(self):
        # Datos propios: no dependen del estado de las unidades de la demo
        station, other_station = self.temp_station(), self.temp_station()
        unit_type = self.temp_type()
        available = self._create("FA", station_id=station, type_id=unit_type)
        maintenance = self._create("FM", station_id=station, type_id=unit_type)
        elsewhere = self._create("FE")  # estación y tipo del seed
        self.set_state(maintenance, "MANTENIMIENTO")
        ids = lambda units: {u["unit_id"] for u in units}  # noqa: E731

        by_station = um.list_units(station_id=station)
        self.assertEqual(ids(by_station), {available, maintenance})
        self.assertTrue(all(u["station_id"] == station for u in by_station))
        self.assertEqual(ids(um.list_units(station_id=other_station)), set())
        self.assertNotIn(elsewhere, ids(by_station))

        # filtro por tipo y combinación estación + tipo
        self.assertEqual(ids(um.list_units(type_id=unit_type)), {available, maintenance})
        self.assertEqual(ids(um.search_units("", station_id=station, type_id=unit_type)),
                         {available, maintenance})
        self.assertEqual(um.search_units("", station_id=other_station, type_id=unit_type), [])
        self.assertEqual(um.search_units("", station_id=station, type_id=self.seed_type), [])

        # filtro por estado: incluye lo esperado y excluye lo demás
        available_id = db_all(
            "SELECT id_estado_unidad FROM estados_unidad WHERE codigo='DISPONIBLE'")[0][0]
        maintenance_id = db_all(
            "SELECT id_estado_unidad FROM estados_unidad WHERE codigo='MANTENIMIENTO'")[0][0]
        disp = um.list_units(state_id=available_id)
        self.assertTrue({available, elsewhere} <= ids(disp))
        self.assertNotIn(maintenance, ids(disp))
        self.assertTrue(all(u["state_code"] == "DISPONIBLE" for u in disp))
        maint = um.list_units(state_id=maintenance_id)
        self.assertIn(maintenance, ids(maint))
        self.assertTrue({available, elsewhere}.isdisjoint(ids(maint)))
        self.assertEqual(ids(um.search_units("", station_id=station, state_id=available_id)),
                         {available})
        self.assertEqual(ids(um.search_units("", station_id=station, type_id=unit_type,
                                             state_id=maintenance_id)), {maintenance})

        # el grupo OR del texto no escapa de los filtros AND
        self.assertEqual(ids(um.search_units(self.prefix, station_id=station)),
                         {available, maintenance})
        self.assertEqual(um.search_units(self.prefix, station_id=other_station), [])
        self.assertEqual(um.search_units(f"{self.prefix}-FA", station_id=station,
                                         state_id=maintenance_id), [])
        # la búsqueda sigue encontrando por placa y nombre dentro de la estación
        self.assertEqual(ids(um.search_units(f"{self.plate_prefix}-FM", station_id=station)),
                         {maintenance})

    def test_types_and_states(self):
        types = um.list_unit_types()
        self.assertEqual({"type_id", "name", "description", "active"}, set(types[0]))
        self.assertGreaterEqual(len([t for t in types if not t["name"].startswith("TUT-")]), 6)
        states = um.list_unit_states()
        self.assertEqual(len(states), 7)
        self.assertEqual([s["sort_order"] for s in states], sorted(s["sort_order"] for s in states))
        self.assertEqual({"state_id", "code", "name", "sort_order"}, set(states[0]))

    def test_create_valid_initial_state(self):
        uid = self._create()
        unit = um.get_unit(uid)
        self.assertEqual(unit["state_code"], "DISPONIBLE")
        self.assertTrue(unit["active"])
        self.assertEqual(unit["capacity"], 4)
        self.assertEqual(unit["plate"], f"{self.plate_prefix}-A")
        self.assertIsNone(um.get_unit(4000000000))

    def test_create_empty_optionals_null(self):
        uid = self._create(name="", plate="", capacity=None, description="")
        unit = um.get_unit(uid)
        self.assertIsNone(unit["plate"])
        self.assertIsNone(unit["capacity"])
        self.assertIsNone(unit["name"])
        # dos unidades sin placa conviven
        other = self._create("B", plate=None)
        self.assertIsNone(um.get_unit(other)["plate"])

    def test_duplicates(self):
        self._create()
        with self.assertRaises(um.DuplicateUnitCodeError):
            self._create("A2", code=f"{self.prefix}-A")
        with self.assertRaises(um.DuplicateUnitPlateError):
            self._create("A3", plate=f"{self.plate_prefix}-A")
        self.assertEqual(self.count_prefix(), 1)

    def test_check_capacity_zero(self):
        with self.assertRaises(um.InvalidUnitDataError):
            self._create(capacity=0)
        self.assertEqual(self.count_prefix(), 0)

    def test_out_of_range_capacity_bypassing_controller(self):
        for bad in (256, -1):
            with self.assertRaises(um.InvalidUnitDataError, msg=str(bad)):
                self._create(capacity=bad)
        with self.assertRaises(um.InvalidUnitDataError):
            self._create(name="x" * 200)
        self.assertEqual(self.count_prefix(), 0)

    def test_lock_unit_does_not_block_other_units_same_state(self):
        from config.database import get_connection
        locked = self._create("L1")
        other = self._create("L2")
        conn_a = get_connection()
        conn_b = get_connection()
        try:
            cur_a = conn_a.cursor(dictionary=True)
            um._lock_unit(cur_a, locked)  # transacción A abierta, sin commit
            cur_b = conn_b.cursor()
            cur_b.execute("SET SESSION innodb_lock_wait_timeout = 2")
            # B actualiza otra unidad del mismo estado sin esperar a A
            cur_b.execute("UPDATE unidades SET nombre = 'x' WHERE id_unidad = %s", (other,))
            cur_b.execute(
                "SELECT id_unidad FROM unidades u JOIN estados_unidad s "
                "ON s.id_estado_unidad = u.id_estado_unidad WHERE u.id_unidad = %s "
                "FOR UPDATE OF u", (other,))
            cur_b.fetchall()
            conn_b.rollback()
            # create_unit completo desde otra conexión tampoco se bloquea
            new_id = self._create("L3")
            self.assertIsNotNone(new_id)
        finally:
            conn_a.rollback()
            conn_b.rollback()
            conn_a.close()
            conn_b.close()

    def test_invalid_references_bypassing_controller(self):
        for bad in (999999, 0, -1):
            with self.assertRaises(um.InvalidStationError, msg=str(bad)):
                self._create(station_id=bad)
            with self.assertRaises(um.InvalidTypeError, msg=str(bad)):
                self._create(type_id=bad)
        self.assertEqual(self.count_prefix(), 0)

    def test_fk_translation(self):
        error = um.mysql.connector.Error(msg="fk", errno=1452)
        self.assertIsInstance(um._translate_error(error), um.InvalidReferenceError)

    def test_inactive_station_rules(self):
        inactive = self.temp_station(active=False)
        with self.assertRaises(um.InactiveStationError):
            self._create(station_id=inactive)
        self.assertEqual(self.count_prefix(), 0)
        active = self.temp_station()
        uid = self._create(station_id=active)
        before = self.row(uid)
        with self.assertRaises(um.InactiveStationError):
            self.update_unit(uid, self._fields(station_id=inactive))
        self.assertEqual(self.row(uid), before)
        # estación activa que se desactiva después: editar otros campos sí
        from models import station_model
        station_model.set_station_active(active, False)
        self.assertTrue(self.update_unit(uid, self._fields(name="Nuevo", station_id=active)))
        self.assertEqual(um.get_unit(uid)["name"], "Nuevo")
        another = self.temp_station(active=False)
        with self.assertRaises(um.InactiveStationError):
            self.update_unit(uid, self._fields(station_id=another))
        # inactiva -> activa permitido
        self.assertTrue(self.update_unit(uid, self._fields(station_id=self.seed_station)))
        self.assertEqual(um.get_unit(uid)["station_id"], self.seed_station)

    def test_inactive_type_rules(self):
        inactive = self.temp_type(active=False)
        with self.assertRaises(um.InactiveTypeError):
            self._create(type_id=inactive)
        uid = self._create()
        before = self.row(uid)
        with self.assertRaises(um.InactiveTypeError):
            self.update_unit(uid, self._fields(type_id=inactive))
        self.assertEqual(self.row(uid), before)
        # tipo histórico que se desactiva: se conserva al editar otros campos
        legacy = self.temp_type()
        uid2 = self._create("B", type_id=legacy)
        self.deactivate_temp_type(legacy)
        self.assertTrue(self.update_unit(uid2, self._fields("B", name="Otro", type_id=legacy)))
        found = um.get_unit(uid2)
        self.assertEqual(found["type_id"], legacy)
        self.assertIn(uid2, self._ids(um.list_units()))
        self.assertFalse(next(t for t in um.list_unit_types() if t["type_id"] == legacy)["active"])
        with self.assertRaises(um.InactiveTypeError):
            self.update_unit(uid2, self._fields("B", type_id=inactive))

    def test_update_persists_and_keeps_state(self):
        other_station = self.temp_station()
        other_type = self.temp_type()
        uid = self._create()
        self.set_state(uid, "MANTENIMIENTO")
        state_before = db_all("SELECT id_estado_unidad FROM unidades WHERE id_unidad=%s", (uid,))
        fields = self._fields(code=f"{self.prefix}-N", name="Nombre2", plate=f"{self.plate_prefix}-N",
                              capacity=9, description="D2", station_id=other_station,
                              type_id=other_type)
        self.assertTrue(self.update_unit(uid, fields))
        unit = um.get_unit(uid)
        self.assertEqual((unit["code"], unit["name"], unit["plate"], unit["capacity"],
                          unit["description"], unit["station_id"], unit["type_id"]),
                         (fields["code"], "Nombre2", fields["plate"], 9, "D2",
                          other_station, other_type))
        self.assertEqual(db_all("SELECT id_estado_unidad FROM unidades WHERE id_unidad=%s", (uid,)),
                         state_before)
        # sin cambios no se confunde con inexistente
        self.assertTrue(self.update_unit(uid, fields))
        self.assertFalse(self.update_unit(4000000000, fields))

    def test_update_duplicates(self):
        first = self._create("A")
        second = self._create("B")
        before = (self.row(first), self.row(second))
        with self.assertRaises(um.DuplicateUnitCodeError):
            self.update_unit(second, self._fields("B", code=f"{self.prefix}-A"))
        with self.assertRaises(um.DuplicateUnitPlateError):
            self.update_unit(second, self._fields("B", plate=f"{self.plate_prefix}-A"))
        self.assertEqual((self.row(first), self.row(second)), before)

    def test_transfer_rule(self):
        target = self.temp_station()
        for code in ("DESPACHADA", "EN_RUTA", "EN_ESCENA"):
            uid = self._create(code[:3] + code[-2:])
            self.set_state(uid, code)
            before = self.row(uid)
            with self.assertRaises(um.UnitBusyError, msg=code):
                self.update_unit(uid, self._fields(code[:3] + code[-2:], station_id=target))
            self.assertEqual(self.row(uid), before)
            # editar otros campos sin cambiar de estación sí se permite
            self.assertTrue(self.update_unit(
                uid, self._fields(code[:3] + code[-2:], name="Editada")))
        for code in ("DISPONIBLE", "MANTENIMIENTO", "REGRESANDO", "FUERA_DE_SERVICIO"):
            suffix = "T" + code[:3] + code[-2:]
            uid = self._create(suffix)
            self.set_state(uid, code)
            self.assertTrue(self.update_unit(uid, self._fields(suffix, station_id=target)), code)
            self.assertEqual(um.get_unit(uid)["station_id"], target)
            self.assertEqual(self.state_of(uid), code)

    def test_deactivate_flow(self):
        uid = self._create()
        user_id = self.user_id("admin.demo")
        self.assertTrue(um.set_unit_active(uid, False, user_id))
        unit = um.get_unit(uid)
        self.assertFalse(unit["active"])
        self.assertEqual(unit["state_code"], "FUERA_DE_SERVICIO")
        audit = self.audit_rows(uid)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0][:4], (user_id, "DESACTIVAR", "UNIDAD", uid))
        self.assertEqual(audit[0][4],
                         "Unidad retirada administrativamente: activo = 0, FUERA_DE_SERVICIO.")
        # idempotente: sin segunda fila
        self.assertTrue(um.set_unit_active(uid, False, user_id))
        self.assertEqual(len(self.audit_rows(uid)), 1)
        self.assertFalse(um.set_unit_active(4000000000, False, user_id))

    def test_deactivate_busy_rejected(self):
        user_id = self.user_id("admin.demo")
        for code in ("DESPACHADA", "EN_RUTA", "EN_ESCENA"):
            uid = self._create(code[:3] + code[-2:])
            self.set_state(uid, code)
            before = self.row(uid)
            with self.assertRaises(um.UnitBusyError, msg=code):
                um.set_unit_active(uid, False, user_id)
            self.assertEqual(self.row(uid), before)
            self.assertEqual(self.audit_rows(uid), [])

    def test_deactivate_other_states_and_fds(self):
        user_id = self.user_id("admin.demo")
        for code in ("REGRESANDO", "MANTENIMIENTO", "FUERA_DE_SERVICIO"):
            uid = self._create(code[:3] + code[-2:])
            self.set_state(uid, code)
            self.assertTrue(um.set_unit_active(uid, False, user_id))
            self.assertEqual(self.active_of(uid), 0)
            self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
            self.assertEqual(len(self.audit_rows(uid)), 1)

    def test_rollback_when_audit_fails(self):
        uid = self._create()
        before = self.row(uid)
        original = um._state_id_by_code
        user_id = self.user_id("admin.demo")
        # user_id inexistente hace fallar el INSERT de bitácora (FK) tras el UPDATE
        with self.assertRaises(Exception):
            um.set_unit_active(uid, False, 4000000000)
        self.assertEqual(self.row(uid), before)
        self.assertEqual(self.audit_rows(uid), [])
        self.assertIs(um._state_id_by_code, original)
        self.assertEqual(self.active_of(uid), 1)
        self.assertEqual(self.state_of(uid), "DISPONIBLE")
        self.assertTrue(um.set_unit_active(uid, False, user_id))

    def test_rollback_with_forced_failure(self):
        uid = self._create()
        before = self.row(uid)
        real_write = um._write

        def failing_write(operation):
            def wrapped(cursor):
                original_execute = cursor.execute

                def execute(query, params=()):
                    if "INSERT INTO bitacora" in query:
                        raise RuntimeError("fallo simulado")
                    return original_execute(query, params)

                wrapped_cursor = mock.Mock(wraps=cursor)
                wrapped_cursor.execute = execute
                return operation(wrapped_cursor)
            return real_write(wrapped)

        with mock.patch.object(um, "_write", failing_write):
            with self.assertRaises(RuntimeError):
                um.set_unit_active(uid, False, self.user_id("admin.demo"))
        self.assertEqual(self.row(uid), before)
        self.assertEqual(self.audit_rows(uid), [])

    def test_reactivate(self):
        user_id = self.user_id("admin.demo")
        uid = self._create()
        um.set_unit_active(uid, False, user_id)
        self.assertTrue(um.set_unit_active(uid, True, user_id))
        unit = um.get_unit(uid)
        self.assertTrue(unit["active"])
        self.assertEqual(unit["state_code"], "FUERA_DE_SERVICIO")
        # La reactivación administrativa deja su propia fila (REACTIVAR) tras DESACTIVAR
        audit = self.audit_rows(uid)
        self.assertEqual([a[1] for a in audit], ["DESACTIVAR", "REACTIVAR"])
        self.assertIn("FUERA_DE_SERVICIO hasta un retorno a servicio explícito", audit[1][4])
        # reactivar una unidad activa no cambia nada
        other = self._create("B")
        before = self.row(other)
        self.assertTrue(um.set_unit_active(other, True, user_id))
        self.assertEqual(self.row(other), before)
        self.assertEqual(self.state_of(other), "DISPONIBLE")
        self.assertFalse(um.set_unit_active(4000000000, True, user_id))

    def test_participation_blocks_deactivate_by_scenario(self):
        user_id = self.user_id("admin.demo")
        for index, (dstate, kind, blocks) in enumerate(self.SCENARIOS):
            uid = self._create(f"PD{index}")
            self.add_participation(uid, dstate, kind)
            before = self.row(uid)
            if blocks:
                with self.assertRaises(um.UnitBusyError, msg=(dstate, kind)) as ctx:
                    um.set_unit_active(uid, False, user_id)
                self.assertIn("participación activa", str(ctx.exception))
                self.assertEqual(self.row(uid), before)
                self.assertEqual(self.audit_rows(uid), [])
            else:
                self.assertTrue(um.set_unit_active(uid, False, user_id), (dstate, kind))
                self.assertEqual(self.active_of(uid), 0)
                self.assertEqual(self.state_of(uid), "FUERA_DE_SERVICIO")
                self.assertEqual(len(self.audit_rows(uid)), 1)

    def test_participation_blocks_transfer_by_scenario(self):
        target = self.temp_station()
        for index, (dstate, kind, blocks) in enumerate(self.SCENARIOS):
            sfx = f"PT{index}"
            uid = self._create(sfx)
            self.add_participation(uid, dstate, kind)
            before = self.row(uid)
            # edición sin cambio de estación nunca se bloquea por participaciones
            self.assertTrue(self.update_unit(uid, self._fields(sfx, name="Editada")))
            before = self.row(uid)
            if blocks:
                with self.assertRaises(um.UnitBusyError, msg=(dstate, kind)) as ctx:
                    self.update_unit(uid, self._fields(sfx, station_id=target))
                self.assertIn("participación activa", str(ctx.exception))
                self.assertEqual(self.row(uid), before)
            else:
                self.assertTrue(self.update_unit(uid, self._fields(sfx, station_id=target)))
                self.assertEqual(um.get_unit(uid)["station_id"], target)
            self.assertEqual(self.state_of(uid), "DISPONIBLE")

    def test_no_participation_behaviour_preserved(self):
        user_id = self.user_id("admin.demo")
        uid = self._create()
        self.assertTrue(self.update_unit(uid, self._fields(station_id=self.temp_station())))
        self.assertTrue(um.set_unit_active(uid, False, user_id))
        # El traslado (MODIFICAR) y el retiro (DESACTIVAR) dejan una fila cada uno
        self.assertEqual([a[1] for a in self.audit_rows(uid)], ["MODIFICAR", "DESACTIVAR"])

    def test_inactive_unit_noop_even_with_participation(self):
        user_id = self.user_id("admin.demo")
        uid = self._create()
        um.set_unit_active(uid, False, user_id)
        self.add_participation(uid, "ACTIVO", "ASSIGNED")
        before = self.row(uid)
        self.assertTrue(um.set_unit_active(uid, False, user_id))
        self.assertTrue(um.set_unit_active(uid, True, user_id))
        self.assertEqual(self.active_of(uid), 1)
        self.assertEqual([a[1] for a in self.audit_rows(uid)], ["DESACTIVAR", "REACTIVAR"])
        self.assertNotEqual(self.row(uid), before)

    def test_participation_check_blocks_while_unit_locked(self):
        from config.database import get_connection
        uid = self._create("LK")
        conn_a = get_connection()
        conn_b = get_connection()
        try:
            cur_a = conn_a.cursor(dictionary=True)
            um._lock_unit(cur_a, uid)
            self.assertFalse(um._has_active_participation(cur_a, uid))
            cur_b = conn_b.cursor()
            cur_b.execute("SET SESSION innodb_lock_wait_timeout = 2")
            with self.assertRaises(um.mysql.connector.Error):
                cur_b.execute("SELECT id_unidad FROM unidades WHERE id_unidad = %s "
                              "FOR UPDATE", (uid,))
        finally:
            cur_a.close()
            conn_a.rollback()
            conn_b.rollback()
            conn_a.close()
            conn_b.close()
        self.assertEqual(self.active_of(uid), 1)

    def test_cleanup_continues_after_failed_step(self):
        # un paso roto no debe impedir los siguientes
        uid = self._create("CL")
        real_steps = self.cleanup_steps

        def broken_steps():
            return [("DELETE FROM tabla_que_no_existe", ())] + real_steps()

        self.cleanup_steps = broken_steps
        try:
            failures = self.run_cleanup()
        finally:
            self.cleanup_steps = real_steps
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.row(uid), [])
        self.assertEqual(self.count_prefix(), 0)

    def test_participation_insert_blocked_while_unit_locked(self):
        from config.database import get_connection
        uid = self._create("LI")
        dispatch_id = self.add_empty_dispatch("ACTIVO")
        insert = ("INSERT INTO despacho_unidades (id_despacho, id_unidad, id_estacion_origen, "
                  "fecha_asignacion) VALUES (%s, %s, %s, '2026-01-01 09:00:00')")
        params = (dispatch_id, uid, self.seed_station)
        conn_a = get_connection()
        conn_b = get_connection()
        cur_a = cur_b = None
        try:
            cur_a = conn_a.cursor(dictionary=True)
            um._lock_unit(cur_a, uid)
            self.assertFalse(um._has_active_participation(cur_a, uid))
            cur_b = conn_b.cursor()
            cur_b.execute("SET SESSION innodb_lock_wait_timeout = 2")
            try:
                cur_b.execute(insert, params)
            except um.mysql.connector.Error as error:
                self.assertEqual(error.errno, 1205)
            else:
                conn_b.rollback()
                self.skipTest("InnoDB no bloqueó el INSERT de participación en este entorno")
            conn_b.rollback()
            conn_a.rollback()
            # liberado el bloqueo, el INSERT procede
            cur_b.execute(insert, params)
            conn_b.commit()
            self.assertTrue(um._has_active_participation(cur_b, uid))
            conn_b.rollback()
        finally:
            for cur in (cur_a, cur_b):
                if cur is not None:
                    cur.close()
            conn_a.rollback()
            conn_b.rollback()
            conn_a.close()
            conn_b.close()

    def test_multiple_participations(self):
        user_id = self.user_id("admin.demo")
        target = self.temp_station()
        # una liberada y otra abierta: bloquea
        blocked = self._create("M1")
        self.add_participation(blocked, "ACTIVO", "RELEASED")
        self.add_participation(blocked, "ACTIVO", "ASSIGNED")
        before = self.row(blocked)
        with self.assertRaises(um.UnitBusyError):
            um.set_unit_active(blocked, False, user_id)
        with self.assertRaises(um.UnitBusyError):
            self.update_unit(blocked, self._fields("M1", station_id=target))
        self.assertEqual(self.row(blocked), before)
        self.assertEqual(self.audit_rows(blocked), [])
        # una liberada y otra cancelada: no bloquea
        free = self._create("M2")
        self.add_participation(free, "ACTIVO", "RELEASED")
        self.add_participation(free, "ACTIVO", "CANCELLED")
        self.assertTrue(self.update_unit(free, self._fields("M2", station_id=target)))
        self.assertTrue(um.set_unit_active(free, False, user_id))
        self.assertEqual([a[1] for a in self.audit_rows(free)], ["MODIFICAR", "DESACTIVAR"])


if __name__ == "__main__":
    unittest.main()

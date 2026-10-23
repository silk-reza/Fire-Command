"""Utilidades compartidas por los tests de unidades (solo para pruebas)."""
import unittest
from uuid import uuid4

from config.database import get_connection
from models import station_model, unit_model


def db_all(query, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        connection.close()


def db_exec(query, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(query, params)
        connection.commit()
        last_id = cursor.lastrowid
        cursor.close()
        return last_id
    finally:
        connection.close()


def seed_snapshot():
    return (
        db_all("SELECT * FROM unidades WHERE codigo NOT LIKE 'TUN-%' ORDER BY id_unidad"),
        db_all("SELECT * FROM estaciones WHERE codigo NOT LIKE 'TUS-%' "
               "AND codigo NOT LIKE 'TPS-%' ORDER BY id_estacion"),
        db_all("SELECT * FROM personal ORDER BY id_personal"),
        db_all("SELECT * FROM tipos_unidad WHERE nombre NOT LIKE 'TUT-%' ORDER BY id_tipo_unidad"),
        db_all("SELECT * FROM estados_unidad ORDER BY id_estado_unidad"),
        db_all("SELECT * FROM bitacora WHERE entidad = 'UNIDAD' AND id_entidad IN "
               "(SELECT id_unidad FROM unidades WHERE codigo NOT LIKE 'TUN-%') "
               "ORDER BY id_bitacora"),
        db_all("SELECT * FROM incidentes WHERE codigo NOT LIKE 'TUI-%' ORDER BY id_incidente"),
        db_all("SELECT * FROM despachos WHERE id_incidente IN "
               "(SELECT id_incidente FROM incidentes WHERE codigo NOT LIKE 'TUI-%') "
               "ORDER BY id_despacho"),
        db_all("SELECT * FROM despacho_unidades WHERE id_unidad IN "
               "(SELECT id_unidad FROM unidades WHERE codigo NOT LIKE 'TUN-%') "
               "ORDER BY id_despacho_unidad"),
    )


class UnitTestBase(unittest.TestCase):
    def setUp(self):
        self.prefix = f"TUN-{uuid4().hex[:8]}"
        self.plate_prefix = f"TP-{uuid4().hex[:8]}"
        self.station_ids = []
        self.type_ids = []
        self.incident_prefix = f"TUI-{uuid4().hex[:8]}"
        self.seed_before = seed_snapshot()
        self.seed_station = db_all(
            "SELECT id_estacion FROM estaciones WHERE codigo NOT LIKE 'TUS-%' "
            "AND codigo NOT LIKE 'TPS-%' ORDER BY id_estacion LIMIT 1")[0][0]
        self.seed_type = db_all(
            "SELECT id_tipo_unidad FROM tipos_unidad WHERE nombre NOT LIKE 'TUT-%' "
            "ORDER BY id_tipo_unidad LIMIT 1")[0][0]

    def cleanup_steps(self):
        """Pasos de limpieza en orden de FK; cada uno es (SQL, parámetros)."""
        pre = self.incident_prefix + "%"
        steps = [
            ("DELETE du FROM despacho_unidades du JOIN despachos d "
             "ON d.id_despacho = du.id_despacho JOIN incidentes i "
             "ON i.id_incidente = d.id_incidente WHERE i.codigo LIKE %s", (pre,)),
            ("DELETE d FROM despachos d JOIN incidentes i "
             "ON i.id_incidente = d.id_incidente WHERE i.codigo LIKE %s", (pre,)),
            ("DELETE FROM incidentes WHERE codigo LIKE %s", (pre,)),
            ("DELETE FROM bitacora WHERE entidad = 'UNIDAD' AND id_entidad IN "
             "(SELECT id_unidad FROM unidades WHERE codigo LIKE %s)", (self.prefix + "%",)),
            ("DELETE FROM unidades WHERE codigo LIKE %s", (self.prefix + "%",)),
        ]
        steps += [("DELETE FROM tipos_unidad WHERE id_tipo_unidad = %s", (t,))
                  for t in self.type_ids]
        steps += [("DELETE FROM estaciones WHERE id_estacion = %s", (st,))
                  for st in self.station_ids]
        return steps

    def run_cleanup(self):
        """Ejecuta cada paso por separado; un fallo no impide los demás."""
        failures = []
        for query, params in self.cleanup_steps():
            try:
                connection = get_connection()
                try:
                    cursor = connection.cursor()
                    cursor.execute(query, params)
                    connection.commit()
                    cursor.close()
                finally:
                    connection.close()
            except Exception as error:
                failures.append((query, error))
        return failures

    def tearDown(self):
        failures = self.run_cleanup()
        self.assertEqual(failures, [])
        self.assertEqual(seed_snapshot(), self.seed_before)

    def add_empty_dispatch(self, dispatch_state="ACTIVO"):
        """Crea incidente y despacho temporales sin participaciones; devuelve id_despacho."""
        admin_id = self.user_id("admin.demo")
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO incidentes (codigo, id_tipo_incidente, id_prioridad, "
                "id_estado_incidente, id_usuario_registro, fecha_reporte, direccion, "
                "descripcion) VALUES (%s, (SELECT MIN(id_tipo_incidente) FROM tipos_incidente), "
                "(SELECT MIN(id_prioridad) FROM prioridades), "
                "(SELECT MIN(id_estado_incidente) FROM estados_incidente), %s, "
                "'2026-01-01 08:00:00', 'Dir', 'Temporal')",
                (f"{self.incident_prefix}-{uuid4().hex[:6]}", admin_id))
            incident_id = cur.lastrowid
            cur.execute(
                "INSERT INTO despachos (id_incidente, id_estado_despacho, id_usuario, "
                "fecha_despacho) VALUES (%s, (SELECT id_estado_despacho FROM estados_despacho "
                "WHERE codigo = %s), %s, '2026-01-01 09:00:00')",
                (incident_id, dispatch_state, admin_id))
            dispatch_id = cur.lastrowid
            conn.commit()
            cur.close()
            return dispatch_id
        finally:
            conn.close()

    def temp_station(self, active=True):
        station_id = station_model.create_station(
            f"TUS-{uuid4().hex[:8]}", "Estación temporal", "Dir", None, None, None, None)
        self.station_ids.append(station_id)
        if not active:
            station_model.set_station_active(station_id, False)
        return station_id

    def temp_type(self, active=True):
        type_id = db_exec(
            "INSERT INTO tipos_unidad (nombre, descripcion, activo) VALUES (%s, NULL, %s)",
            (f"TUT-{uuid4().hex[:8]}", 1 if active else 0))
        self.type_ids.append(type_id)
        return type_id

    def deactivate_temp_type(self, type_id):
        db_exec("UPDATE tipos_unidad SET activo = 0 WHERE id_tipo_unidad = %s", (type_id,))

    def set_state(self, unit_id, state_code):
        """Solo para el harness de pruebas: fuerza el estado de una unidad temporal."""
        db_exec(
            "UPDATE unidades SET id_estado_unidad = "
            "(SELECT id_estado_unidad FROM estados_unidad WHERE codigo = %s) "
            "WHERE id_unidad = %s", (state_code, unit_id))

    def add_participation(self, unit_id, dispatch_state, kind):
        """Crea incidente, despacho y participación temporales (solo harness).

        kind: ASSIGNED (asignada sin cerrar), RELEASED, CANCELLED o PRESELECTION.
        """
        admin_id = self.user_id("admin.demo")
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO incidentes (codigo, id_tipo_incidente, id_prioridad, "
                "id_estado_incidente, id_usuario_registro, fecha_reporte, direccion, "
                "descripcion) VALUES (%s, (SELECT MIN(id_tipo_incidente) FROM tipos_incidente), "
                "(SELECT MIN(id_prioridad) FROM prioridades), "
                "(SELECT MIN(id_estado_incidente) FROM estados_incidente), %s, "
                "'2026-01-01 08:00:00', 'Dir', 'Temporal')",
                (f"{self.incident_prefix}-{uuid4().hex[:6]}", admin_id))
            incident_id = cur.lastrowid
            cur.execute(
                "INSERT INTO despachos (id_incidente, id_estado_despacho, id_usuario, "
                "fecha_despacho, fecha_finalizacion, fecha_cancelacion, motivo_cancelacion) "
                "VALUES (%s, (SELECT id_estado_despacho FROM estados_despacho WHERE codigo = %s), "
                "%s, %s, %s, %s, %s)",
                (incident_id, dispatch_state, admin_id,
                 None if dispatch_state == "CREADO" else "2026-01-01 09:00:00",
                 "2026-01-01 12:00:00" if dispatch_state == "COMPLETADO" else None,
                 "2026-01-01 10:00:00" if dispatch_state == "CANCELADO" else None,
                 "Motivo temporal" if dispatch_state == "CANCELADO" else None))
            dispatch_id = cur.lastrowid
            station_id = self.row(unit_id)[0][1]
            assigned = None if kind == "PRESELECTION" else "2026-01-01 09:00:00"
            departure = "2026-01-01 09:10:00" if kind == "RELEASED" else None
            arrival = "2026-01-01 09:20:00" if kind == "RELEASED" else None
            release = "2026-01-01 09:40:00" if kind == "RELEASED" else None
            cancel = "2026-01-01 09:30:00" if kind == "CANCELLED" else None
            cur.execute(
                "INSERT INTO despacho_unidades (id_despacho, id_unidad, id_estacion_origen, "
                "fecha_asignacion, fecha_salida, fecha_llegada_escena, fecha_liberacion, "
                "fecha_cancelacion, motivo_cancelacion) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (dispatch_id, unit_id, None if kind == "PRESELECTION" else station_id,
                 assigned, departure, arrival, release, cancel,
                 "Motivo temporal" if cancel else None))
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def row(self, unit_id):
        return db_all("SELECT * FROM unidades WHERE id_unidad = %s", (unit_id,))

    def state_of(self, unit_id):
        return db_all(
            "SELECT s.codigo FROM unidades u JOIN estados_unidad s "
            "ON s.id_estado_unidad = u.id_estado_unidad WHERE u.id_unidad = %s",
            (unit_id,))[0][0]

    def active_of(self, unit_id):
        return db_all("SELECT activo FROM unidades WHERE id_unidad = %s", (unit_id,))[0][0]

    def audit_rows(self, unit_id):
        return db_all(
            "SELECT id_usuario, accion, entidad, id_entidad, detalle FROM bitacora "
            "WHERE entidad = 'UNIDAD' AND id_entidad = %s", (unit_id,))

    def count_prefix(self):
        return db_all("SELECT COUNT(*) FROM unidades WHERE codigo LIKE %s",
                      (self.prefix + "%",))[0][0]

    def user_id(self, username):
        return db_all("SELECT id_usuario FROM usuarios WHERE nombre_usuario = %s",
                      (username,))[0][0]


__all__ = ["UnitTestBase", "db_all", "db_exec", "seed_snapshot", "unit_model"]

"""Utilidades compartidas por los tests de incidentes (solo para pruebas)."""
import unittest
from uuid import uuid4

import mysql.connector

from config.database import get_connection
from tests._unit_support import db_all, db_exec

# Dependencias de cada estado: columnas de fecha que deben estar pobladas
_STATE_UPDATES = {
    "REPORTADO": "",
    "ASIGNADO": ", fecha_asignacion = DATE_ADD(fecha_reporte, INTERVAL 1 MINUTE)",
    "EN_ATENCION": (", fecha_asignacion = DATE_ADD(fecha_reporte, INTERVAL 1 MINUTE)"
                    ", fecha_inicio_atencion = DATE_ADD(fecha_reporte, INTERVAL 2 MINUTE)"),
    "CONTROLADO": (", fecha_asignacion = DATE_ADD(fecha_reporte, INTERVAL 1 MINUTE)"
                   ", fecha_inicio_atencion = DATE_ADD(fecha_reporte, INTERVAL 2 MINUTE)"
                   ", fecha_control = DATE_ADD(fecha_reporte, INTERVAL 3 MINUTE)"),
    "CERRADO": (", fecha_asignacion = DATE_ADD(fecha_reporte, INTERVAL 1 MINUTE)"
                ", fecha_inicio_atencion = DATE_ADD(fecha_reporte, INTERVAL 2 MINUTE)"
                ", fecha_control = DATE_ADD(fecha_reporte, INTERVAL 3 MINUTE)"
                ", fecha_cierre = DATE_ADD(fecha_reporte, INTERVAL 4 MINUTE)"),
    "CANCELADO": (", fecha_cancelacion = DATE_ADD(fecha_reporte, INTERVAL 1 MINUTE)"
                  ", motivo_cancelacion = 'Motivo temporal'"),
}

_TEMP = "TIN-%"


def seed_snapshot():
    # Una sola conexión para todas las consultas evita agotar puertos en la suite completa
    connection = get_connection()
    cursor = connection.cursor()

    def q(query):
        cursor.execute(query)
        return cursor.fetchall()

    try:
        return _snapshot_rows(q)
    finally:
        cursor.close()
        connection.close()


def _snapshot_rows(q):
    return (
        q("SELECT * FROM incidentes WHERE codigo NOT LIKE 'TIN-%' "
               "AND codigo NOT LIKE 'TUI-%' ORDER BY id_incidente"),
        q("SELECT * FROM despachos WHERE id_incidente IN "
               "(SELECT id_incidente FROM incidentes WHERE codigo NOT LIKE 'TIN-%' "
               "AND codigo NOT LIKE 'TUI-%') ORDER BY id_despacho"),
        q("SELECT * FROM despacho_unidades WHERE id_despacho IN "
               "(SELECT d.id_despacho FROM despachos d JOIN incidentes i "
               "ON i.id_incidente = d.id_incidente WHERE i.codigo NOT LIKE 'TIN-%' "
               "AND i.codigo NOT LIKE 'TUI-%') ORDER BY id_despacho_unidad"),
        q("SELECT * FROM unidades WHERE codigo NOT LIKE 'TUN-%' ORDER BY id_unidad"),
        q("SELECT * FROM tipos_incidente WHERE nombre NOT LIKE 'TIT-%' "
               "ORDER BY id_tipo_incidente"),
        q("SELECT * FROM prioridades WHERE nombre NOT LIKE 'TIP-%' ORDER BY id_prioridad"),
        q("SELECT * FROM estados_incidente ORDER BY id_estado_incidente"),
        q("SELECT * FROM bitacora WHERE entidad = 'INCIDENTE' AND id_entidad IN "
               "(SELECT id_incidente FROM incidentes WHERE codigo NOT LIKE 'TIN-%' "
               "AND codigo NOT LIKE 'TUI-%') ORDER BY id_bitacora"),
        q("SELECT * FROM bitacora WHERE entidad = 'DESPACHO' AND id_entidad IN "
          "(SELECT d.id_despacho FROM despachos d JOIN incidentes i "
          "ON i.id_incidente = d.id_incidente WHERE i.codigo NOT LIKE 'TIN-%' "
          "AND i.codigo NOT LIKE 'TUI-%') ORDER BY id_bitacora"),
        q("SELECT COUNT(*) FROM bitacora WHERE entidad = 'INCIDENTE' AND id_entidad "
               "NOT IN (SELECT id_incidente FROM incidentes WHERE codigo LIKE 'TIN-%' "
               "OR codigo LIKE 'TUI-%')"),
    )


class IncidentTestBase(unittest.TestCase):
    def setUp(self):
        self.prefix = f"TIN-{uuid4().hex[:8]}"
        self.type_ids = []
        self.priority_ids = []
        self.seed_before = seed_snapshot()
        self.seed_type, self.seed_priority = db_all(
            "SELECT (SELECT id_tipo_incidente FROM tipos_incidente WHERE nombre NOT LIKE "
            "'TIT-%' AND activo = 1 ORDER BY id_tipo_incidente LIMIT 1), "
            "(SELECT id_prioridad FROM prioridades WHERE nombre NOT LIKE 'TIP-%' "
            "AND activo = 1 ORDER BY id_prioridad LIMIT 1)")[0]

    def cleanup_steps(self):
        """Pasos de limpieza en orden de FK; cada uno es (SQL, parámetros)."""
        temp = "(SELECT id_incidente FROM incidentes WHERE codigo LIKE %s)"
        steps = [
            ("DELETE FROM bitacora WHERE entidad = 'DESPACHO' AND id_entidad IN "
             "(SELECT id_despacho FROM despachos WHERE id_incidente IN " + temp + ")",
             (self.prefix + "%",)),
            ("DELETE FROM despacho_unidades WHERE id_despacho IN "
             "(SELECT id_despacho FROM despachos WHERE id_incidente IN " + temp + ")",
             (self.prefix + "%",)),
            ("DELETE FROM despachos WHERE id_incidente IN " + temp, (self.prefix + "%",)),
            ("DELETE FROM bitacora WHERE entidad = 'INCIDENTE' AND id_entidad IN "
             "(SELECT id_incidente FROM incidentes WHERE codigo LIKE %s)", (self.prefix + "%",)),
            ("DELETE FROM incidentes WHERE codigo LIKE %s", (self.prefix + "%",)),
        ]
        steps += [("DELETE FROM tipos_incidente WHERE id_tipo_incidente = %s", (t,))
                  for t in self.type_ids]
        steps += [("DELETE FROM prioridades WHERE id_prioridad = %s", (p,))
                  for p in self.priority_ids]
        return steps

    def run_cleanup(self):
        """Ejecuta cada paso por separado; un fallo no impide los demás."""
        failures = []
        try:
            connection = get_connection()
        except Exception as error:
            return [("conexión", error)]
        try:
            cursor = connection.cursor()
            for query, params in self.cleanup_steps():
                try:
                    cursor.execute(query, params)
                    connection.commit()
                except Exception as error:
                    failures.append((query, error))
                    try:
                        connection.rollback()
                    except Exception:
                        pass
            cursor.close()
        finally:
            connection.close()
        return failures

    def tearDown(self):
        failures = self.run_cleanup()
        self.assertEqual(failures, [])
        self.assertEqual(seed_snapshot(), self.seed_before)

    def temp_type(self, active=True):
        type_id = db_exec(
            "INSERT INTO tipos_incidente (nombre, descripcion, activo) VALUES (%s, NULL, %s)",
            (f"TIT-{uuid4().hex[:8]}", 1 if active else 0))
        self.type_ids.append(type_id)
        return type_id

    def temp_priority(self, active=True):
        for _ in range(20):
            used = {row[0] for row in db_all("SELECT nivel FROM prioridades")}
            level = next(n for n in range(100, 251) if n not in used)
            try:
                priority_id = db_exec(
                    "INSERT INTO prioridades (nombre, nivel, descripcion, activo) "
                    "VALUES (%s, %s, NULL, %s)",
                    (f"TIP-{uuid4().hex[:8]}", level, 1 if active else 0))
            except mysql.connector.IntegrityError:
                continue
            self.priority_ids.append(priority_id)
            return priority_id
        raise RuntimeError("No se pudo crear una prioridad temporal")

    def deactivate_type(self, type_id):
        db_exec("UPDATE tipos_incidente SET activo = 0 WHERE id_tipo_incidente = %s", (type_id,))

    def deactivate_priority(self, priority_id):
        db_exec("UPDATE prioridades SET activo = 0 WHERE id_prioridad = %s", (priority_id,))

    def insert_incident(self, suffix="A", state_code="REPORTADO", **overrides):
        """Inserta un incidente temporal por SQL directo (solo harness) y devuelve su id."""
        values = {
            "codigo": f"{self.prefix}-{suffix}",
            "tipo": self.seed_type, "prioridad": self.seed_priority,
            "direccion": "Dirección temporal", "descripcion": "Descripción temporal",
            "usuario": self.user_id("admin.demo"),
        }
        values.update(overrides)
        incident_id = db_exec(
            "INSERT INTO incidentes (codigo, id_tipo_incidente, id_prioridad, "
            "id_estado_incidente, id_usuario_registro, fecha_reporte, direccion, "
            "descripcion) VALUES (%s, %s, %s, (SELECT id_estado_incidente FROM "
            "estados_incidente WHERE codigo = 'REPORTADO'), %s, "
            "DATE_SUB(NOW(), INTERVAL 1 HOUR), %s, %s)",
            (values["codigo"], values["tipo"], values["prioridad"], values["usuario"],
             values["direccion"], values["descripcion"]))
        if state_code != "REPORTADO":
            self.set_state(incident_id, state_code)
        return incident_id

    def set_state(self, incident_id, state_code):
        """Solo para el harness de pruebas: fuerza estado y fechas coherentes."""
        db_exec(
            "UPDATE incidentes SET id_estado_incidente = (SELECT id_estado_incidente "
            "FROM estados_incidente WHERE codigo = %s)" + _STATE_UPDATES[state_code]
            + " WHERE id_incidente = %s", (state_code, incident_id))

    def add_dispatch(self, incident_id, state_code="CREADO"):
        """Inserta un despacho temporal por SQL directo (solo harness)."""
        dates = {
            "CREADO": "NULL, NULL, NULL, NULL",
            "ACTIVO": "DATE_SUB(NOW(), INTERVAL 30 MINUTE), NULL, NULL, NULL",
            "COMPLETADO": ("DATE_SUB(NOW(), INTERVAL 30 MINUTE), "
                           "DATE_SUB(NOW(), INTERVAL 10 MINUTE), NULL, NULL"),
            "CANCELADO": "NULL, NULL, DATE_SUB(NOW(), INTERVAL 10 MINUTE), 'Motivo temporal'",
        }[state_code]
        return db_exec(
            "INSERT INTO despachos (id_incidente, id_estado_despacho, id_usuario, "
            "fecha_despacho, fecha_finalizacion, fecha_cancelacion, motivo_cancelacion) "
            "VALUES (%s, (SELECT id_estado_despacho FROM estados_despacho WHERE codigo = %s), "
            "%s, " + dates + ")",
            (incident_id, state_code, self.user_id("admin.demo")))

    def add_preselection(self, dispatch_id, unit_id):
        return db_exec(
            "INSERT INTO despacho_unidades (id_despacho, id_unidad) VALUES (%s, %s)",
            (dispatch_id, unit_id))

    def dispatch_rows(self, incident_id):
        return db_all("SELECT * FROM despachos WHERE id_incidente = %s ORDER BY id_despacho",
                      (incident_id,))

    def dispatch_state(self, dispatch_id):
        return db_all(
            "SELECT e.codigo FROM despachos d JOIN estados_despacho e "
            "ON e.id_estado_despacho = d.id_estado_despacho WHERE d.id_despacho = %s",
            (dispatch_id,))[0][0]

    def preselections(self, dispatch_id):
        return db_all("SELECT * FROM despacho_unidades WHERE id_despacho = %s "
                      "ORDER BY id_despacho_unidad", (dispatch_id,))

    def dispatch_audit(self, dispatch_id):
        return db_all(
            "SELECT id_usuario, accion, entidad, id_entidad, detalle FROM bitacora "
            "WHERE entidad = 'DESPACHO' AND id_entidad = %s ORDER BY id_bitacora",
            (dispatch_id,))

    def row(self, incident_id):
        return db_all("SELECT * FROM incidentes WHERE id_incidente = %s", (incident_id,))

    def fixed_row(self, incident_id):
        """Columnas que ninguna edición descriptiva debe tocar."""
        return db_all(
            "SELECT codigo, id_estado_incidente, id_usuario_registro, fecha_reporte, "
            "fecha_asignacion, fecha_inicio_atencion, fecha_control, fecha_cierre, "
            "fecha_cancelacion, motivo_cancelacion, creado_en FROM incidentes "
            "WHERE id_incidente = %s", (incident_id,))

    def state_of(self, incident_id):
        return db_all(
            "SELECT s.codigo FROM incidentes i JOIN estados_incidente s "
            "ON s.id_estado_incidente = i.id_estado_incidente "
            "WHERE i.id_incidente = %s", (incident_id,))[0][0]

    def audit_rows(self, incident_id):
        return db_all(
            "SELECT id_usuario, accion, entidad, id_entidad, detalle FROM bitacora "
            "WHERE entidad = 'INCIDENTE' AND id_entidad = %s ORDER BY id_bitacora",
            (incident_id,))

    def count_prefix(self):
        return db_all("SELECT COUNT(*) FROM incidentes WHERE codigo LIKE %s",
                      (self.prefix + "%",))[0][0]

    def count_audit_prefix(self):
        return db_all(
            "SELECT COUNT(*) FROM bitacora WHERE entidad = 'INCIDENTE' AND id_entidad IN "
            "(SELECT id_incidente FROM incidentes WHERE codigo LIKE %s)",
            (self.prefix + "%",))[0][0]

    def user_id(self, username):
        return db_all("SELECT id_usuario FROM usuarios WHERE nombre_usuario = %s",
                      (username,))[0][0]


__all__ = ["IncidentTestBase", "db_all", "db_exec", "seed_snapshot"]

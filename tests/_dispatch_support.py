"""Utilidades compartidas por los tests de despachos (solo para pruebas)."""
from uuid import uuid4

from config.database import get_connection
from models import station_model, unit_model
from tests._incident_support import IncidentTestBase, db_all, db_exec, seed_snapshot


def full_snapshot():
    """Tablas completas afectadas por OBJ 10; tras la limpieza deben quedar como al inicio."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        result = []
        for table, order in (
            ("incidentes", "id_incidente"), ("despachos", "id_despacho"),
            ("despacho_unidades", "id_despacho_unidad"), ("unidades", "id_unidad"),
            ("estaciones", "id_estacion"), ("personal", "id_personal"),
            ("tipos_unidad", "id_tipo_unidad"), ("estados_unidad", "id_estado_unidad"),
            ("estados_despacho", "id_estado_despacho"),
            ("estados_incidente", "id_estado_incidente"), ("bitacora", "id_bitacora"),
        ):
            cursor.execute(f"SELECT * FROM {table} ORDER BY {order}")
            result.append(cursor.fetchall())
        cursor.close()
        return result
    finally:
        connection.close()


class DispatchTestBase(IncidentTestBase):
    def setUp(self):
        super().setUp()
        self.unit_prefix = f"TUN-{uuid4().hex[:8]}"
        self.plate_prefix = f"TP-{uuid4().hex[:8]}"
        self.station_ids = []
        self.unit_type_ids = []
        self.seed_station = db_all(
            "SELECT id_estacion FROM estaciones WHERE codigo NOT LIKE 'TUS-%' "
            "AND codigo NOT LIKE 'TPS-%' ORDER BY id_estacion LIMIT 1")[0][0]
        self.seed_unit_type = db_all(
            "SELECT id_tipo_unidad FROM tipos_unidad WHERE nombre NOT LIKE 'TUT-%' "
            "ORDER BY id_tipo_unidad LIMIT 1")[0][0]
        self.full_before = full_snapshot()
        self._unit_counter = 0

    def cleanup_steps(self):
        steps = [
            ("DELETE FROM despacho_unidades WHERE id_unidad IN "
             "(SELECT id_unidad FROM unidades WHERE codigo LIKE %s)",
             (self.unit_prefix + "%",)),
        ]
        steps += super().cleanup_steps()
        steps += [
            ("DELETE FROM bitacora WHERE entidad = 'UNIDAD' AND id_entidad IN "
             "(SELECT id_unidad FROM unidades WHERE codigo LIKE %s)",
             (self.unit_prefix + "%",)),
            ("DELETE FROM unidades WHERE codigo LIKE %s", (self.unit_prefix + "%",)),
        ]
        steps += [("DELETE FROM tipos_unidad WHERE id_tipo_unidad = %s", (t,))
                  for t in self.unit_type_ids]
        steps += [("DELETE FROM estaciones WHERE id_estacion = %s", (s,))
                  for s in self.station_ids]
        return steps

    def tearDown(self):
        failures = self.run_cleanup()
        self.assertEqual(failures, [])
        self.assertEqual(seed_snapshot(), self.seed_before)
        self.assertEqual(full_snapshot(), self.full_before)

    def temp_station(self, active=True):
        station_id = station_model.create_station(
            f"TUS-{uuid4().hex[:8]}", "Estación temporal", "Dir", None, None, None, None)
        self.station_ids.append(station_id)
        if not active:
            station_model.set_station_active(station_id, False)
        return station_id

    def temp_unit_type(self):
        type_id = db_exec(
            "INSERT INTO tipos_unidad (nombre, descripcion, activo) VALUES (%s, NULL, 1)",
            (f"TUT-{uuid4().hex[:8]}",))
        self.unit_type_ids.append(type_id)
        return type_id

    def temp_unit(self, state_code="DISPONIBLE", active=True, station_id=None,
                  type_id=None, name=None, plate=None):
        """Crea una unidad temporal y fuerza su estado/activo por SQL (solo harness)."""
        self._unit_counter += 1
        unit_id = unit_model.create_unit(
            f"{self.unit_prefix}-{self._unit_counter}", name, plate, 4, None,
            station_id or self.seed_station, type_id or self.seed_unit_type)
        if state_code != "DISPONIBLE":
            self.set_unit_state(unit_id, state_code)
        if not active:
            db_exec("UPDATE unidades SET activo = 0 WHERE id_unidad = %s", (unit_id,))
        return unit_id

    def set_unit_state(self, unit_id, state_code):
        db_exec(
            "UPDATE unidades SET id_estado_unidad = (SELECT id_estado_unidad FROM "
            "estados_unidad WHERE codigo = %s) WHERE id_unidad = %s", (state_code, unit_id))

    def unit_row(self, unit_id):
        return db_all("SELECT * FROM unidades WHERE id_unidad = %s", (unit_id,))

    def unit_state(self, unit_id):
        return db_all(
            "SELECT s.codigo FROM unidades u JOIN estados_unidad s "
            "ON s.id_estado_unidad = u.id_estado_unidad WHERE u.id_unidad = %s",
            (unit_id,))[0][0]

    def add_assigned_participation(self, dispatch_id, unit_id):
        """Participación asignada válida (cadena de CHECK) en un despacho no borrador."""
        station_id = db_all("SELECT id_estacion FROM unidades WHERE id_unidad = %s",
                            (unit_id,))[0][0]
        return db_exec(
            "INSERT INTO despacho_unidades (id_despacho, id_unidad, id_estacion_origen, "
            "fecha_asignacion) VALUES (%s, %s, %s, DATE_SUB(NOW(), INTERVAL 20 MINUTE))",
            (dispatch_id, unit_id, station_id))

    def dispatch_row(self, dispatch_id):
        return db_all("SELECT * FROM despachos WHERE id_despacho = %s", (dispatch_id,))

    def active_participation_count(self, unit_id):
        """SQL independiente: participaciones asignadas, sin cerrar, en despacho ACTIVO."""
        return db_all(
            "SELECT COUNT(*) FROM despacho_unidades du JOIN despachos d "
            "ON d.id_despacho = du.id_despacho JOIN estados_despacho e "
            "ON e.id_estado_despacho = d.id_estado_despacho WHERE du.id_unidad = %s "
            "AND du.fecha_asignacion IS NOT NULL AND du.fecha_liberacion IS NULL "
            "AND du.fecha_cancelacion IS NULL AND e.codigo = 'ACTIVO'", (unit_id,))[0][0]

    def table_count(self, table):
        return db_all(f"SELECT COUNT(*) FROM {table}")[0][0]


__all__ = ["DispatchTestBase", "db_all", "db_exec", "full_snapshot"]

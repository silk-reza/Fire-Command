import re

import mysql.connector

from config.database import get_connection

# Estados que implican participación activa en un despacho (máquina de estados de unidades)
BUSY_STATE_CODES = ("DESPACHADA", "EN_RUTA", "EN_ESCENA")

INITIAL_STATE_CODE = "DISPONIBLE"
OUT_OF_SERVICE_STATE_CODE = "FUERA_DE_SERVICIO"
MAINTENANCE_STATE_CODE = "MANTENIMIENTO"
ACTIVE_DISPATCH_CODE = "ACTIVO"


class DuplicateUnitCodeError(Exception):
    """El código de unidad ya existe."""


class DuplicateUnitPlateError(Exception):
    """La placa de unidad ya existe."""


class InvalidReferenceError(Exception):
    """Una referencia (FK) no existe."""


class InvalidStationError(Exception):
    """La estación indicada no existe."""


class InactiveStationError(Exception):
    """La estación existe pero está inactiva."""


class InvalidTypeError(Exception):
    """El tipo de unidad indicado no existe."""


class InactiveTypeError(Exception):
    """El tipo de unidad existe pero está inactivo."""


class InvalidUnitDataError(Exception):
    """MySQL rechazó los datos por una restricción CHECK."""


class UnitBusyError(Exception):
    """La unidad participa en una atención activa y no admite la operación."""


class UnitTransitionError(Exception):
    """La unidad no está en un estado que admita la transición pedida."""


_UNIT_SELECT = """
    SELECT u.id_unidad AS unit_id,
           u.codigo AS code,
           u.nombre AS name,
           u.placa AS plate,
           u.capacidad_personal AS capacity,
           u.descripcion AS description,
           u.activo AS active,
           e.id_estacion AS station_id,
           e.codigo AS station_code,
           e.nombre AS station_name,
           t.id_tipo_unidad AS type_id,
           t.nombre AS type_name,
           s.id_estado_unidad AS state_id,
           s.codigo AS state_code,
           s.nombre AS state_name
    FROM unidades u
    INNER JOIN estaciones e ON e.id_estacion = u.id_estacion
    INNER JOIN tipos_unidad t ON t.id_tipo_unidad = u.id_tipo_unidad
    INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
"""

_ORDER = " ORDER BY u.codigo, u.id_unidad"


def _clean_unit(row):
    row["active"] = bool(row["active"])
    return row


def _fetch_all(query, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            cursor.close()
    finally:
        connection.close()


def _escape_like(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_units(term, station_id=None, type_id=None, state_id=None):
    """Busca por código, placa o nombre, combinando filtros con AND."""
    conditions = []
    params = []
    if term:
        pattern = f"%{_escape_like(term)}%"
        conditions.append("(u.codigo LIKE %s OR u.placa LIKE %s OR u.nombre LIKE %s)")
        params.extend([pattern] * 3)
    if station_id is not None:
        conditions.append("u.id_estacion = %s")
        params.append(station_id)
    if type_id is not None:
        conditions.append("u.id_tipo_unidad = %s")
        params.append(type_id)
    if state_id is not None:
        conditions.append("u.id_estado_unidad = %s")
        params.append(state_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _fetch_all(_UNIT_SELECT + where + _ORDER, tuple(params))
    return [_clean_unit(row) for row in rows]


def list_units(station_id=None, type_id=None, state_id=None):
    """Lista todas las unidades, activas e inactivas, con filtros opcionales."""
    return search_units("", station_id, type_id, state_id)


def get_unit(unit_id):
    """Devuelve una unidad por id o None."""
    rows = _fetch_all(_UNIT_SELECT + " WHERE u.id_unidad = %s", (unit_id,))
    return _clean_unit(rows[0]) if rows else None


def list_unit_types():
    """Todos los tipos de unidad, con su bandera de activo."""
    rows = _fetch_all(
        "SELECT id_tipo_unidad AS type_id, nombre AS name, descripcion AS description,"
        " activo AS active FROM tipos_unidad ORDER BY nombre, id_tipo_unidad"
    )
    for row in rows:
        row["active"] = bool(row["active"])
    return rows


def list_unit_states():
    """Estados operativos de unidad, en su orden aprobado."""
    return _fetch_all(
        "SELECT id_estado_unidad AS state_id, codigo AS code, nombre AS name,"
        " orden AS sort_order FROM estados_unidad ORDER BY orden, id_estado_unidad"
    )


def _translate_error(error):
    """Convierte errores de MySQL en excepciones del dominio."""
    message = str(error.msg or "")
    if error.errno == 1062:
        match = re.search(r"for key '([^']*)'\s*$", message)
        key = match.group(1).split(".")[-1] if match else ""
        if key == "uq_unidades_codigo":
            return DuplicateUnitCodeError("Ya existe una unidad con ese código.")
        if key == "uq_unidades_placa":
            return DuplicateUnitPlateError("Ya existe una unidad con esa placa.")
    if error.errno == 1452:
        return InvalidReferenceError("Una referencia de la unidad no existe.")
    if error.errno in (3819, 1264, 1406):
        return InvalidUnitDataError("Los datos de la unidad no son válidos.")
    return None


def _write(operation):
    """Ejecuta operation(cursor) en su propia transacción."""
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            result = operation(cursor)
            connection.commit()
            return result
        except mysql.connector.Error as error:
            connection.rollback()
            translated = _translate_error(error)
            if translated is None:
                raise
            raise translated from error
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
    finally:
        connection.close()


def _ensure_station_active(cursor, station_id):
    """Verifica en la misma transacción que la estación exista y esté activa."""
    cursor.execute(
        "SELECT activo FROM estaciones WHERE id_estacion = %s FOR SHARE",
        (station_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise InvalidStationError("La estación indicada no existe.")
    if not row["activo"]:
        raise InactiveStationError("No se puede asignar una unidad a una estación inactiva.")


def _ensure_type_active(cursor, type_id):
    """Verifica en la misma transacción que el tipo exista y esté activo."""
    cursor.execute(
        "SELECT activo FROM tipos_unidad WHERE id_tipo_unidad = %s FOR SHARE",
        (type_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise InvalidTypeError("El tipo de unidad indicado no existe.")
    if not row["activo"]:
        raise InactiveTypeError("No se puede asignar un tipo de unidad inactivo.")


def _state_id_by_code(cursor, state_code):
    cursor.execute(
        "SELECT id_estado_unidad FROM estados_unidad WHERE codigo = %s",
        (state_code,),
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Estado de unidad no encontrado: {state_code}")
    return row["id_estado_unidad"]


def _has_active_participation(cursor, unit_id):
    """Indica si la unidad está asignada y sin cerrar en un despacho ACTIVO."""
    # Orden de bloqueo: primero la fila de la unidad (X), luego las participaciones;
    # el código de despacho futuro debe bloquear la unidad antes que despacho_unidades
    cursor.execute(
        """
        SELECT du.id_despacho_unidad
        FROM despacho_unidades du
        INNER JOIN despachos d ON d.id_despacho = du.id_despacho
        INNER JOIN estados_despacho ed ON ed.id_estado_despacho = d.id_estado_despacho
        WHERE du.id_unidad = %s
          AND du.fecha_asignacion IS NOT NULL
          AND du.fecha_liberacion IS NULL
          AND du.fecha_cancelacion IS NULL
          AND ed.codigo = %s
        LIMIT 1
        FOR SHARE OF du
        """,
        (unit_id, ACTIVE_DISPATCH_CODE),
    )
    return cursor.fetchone() is not None


def create_unit(code, name, plate, capacity, description, station_id, type_id):
    """Inserta una unidad activa y DISPONIBLE; devuelve su nuevo id."""

    def operation(cursor):
        # Lee el estado real de estación y tipo al momento de guardar
        _ensure_station_active(cursor, station_id)
        _ensure_type_active(cursor, type_id)
        state_id = _state_id_by_code(cursor, INITIAL_STATE_CODE)
        cursor.execute(
            """
            INSERT INTO unidades
                (id_estacion, id_tipo_unidad, id_estado_unidad, codigo, nombre,
                 placa, capacidad_personal, descripcion, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
            """,
            (
                station_id,
                type_id,
                state_id,
                code,
                name or None,
                plate or None,
                capacity,
                description or None,
            ),
        )
        return cursor.lastrowid

    return _write(operation)


def _lock_unit(cursor, unit_id):
    cursor.execute(
        """
        SELECT u.id_unidad, u.id_estacion, u.id_tipo_unidad, u.activo,
               s.codigo AS state_code
        FROM unidades u
        INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
        WHERE u.id_unidad = %s
        FOR UPDATE OF u
        """,
        (unit_id,),
    )
    return cursor.fetchone()


def update_unit(unit_id, fields):
    """Actualiza datos administrativos (nunca el estado); False si no existe."""

    def operation(cursor):
        # rowcount es 0 cuando no hay cambios, por eso se verifica existencia aparte
        current = _lock_unit(cursor, unit_id)
        if current is None:
            return False
        if fields["station_id"] != current["id_estacion"]:
            # Una unidad con participación activa no cambia de estación
            if current["state_code"] in BUSY_STATE_CODES:
                raise UnitBusyError(
                    "No se puede transferir una unidad que está en atención "
                    f"({current['state_code']})."
                )
            if _has_active_participation(cursor, unit_id):
                raise UnitBusyError(
                    "La unidad tiene una participación activa en un despacho "
                    "y no puede cambiar de estación."
                )
            _ensure_station_active(cursor, fields["station_id"])
        if fields["type_id"] != current["id_tipo_unidad"]:
            _ensure_type_active(cursor, fields["type_id"])
        cursor.execute(
            """
            UPDATE unidades
            SET id_estacion = %s, id_tipo_unidad = %s, codigo = %s, nombre = %s,
                placa = %s, capacidad_personal = %s, descripcion = %s
            WHERE id_unidad = %s
            """,
            (
                fields["station_id"],
                fields["type_id"],
                fields["code"],
                fields["name"] or None,
                fields["plate"] or None,
                fields["capacity"],
                fields["description"] or None,
                unit_id,
            ),
        )
        return True

    return _write(operation)


def set_unit_active(unit_id, active, user_id):
    """Retira o reactiva una unidad; devuelve False si no existe."""

    def operation(cursor):
        current = _lock_unit(cursor, unit_id)
        if current is None:
            return False
        is_active = bool(current["activo"])
        if bool(active) == is_active:
            # Idempotente: no cambia nada ni escribe bitácora
            return True
        fds_id = _state_id_by_code(cursor, OUT_OF_SERVICE_STATE_CODE)
        if not active:
            if current["state_code"] in BUSY_STATE_CODES:
                raise UnitBusyError(
                    "No se puede desactivar una unidad que está en atención "
                    f"({current['state_code']})."
                )
            if _has_active_participation(cursor, unit_id):
                raise UnitBusyError(
                    "La unidad tiene una participación activa en un despacho "
                    "y no puede desactivarse."
                )
            cursor.execute(
                "UPDATE unidades SET activo = 0, id_estado_unidad = %s WHERE id_unidad = %s",
                (fds_id, unit_id),
            )
            # La bitácora confirma o revierte junto con el retiro
            cursor.execute(
                """
                INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
                VALUES (%s, 'DESACTIVAR', 'UNIDAD', %s, %s)
                """,
                (
                    user_id,
                    unit_id,
                    "Unidad retirada administrativamente: activo = 0, FUERA_DE_SERVICIO.",
                ),
            )
        else:
            # Nunca pasa a DISPONIBLE automáticamente
            cursor.execute(
                "UPDATE unidades SET activo = 1, id_estado_unidad = %s WHERE id_unidad = %s",
                (fds_id, unit_id),
            )
        return True

    return _write(operation)


def _audit_unit_state(cursor, user_id, unit_id, detail):
    cursor.execute(
        """
        INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
        VALUES (%s, 'CAMBIO_ESTADO', 'UNIDAD', %s, %s)
        """,
        (user_id, unit_id, detail),
    )


def _apply_service_transition(cursor, unit_id, source_code, target_code, user_id):
    """UPDATE guardado de estado (bajo lock) y bitácora; falla si no cambia una fila."""
    cursor.execute(
        "UPDATE unidades SET id_estado_unidad = %s WHERE id_unidad = %s "
        "AND id_estado_unidad = %s",
        (_state_id_by_code(cursor, target_code), unit_id,
         _state_id_by_code(cursor, source_code)),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("No se pudo actualizar el estado de la unidad.")
    _audit_unit_state(cursor, user_id, unit_id, f"{source_code} → {target_code}.")


def send_unit_to_maintenance(unit_id, user_id):
    """DISPONIBLE -> MANTENIMIENTO; False si la unidad no existe."""

    def operation(cursor):
        current = _lock_unit(cursor, unit_id)
        if current is None:
            return False
        if not current["activo"]:
            raise UnitTransitionError("Una unidad inactiva no puede enviarse a mantenimiento.")
        if current["state_code"] != INITIAL_STATE_CODE:
            raise UnitTransitionError(
                "Solo una unidad Disponible puede enviarse a mantenimiento "
                f"(estado actual: {current['state_code']})."
            )
        if _has_active_participation(cursor, unit_id):
            raise UnitBusyError(
                "La unidad tiene una participación activa en un despacho "
                "y no puede enviarse a mantenimiento."
            )
        _apply_service_transition(
            cursor, unit_id, INITIAL_STATE_CODE, MAINTENANCE_STATE_CODE, user_id)
        return True

    return _write(operation)


def return_unit_to_service(unit_id, user_id):
    """MANTENIMIENTO o FUERA_DE_SERVICIO -> DISPONIBLE; nunca cambia activo."""

    def operation(cursor):
        current = _lock_unit(cursor, unit_id)
        if current is None:
            return False
        if not current["activo"]:
            raise UnitTransitionError(
                "Una unidad inactiva no puede volver a servicio; reactívela primero."
            )
        if current["state_code"] not in (MAINTENANCE_STATE_CODE, OUT_OF_SERVICE_STATE_CODE):
            raise UnitTransitionError(
                "Solo una unidad en Mantenimiento o Fuera de servicio puede volver a "
                f"servicio (estado actual: {current['state_code']})."
            )
        if _has_active_participation(cursor, unit_id):
            raise UnitBusyError(
                "La unidad tiene una participación activa en un despacho "
                "y no puede volver a servicio."
            )
        try:
            _ensure_station_active(cursor, current["id_estacion"])
        except InactiveStationError:
            raise UnitTransitionError(
                "La estación de la unidad está inactiva; transfiera la unidad antes "
                "de devolverla a servicio."
            ) from None
        _apply_service_transition(
            cursor, unit_id, current["state_code"], INITIAL_STATE_CODE, user_id)
        return True

    return _write(operation)

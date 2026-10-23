import re

import mysql.connector

from config.database import get_connection

DRAFT_STATE_CODE = "CREADO"
CANCELLED_STATE_CODE = "CANCELADO"
AVAILABLE_UNIT_STATE_CODE = "DISPONIBLE"
# Estados de incidente que no admiten nuevos despachos
TERMINAL_INCIDENT_CODES = ("CERRADO", "CANCELADO")
_MAX_REASON_LENGTH = 250
_MAX_OBSERVATIONS_LENGTH = 65535
_TERMINAL_INCIDENT_MESSAGE = (
    "El incidente de este despacho está cerrado o cancelado: "
    "el borrador ya no puede modificarse; solo puede cancelarse."
)
_NOT_DRAFT_MESSAGE = "Solo se puede modificar o cancelar un despacho en estado Creado."


class DispatchIncidentNotFoundError(Exception):
    """El incidente indicado no existe."""


class IncidentTerminalError(Exception):
    """El incidente está cerrado o cancelado."""


class DispatchNotFoundError(Exception):
    """El despacho indicado no existe."""


class DispatchStateError(Exception):
    """El despacho no está en estado CREADO."""


class DraftIncidentClosedError(DispatchStateError):
    """El borrador pertenece a un incidente cerrado o cancelado y ya no se modifica."""


class UnitNotFoundError(Exception):
    """La unidad indicada no existe."""


class UnitNotEligibleError(Exception):
    """La unidad no cumple las condiciones para preseleccionarse."""


class DuplicateDraftUnitError(Exception):
    """La unidad ya está preseleccionada en ese borrador."""


class DraftUnitNotFoundError(Exception):
    """La unidad no está preseleccionada en ese borrador."""


class InvalidReferenceError(Exception):
    """Una referencia (FK) no existe."""


class InvalidDispatchDataError(Exception):
    """Datos rechazados por validación o por una restricción CHECK."""


_DISPATCH_SELECT = """
    SELECT d.id_despacho AS dispatch_id,
           i.id_incidente AS incident_id,
           i.codigo AS incident_code,
           si.codigo AS incident_state_code,
           si.nombre AS incident_state_name,
           i.direccion AS incident_address,
           sd.id_estado_despacho AS state_id,
           sd.codigo AS state_code,
           sd.nombre AS state_name,
           u.id_usuario AS created_by_id,
           u.nombre_usuario AS created_by_username,
           d.fecha_despacho AS dispatched_at,
           d.fecha_finalizacion AS finished_at,
           d.fecha_cancelacion AS cancelled_at,
           d.motivo_cancelacion AS cancel_reason,
           d.observaciones AS observations,
           (SELECT COUNT(*) FROM despacho_unidades du
             WHERE du.id_despacho = d.id_despacho) AS unit_count,
           d.creado_en AS created_at
    FROM despachos d
    INNER JOIN incidentes i ON i.id_incidente = d.id_incidente
    INNER JOIN estados_incidente si ON si.id_estado_incidente = i.id_estado_incidente
    INNER JOIN estados_despacho sd ON sd.id_estado_despacho = d.id_estado_despacho
    INNER JOIN usuarios u ON u.id_usuario = d.id_usuario
"""

_DRAFT_UNIT_SELECT = """
    SELECT du.id_despacho_unidad AS dispatch_unit_id,
           un.id_unidad AS unit_id,
           un.codigo AS unit_code,
           un.nombre AS unit_name,
           un.placa AS plate,
           t.nombre AS type_name,
           e.id_estacion AS station_id,
           e.codigo AS station_code,
           e.nombre AS station_name,
           e.activo AS station_active,
           un.activo AS unit_active,
           s.codigo AS state_code,
           s.nombre AS state_name,
           (un.activo = 1 AND s.codigo = %s AND e.activo = 1) AS eligible_now
    FROM despacho_unidades du
    INNER JOIN unidades un ON un.id_unidad = du.id_unidad
    INNER JOIN estaciones e ON e.id_estacion = un.id_estacion
    INNER JOIN tipos_unidad t ON t.id_tipo_unidad = un.id_tipo_unidad
    INNER JOIN estados_unidad s ON s.id_estado_unidad = un.id_estado_unidad
    WHERE du.id_despacho = %s
    ORDER BY un.codigo, un.id_unidad
"""


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


def _clean_dispatch(row):
    row["unit_count"] = int(row["unit_count"])
    return row


def list_dispatches(incident_id=None, state_id=None, state_code=None):
    """Lista despachos (más recientes primero) con filtros opcionales."""
    conditions = []
    params = []
    if state_code is not None:
        conditions.append("sd.codigo = %s")
        params.append(state_code)
    if incident_id is not None:
        conditions.append("d.id_incidente = %s")
        params.append(incident_id)
    if state_id is not None:
        conditions.append("d.id_estado_despacho = %s")
        params.append(state_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _fetch_all(
        _DISPATCH_SELECT + where + " ORDER BY d.id_despacho DESC", tuple(params)
    )
    return [_clean_dispatch(row) for row in rows]


def get_dispatch(dispatch_id):
    """Devuelve un despacho por id o None."""
    rows = _fetch_all(_DISPATCH_SELECT + " WHERE d.id_despacho = %s", (dispatch_id,))
    return _clean_dispatch(rows[0]) if rows else None


def list_draft_units(dispatch_id):
    """Unidades preseleccionadas con su estado operativo real actual."""
    rows = _fetch_all(_DRAFT_UNIT_SELECT, (AVAILABLE_UNIT_STATE_CODE, dispatch_id))
    for row in rows:
        for key in ("station_active", "unit_active", "eligible_now"):
            row[key] = bool(row[key])
    return rows


def list_available_units(dispatch_id=None, type_id=None, station_id=None, term=None):
    """Unidades activas, DISPONIBLE y de estación activa, aptas para preseleccionar."""
    conditions = ["u.activo = 1", "s.codigo = %s", "e.activo = 1"]
    params = [AVAILABLE_UNIT_STATE_CODE]
    if dispatch_id is not None:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM despacho_unidades du "
            "WHERE du.id_despacho = %s AND du.id_unidad = u.id_unidad)"
        )
        params.append(dispatch_id)
    if type_id is not None:
        conditions.append("u.id_tipo_unidad = %s")
        params.append(type_id)
    if station_id is not None:
        conditions.append("u.id_estacion = %s")
        params.append(station_id)
    if term:
        pattern = f"%{_escape_like(term)}%"
        conditions.append("(u.codigo LIKE %s OR u.nombre LIKE %s OR u.placa LIKE %s)")
        params.extend([pattern] * 3)
    return _fetch_all(
        """
        SELECT u.id_unidad AS unit_id, u.codigo AS unit_code, u.nombre AS unit_name,
               u.placa AS plate, t.id_tipo_unidad AS type_id, t.nombre AS type_name,
               e.id_estacion AS station_id, e.codigo AS station_code,
               e.nombre AS station_name, s.codigo AS state_code, s.nombre AS state_name
        FROM unidades u
        INNER JOIN estaciones e ON e.id_estacion = u.id_estacion
        INNER JOIN tipos_unidad t ON t.id_tipo_unidad = u.id_tipo_unidad
        INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
        WHERE """ + " AND ".join(conditions)
        + " ORDER BY e.codigo, u.codigo, u.id_unidad",
        tuple(params),
    )


def list_selectable_incidents():
    """Incidentes no terminales, del más reciente al más antiguo."""
    return _fetch_all(
        """
        SELECT i.id_incidente AS incident_id, i.codigo AS code,
               i.direccion AS address, i.fecha_reporte AS reported_at,
               s.id_estado_incidente AS state_id, s.codigo AS state_code,
               s.nombre AS state_name, t.nombre AS type_name, p.nombre AS priority_name
        FROM incidentes i
        INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
        INNER JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente
        INNER JOIN prioridades p ON p.id_prioridad = i.id_prioridad
        WHERE s.codigo NOT IN (%s, %s)
        ORDER BY i.fecha_reporte DESC, i.id_incidente DESC
        """,
        TERMINAL_INCIDENT_CODES,
    )


def list_dispatch_states():
    """Estados de despacho en su orden aprobado (solo lectura)."""
    return _fetch_all(
        "SELECT id_estado_despacho AS state_id, codigo AS code, nombre AS name,"
        " orden AS sort_order FROM estados_despacho ORDER BY orden, id_estado_despacho"
    )


def _translate_error(error):
    """Convierte errores de MySQL en excepciones del dominio."""
    message = str(error.msg or "")
    if error.errno == 1062:
        match = re.search(r"for key '([^']*)'\s*$", message)
        key = match.group(1).split(".")[-1] if match else ""
        if key == "uq_despacho_unidades_despacho_unidad":
            return DuplicateDraftUnitError("La unidad ya está preseleccionada en este despacho.")
    if error.errno == 1452:
        return InvalidReferenceError("Una referencia del despacho no existe.")
    if error.errno in (3819, 1264, 1406):
        return InvalidDispatchDataError("Los datos del despacho no son válidos.")
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


def _state_id_by_code(cursor, state_code):
    cursor.execute(
        "SELECT id_estado_despacho FROM estados_despacho WHERE codigo = %s",
        (state_code,),
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Estado de despacho no encontrado: {state_code}")
    return row["id_estado_despacho"]


def _audit(cursor, user_id, action, dispatch_id, detail):
    cursor.execute(
        """
        INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
        VALUES (%s, %s, 'DESPACHO', %s, %s)
        """,
        (user_id, action, dispatch_id, detail),
    )


def _normalize_observations(observations):
    if observations is None:
        return None
    if not isinstance(observations, str):
        raise InvalidDispatchDataError("Las observaciones no son válidas.")
    observations = observations.strip()
    if len(observations) > _MAX_OBSERVATIONS_LENGTH:
        raise InvalidDispatchDataError("Las observaciones son demasiado largas.")
    return observations or None


def _lock_incident_shared(cursor, incident_id):
    # Lock compartido: serializa contra la cancelación del incidente (FOR UPDATE)
    cursor.execute(
        """
        SELECT i.id_incidente, i.codigo, s.codigo AS state_code
        FROM incidentes i
        INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
        WHERE i.id_incidente = %s
        FOR SHARE OF i
        """,
        (incident_id,),
    )
    return cursor.fetchone()


def _require_ids(**ids):
    """Rechaza ids que no sean int (True no cuenta como 1)."""
    for name, value in ids.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidDispatchDataError(f"El identificador {name} no es válido.")


def _lock_draft(cursor, dispatch_id, reject_terminal_incident=False):
    """Bloquea incidente (S) y luego despacho (X); None si el despacho no existe.

    Lanza DispatchStateError si el despacho no está CREADO. Con
    reject_terminal_incident también rechaza si su incidente está cerrado o
    cancelado (la cancelación del borrador sí se permite en ese caso).
    """
    # El incidente de un despacho no cambia, por eso se lee sin bloqueo para ordenar locks
    cursor.execute(
        "SELECT id_incidente FROM despachos WHERE id_despacho = %s", (dispatch_id,)
    )
    head = cursor.fetchone()
    if head is None:
        return None
    incident = _lock_incident_shared(cursor, head["id_incidente"])
    cursor.execute(
        """
        SELECT d.id_despacho, d.id_incidente, d.observaciones, s.codigo AS state_code
        FROM despachos d
        INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
        WHERE d.id_despacho = %s
        FOR UPDATE OF d
        """,
        (dispatch_id,),
    )
    current = cursor.fetchone()
    if current is None:
        return None
    if current["state_code"] != DRAFT_STATE_CODE:
        raise DispatchStateError(_NOT_DRAFT_MESSAGE)
    if (reject_terminal_incident and incident is not None
            and incident["state_code"] in TERMINAL_INCIDENT_CODES):
        raise DraftIncidentClosedError(_TERMINAL_INCIDENT_MESSAGE)
    return current


def create_dispatch_draft(incident_id, observations, user_id):
    """Inserta un despacho CREADO con su bitácora; devuelve el nuevo id."""
    _require_ids(incident_id=incident_id, user_id=user_id)
    observations = _normalize_observations(observations)

    def operation(cursor):
        incident = _lock_incident_shared(cursor, incident_id)
        if incident is None:
            raise DispatchIncidentNotFoundError("El incidente no existe.")
        if incident["state_code"] in TERMINAL_INCIDENT_CODES:
            raise IncidentTerminalError(
                "No se puede crear un despacho para un incidente cerrado o cancelado."
            )
        state_id = _state_id_by_code(cursor, DRAFT_STATE_CODE)
        # fecha_despacho queda NULL: el borrador no compromete recursos
        cursor.execute(
            """
            INSERT INTO despachos (id_incidente, id_estado_despacho, id_usuario, observaciones)
            VALUES (%s, %s, %s, %s)
            """,
            (incident_id, state_id, user_id, observations),
        )
        new_id = cursor.lastrowid
        _audit(cursor, user_id, "CREAR", new_id,
               f"Borrador creado para {incident['codigo']}.")
        return new_id

    return _write(operation)


def update_dispatch_draft(dispatch_id, observations):
    """Edita las observaciones de un borrador; False si no existe."""
    _require_ids(dispatch_id=dispatch_id)
    observations = _normalize_observations(observations)

    def operation(cursor):
        # La regla del incidente terminal aplica antes del atajo idempotente
        current = _lock_draft(cursor, dispatch_id, reject_terminal_incident=True)
        if current is None:
            return False
        if current["observaciones"] == observations:
            # Idempotente: sin cambios no se escribe
            return True
        cursor.execute(
            "UPDATE despachos SET observaciones = %s WHERE id_despacho = %s",
            (observations, dispatch_id),
        )
        return True

    return _write(operation)


def _unit_ineligibility(unit):
    if not unit["activo"]:
        return "La unidad está inactiva y no puede preseleccionarse."
    if unit["state_code"] != AVAILABLE_UNIT_STATE_CODE:
        return (f"La unidad no está disponible (estado {unit['state_name']}) "
                "y no puede preseleccionarse.")
    if not unit["station_active"]:
        return "La estación de la unidad está inactiva y la unidad no puede preseleccionarse."
    return None


def add_unit_to_draft(dispatch_id, unit_id):
    """Preselecciona una unidad en un borrador; devuelve el id de despacho_unidades."""

    _require_ids(dispatch_id=dispatch_id, unit_id=unit_id)

    def operation(cursor):
        if _lock_draft(cursor, dispatch_id, reject_terminal_incident=True) is None:
            raise DispatchNotFoundError("El despacho no existe.")
        # Lock corto de lectura, no es una reserva: se libera al confirmar
        cursor.execute(
            """
            SELECT u.activo, s.codigo AS state_code, s.nombre AS state_name,
                   e.activo AS station_active
            FROM unidades u
            INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
            INNER JOIN estaciones e ON e.id_estacion = u.id_estacion
            WHERE u.id_unidad = %s
            FOR SHARE OF u
            """,
            (unit_id,),
        )
        unit = cursor.fetchone()
        if unit is None:
            raise UnitNotFoundError("La unidad no existe.")
        reason = _unit_ineligibility(unit)
        if reason:
            raise UnitNotEligibleError(reason)
        cursor.execute(
            "SELECT 1 FROM despacho_unidades WHERE id_despacho = %s AND id_unidad = %s",
            (dispatch_id, unit_id),
        )
        if cursor.fetchone() is not None:
            raise DuplicateDraftUnitError("La unidad ya está preseleccionada en este despacho.")
        # fecha_asignacion y id_estacion_origen quedan NULL: no hay participación activa
        cursor.execute(
            "INSERT INTO despacho_unidades (id_despacho, id_unidad) VALUES (%s, %s)",
            (dispatch_id, unit_id),
        )
        return cursor.lastrowid

    return _write(operation)


def remove_unit_from_draft(dispatch_id, unit_id):
    """Quita una preselección; False si el despacho no existe."""

    _require_ids(dispatch_id=dispatch_id, unit_id=unit_id)

    def operation(cursor):
        if _lock_draft(cursor, dispatch_id, reject_terminal_incident=True) is None:
            return False
        # Único borrado de la aplicación: solo preselecciones de un borrador CREADO
        cursor.execute(
            """
            DELETE du FROM despacho_unidades du
            INNER JOIN despachos d ON d.id_despacho = du.id_despacho
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE du.id_despacho = %s AND du.id_unidad = %s
              AND du.fecha_asignacion IS NULL AND s.codigo = %s
            """,
            (dispatch_id, unit_id, DRAFT_STATE_CODE),
        )
        if cursor.rowcount == 0:
            raise DraftUnitNotFoundError("La unidad no está preseleccionada en este despacho.")
        return True

    return _write(operation)


def cancel_dispatch_draft(dispatch_id, reason, user_id):
    """Cancela un despacho CREADO con motivo; False si no existe."""
    _require_ids(dispatch_id=dispatch_id, user_id=user_id)
    if not isinstance(reason, str):
        raise InvalidDispatchDataError("El motivo de cancelación no es válido.")
    reason = reason.strip()
    if not reason or len(reason) > _MAX_REASON_LENGTH:
        raise InvalidDispatchDataError("El motivo de cancelación no es válido.")

    def operation(cursor):
        if _lock_draft(cursor, dispatch_id) is None:
            return False
        cancelled_id = _state_id_by_code(cursor, CANCELLED_STATE_CODE)
        # fecha_despacho queda NULL y las preselecciones se conservan como histórico
        cursor.execute(
            """
            UPDATE despachos
            SET id_estado_despacho = %s, fecha_cancelacion = NOW(),
                motivo_cancelacion = %s
            WHERE id_despacho = %s
            """,
            (cancelled_id, reason, dispatch_id),
        )
        _audit(cursor, user_id, "CAMBIO_ESTADO", dispatch_id,
               f"CREADO → CANCELADO. Motivo: {reason}")
        return True

    return _write(operation)

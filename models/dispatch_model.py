import re

import mysql.connector

from config.database import get_connection
from models import unit_model

DRAFT_STATE_CODE = "CREADO"
CANCELLED_STATE_CODE = "CANCELADO"
ACTIVATED_STATE_CODE = "ACTIVO"
COMPLETED_STATE_CODE = "COMPLETADO"
# Participación activa (definición aprobada): asignada y sin liberar ni cancelar.
# El despacho debe estar además en estado ACTIVO.
ACTIVE_PARTICIPATION_PREDICATE = (
    "fecha_asignacion IS NOT NULL AND fecha_liberacion IS NULL "
    "AND fecha_cancelacion IS NULL"
)
DISPATCHED_UNIT_STATE_CODE = "DESPACHADA"
INCIDENT_REPORTED_CODE = "REPORTADO"
INCIDENT_ASSIGNED_CODE = "ASIGNADO"
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


class UnitStateError(DispatchStateError):
    """La unidad no está en el estado exacto que exige la transición."""


class NoActiveParticipationError(DispatchStateError):
    """La unidad no tiene participación activa en el despacho."""


class DispatchUnitNotFoundError(UnitNotFoundError):
    """La unidad no pertenece a ese despacho."""


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


class OperationDatesError(InvalidDispatchDataError):
    """Una restricción CHECK de fechas impidió la operación."""


class IncidentDatesError(OperationDatesError):
    """Una restricción CHECK de fechas del incidente impidió la operación."""


class DispatchHasNoUnitsError(InvalidDispatchDataError):
    """El borrador no tiene unidades preseleccionadas."""


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
           (un.activo = 1 AND s.codigo = %s AND e.activo = 1) AS eligible_now,
           du.fecha_asignacion AS assigned_at,
           du.fecha_salida AS departed_at,
           du.fecha_llegada_escena AS arrived_at,
           du.fecha_liberacion AS released_at,
           eo.codigo AS origin_station_code,
           eo.nombre AS origin_station_name
    FROM despacho_unidades du
    INNER JOIN unidades un ON un.id_unidad = du.id_unidad
    INNER JOIN estaciones e ON e.id_estacion = un.id_estacion
    INNER JOIN tipos_unidad t ON t.id_tipo_unidad = un.id_tipo_unidad
    INNER JOIN estados_unidad s ON s.id_estado_unidad = un.id_estado_unidad
    LEFT JOIN estaciones eo ON eo.id_estacion = du.id_estacion_origen
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


def _write(operation, isolation=None):
    """Ejecuta operation(cursor) en su propia transacción.

    isolation (opcional) fija el nivel solo para esta transacción.
    """
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            if isolation:
                connection.start_transaction(isolation_level=isolation)
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


def _expect_one_row(cursor, label):
    """Fuerza el rollback si un UPDATE de la operación no cambió exactamente una fila."""
    if cursor.rowcount != 1:
        raise RuntimeError(f"La operación no pudo actualizar la fila de {label}.")


def _audit_incident(cursor, user_id, incident_id, detail):
    cursor.execute(
        """
        INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
        VALUES (%s, 'CAMBIO_ESTADO', 'INCIDENTE', %s, %s)
        """,
        (user_id, incident_id, detail),
    )


def _unit_activation_problem(cursor, unit):
    """Motivo por el que la unidad no puede activarse, o None si está lista."""
    if not unit["activo"]:
        return "está inactiva"
    if unit["state_code"] != AVAILABLE_UNIT_STATE_CODE:
        return f"no está disponible (estado {unit['state_name']})"
    if not unit["station_active"]:
        return "pertenece a una estación inactiva"
    if unit_model._has_active_participation(cursor, unit["id_unidad"]):
        return "tiene una participación activa en otro despacho"
    return None


def activate_dispatch(dispatch_id, user_id):
    """Activa un despacho CREADO en una sola transacción; False si no existe."""
    _require_ids(dispatch_id=dispatch_id, user_id=user_id)

    def operation(cursor):
        cursor.execute(
            "SELECT id_incidente FROM despachos WHERE id_despacho = %s", (dispatch_id,)
        )
        head = cursor.fetchone()
        if head is None:
            return False
        incident_id = head["id_incidente"]
        # Orden de bloqueo: incidente -> despacho -> unidades (por id) -> sus despacho_unidades.
        # Se ejecuta en READ COMMITTED: sin bloqueos de hueco en despacho_unidades (que
        # chocarían con add_unit_to_draft de otro despacho) y con lecturas siempre frescas.
        cursor.execute(
            """
            SELECT i.id_incidente, i.codigo, i.fecha_asignacion, s.codigo AS state_code
            FROM incidentes i
            INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
            WHERE i.id_incidente = %s
            FOR UPDATE OF i
            """,
            (incident_id,),
        )
        incident = cursor.fetchone()
        if incident["state_code"] in TERMINAL_INCIDENT_CODES:
            raise IncidentTerminalError(
                "No se puede activar un despacho de un incidente cerrado o cancelado."
            )
        cursor.execute(
            """
            SELECT s.codigo AS state_code
            FROM despachos d
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE d.id_despacho = %s
            FOR UPDATE OF d
            """,
            (dispatch_id,),
        )
        if cursor.fetchone()["state_code"] != DRAFT_STATE_CODE:
            raise DispatchStateError("Solo se puede activar un despacho en estado Creado.")
        # Lectura simple: el bloqueo del despacho ya congela sus preselecciones
        cursor.execute(
            "SELECT id_unidad FROM despacho_unidades WHERE id_despacho = %s "
            "ORDER BY id_unidad",
            (dispatch_id,),
        )
        unit_ids = [row["id_unidad"] for row in cursor.fetchall()]
        if not unit_ids:
            raise DispatchHasNoUnitsError("El borrador no tiene unidades preseleccionadas.")
        placeholders = ", ".join(["%s"] * len(unit_ids))
        cursor.execute(
            f"""
            SELECT u.id_unidad, u.codigo, u.activo, u.id_estacion,
                   s.codigo AS state_code, s.nombre AS state_name,
                   e.activo AS station_active
            FROM unidades u
            INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
            INNER JOIN estaciones e ON e.id_estacion = u.id_estacion
            WHERE u.id_unidad IN ({placeholders})
            ORDER BY u.id_unidad
            FOR UPDATE OF u
            """,
            tuple(unit_ids),
        )
        units = cursor.fetchall()
        if len(units) != len(unit_ids):
            raise UnitNotFoundError("Una unidad preseleccionada ya no existe.")
        cursor.execute(
            "SELECT id_despacho_unidad, id_unidad FROM despacho_unidades "
            "WHERE id_despacho = %s ORDER BY id_unidad FOR UPDATE",
            (dispatch_id,),
        )
        participations = {row["id_unidad"]: row["id_despacho_unidad"]
                          for row in cursor.fetchall()}
        # Todo o nada: la primera unidad inválida (por id) aborta la activación
        for unit in units:
            problem = _unit_activation_problem(cursor, unit)
            if problem:
                raise UnitNotEligibleError(f"La unidad {unit['codigo']} {problem}.")
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
        active_id = _state_id_by_code(cursor, ACTIVATED_STATE_CODE)
        draft_id = _state_id_by_code(cursor, DRAFT_STATE_CODE)
        cursor.execute(
            "UPDATE despachos SET id_estado_despacho = %s, fecha_despacho = %s "
            "WHERE id_despacho = %s AND id_estado_despacho = %s",
            (active_id, moment, dispatch_id, draft_id),
        )
        _expect_one_row(cursor, "despacho")
        cursor.execute(
            "SELECT id_estado_unidad FROM estados_unidad WHERE codigo = %s",
            (DISPATCHED_UNIT_STATE_CODE,),
        )
        dispatched_id = cursor.fetchone()["id_estado_unidad"]
        cursor.execute(
            "SELECT id_estado_unidad FROM estados_unidad WHERE codigo = %s",
            (AVAILABLE_UNIT_STATE_CODE,),
        )
        available_id = cursor.fetchone()["id_estado_unidad"]
        for unit in units:
            # Estación de origen congelada: la actual de la unidad bajo lock
            cursor.execute(
                "UPDATE despacho_unidades SET fecha_asignacion = %s, id_estacion_origen = %s "
                "WHERE id_despacho_unidad = %s AND fecha_asignacion IS NULL",
                (moment, unit["id_estacion"], participations[unit["id_unidad"]]),
            )
            _expect_one_row(cursor, "participación")
            cursor.execute(
                "UPDATE unidades SET id_estado_unidad = %s "
                "WHERE id_unidad = %s AND id_estado_unidad = %s",
                (dispatched_id, unit["id_unidad"], available_id),
            )
            _expect_one_row(cursor, "unidad")
        moved_incident = incident["state_code"] == INCIDENT_REPORTED_CODE
        if moved_incident:
            cursor.execute(
                "SELECT id_estado_incidente FROM estados_incidente WHERE codigo = %s",
                (INCIDENT_ASSIGNED_CODE,),
            )
            assigned_id = cursor.fetchone()["id_estado_incidente"]
            try:
                cursor.execute(
                    "UPDATE incidentes SET id_estado_incidente = %s, "
                    "fecha_asignacion = COALESCE(fecha_asignacion, %s) WHERE id_incidente = %s",
                    (assigned_id, moment, incident_id),
                )
            except mysql.connector.Error as error:
                if error.errno == 3819:
                    raise IncidentDatesError(
                        "Las fechas del incidente no son coherentes con la activación."
                    ) from error
                raise
        _audit(cursor, user_id, "CAMBIO_ESTADO", dispatch_id,
               f"CREADO → ACTIVO para {incident['codigo']}.")
        if moved_incident:
            _audit_incident(cursor, user_id, incident_id,
                            f"REPORTADO → ASIGNADO por activación del despacho #{dispatch_id}.")
        return True

    return _write(operation, isolation="READ COMMITTED")


def _audit_unit(cursor, user_id, unit_id, detail):
    cursor.execute(
        """
        INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
        VALUES (%s, 'CAMBIO_ESTADO', 'UNIDAD', %s, %s)
        """,
        (user_id, unit_id, detail),
    )


# Columna de despacho_unidades que registra cada transición (valores internos fijos)
_TRANSITIONS = {
    "en_route": dict(
        source="DESPACHADA", target="EN_RUTA", column="fecha_salida",
        detail="DESPACHADA → EN_RUTA; salida registrada (despacho #{id}).",
    ),
    "on_scene": dict(
        source="EN_RUTA", target="EN_ESCENA", column="fecha_llegada_escena",
        detail="EN_RUTA → EN_ESCENA; llegada registrada (despacho #{id}).",
    ),
    "return": dict(
        source="EN_ESCENA", target="REGRESANDO", column="fecha_liberacion",
        detail="EN_ESCENA → REGRESANDO por liberación normal (despacho #{id}).",
    ),
    "available": dict(
        source="REGRESANDO", target="DISPONIBLE", column=None,
        detail="REGRESANDO → DISPONIBLE (despacho #{id}).",
    ),
}
_ACTIVE_DISPATCH_MESSAGE = "Solo se pueden operar unidades de un despacho en estado Activo."
_RETURN_DISPATCH_MESSAGE = (
    "Solo se puede cerrar el regreso de unidades de un despacho Activo, Completado o Cancelado."
)


def _count_active_participations(cursor, dispatch_id):
    """Participaciones activas restantes del despacho, con lectura actual (no snapshot)."""
    cursor.execute(
        "SELECT id_despacho_unidad FROM despacho_unidades WHERE id_despacho = %s AND "
        + ACTIVE_PARTICIPATION_PREDICATE + " FOR SHARE",
        (dispatch_id,),
    )
    return len(cursor.fetchall())


def _complete_dispatch_if_no_active_participations(cursor, dispatch_id, moment, user_id):
    """ACTIVO -> COMPLETADO si ya no quedan participaciones activas.

    Corre dentro de la transacción del llamador (sin commit ni rollback), que debe
    tener bloqueado el despacho FOR UPDATE. Devuelve True si lo completó.
    """
    # Lectura bloqueante: ve lo confirmado por otras transacciones y lo propio
    if _count_active_participations(cursor, dispatch_id) > 0:
        return False
    cursor.execute(
        """
        SELECT s.codigo AS state_code
        FROM despachos d
        INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
        WHERE d.id_despacho = %s
        FOR UPDATE OF d
        """,
        (dispatch_id,),
    )
    row = cursor.fetchone()
    if row is None or row["state_code"] != ACTIVATED_STATE_CODE:
        return False
    completed_id = _state_id_by_code(cursor, COMPLETED_STATE_CODE)
    active_id = _state_id_by_code(cursor, ACTIVATED_STATE_CODE)
    # Misma marca de tiempo que la liberación: fecha_finalizacion >= fecha_despacho
    cursor.execute(
        "UPDATE despachos SET id_estado_despacho = %s, fecha_finalizacion = %s "
        "WHERE id_despacho = %s AND id_estado_despacho = %s",
        (completed_id, moment, dispatch_id, active_id),
    )
    _expect_one_row(cursor, "despacho")
    _audit(cursor, user_id, "CAMBIO_ESTADO", dispatch_id,
           "ACTIVO → COMPLETADO: sin participaciones activas.")
    return True


def _unit_state_id(cursor, state_code):
    cursor.execute(
        "SELECT id_estado_unidad FROM estados_unidad WHERE codigo = %s", (state_code,)
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Estado de unidad no encontrado: {state_code}")
    return row["id_estado_unidad"]


def _operate_unit(kind, dispatch_id, unit_id, user_id):
    """Aplica una transición operativa de la unidad en una transacción corta."""
    _require_ids(dispatch_id=dispatch_id, unit_id=unit_id, user_id=user_id)
    step = _TRANSITIONS[kind]
    arrival = kind == "on_scene"
    returning_done = kind == "available"

    def operation(cursor):
        cursor.execute(
            "SELECT id_incidente FROM despachos WHERE id_despacho = %s", (dispatch_id,)
        )
        head = cursor.fetchone()
        if head is None:
            raise DispatchNotFoundError("El despacho no existe.")
        incident = None
        # Orden de bloqueo: incidente (solo si puede cambiar) -> despacho -> unidad -> su
        # participación; igual que activate_dispatch y unit_model (unidad antes que du)
        if arrival:
            cursor.execute(
                """
                SELECT i.codigo, i.fecha_inicio_atencion, s.codigo AS state_code
                FROM incidentes i
                INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
                WHERE i.id_incidente = %s
                FOR UPDATE OF i
                """,
                (head["id_incidente"],),
            )
            incident = cursor.fetchone()
        # Solo importa que el estado ACTIVO del despacho sea estable durante la transacción;
        # al iniciar el regreso se bloquea FOR UPDATE (despacho -> unidad -> du) para que dos
        # liberaciones simultáneas se serialicen y la última vea a la otra ya liberada
        dispatch_query = """
            SELECT s.codigo AS state_code
            FROM despachos d
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE d.id_despacho = %s
            """
        if kind == "return":
            dispatch_query += "FOR UPDATE OF d"
        elif not returning_done:
            dispatch_query += "FOR SHARE OF d"
        cursor.execute(dispatch_query, (dispatch_id,))
        dispatch_state = cursor.fetchone()["state_code"]
        if returning_done:
            allowed = (ACTIVATED_STATE_CODE, COMPLETED_STATE_CODE, CANCELLED_STATE_CODE)
        else:
            allowed = (ACTIVATED_STATE_CODE,)
        if dispatch_state not in allowed:
            raise DispatchStateError(
                _RETURN_DISPATCH_MESSAGE if returning_done else _ACTIVE_DISPATCH_MESSAGE
            )
        cursor.execute(
            """
            SELECT u.codigo, s.codigo AS state_code, s.nombre AS state_name
            FROM unidades u
            INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
            WHERE u.id_unidad = %s
            FOR UPDATE OF u
            """,
            (unit_id,),
        )
        unit = cursor.fetchone()
        if unit is None:
            raise UnitNotFoundError("La unidad no existe.")
        cursor.execute(
            """
            SELECT id_despacho_unidad, fecha_asignacion, fecha_liberacion, fecha_cancelacion
            FROM despacho_unidades
            WHERE id_despacho = %s AND id_unidad = %s
            FOR UPDATE
            """,
            (dispatch_id, unit_id),
        )
        part = cursor.fetchone()
        if part is None:
            raise DispatchUnitNotFoundError("La unidad no pertenece a este despacho.")
        if returning_done:
            # Terminada por liberación normal o por cancelación (nunca ambas por CHECK)
            terminated = part["fecha_asignacion"] is not None and (
                part["fecha_liberacion"] is not None or part["fecha_cancelacion"] is not None)
            if not terminated:
                raise NoActiveParticipationError(
                    "La participación de la unidad no fue liberada ni cancelada en este despacho."
                )
        elif (part["fecha_asignacion"] is None or part["fecha_liberacion"] is not None
              or part["fecha_cancelacion"] is not None):
            raise NoActiveParticipationError(
                "La unidad no tiene una participación activa en este despacho."
            )
        if unit["state_code"] != step["source"]:
            raise UnitStateError(
                f"La unidad {unit['codigo']} está en estado {unit['state_name']} "
                f"y esta acción requiere el estado {step['source']}."
            )
        if arrival and incident["state_code"] not in ("ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            raise DispatchStateError(
                "El incidente no admite la llegada de unidades en su estado actual."
            )
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
        if step["column"]:
            try:
                cursor.execute(
                    f"UPDATE despacho_unidades SET {step['column']} = %s "
                    f"WHERE id_despacho_unidad = %s AND {step['column']} IS NULL",
                    (moment, part["id_despacho_unidad"]),
                )
            except mysql.connector.Error as error:
                if error.errno == 3819:
                    raise OperationDatesError(
                        "Las fechas de la participación no son coherentes."
                    ) from error
                raise
            _expect_one_row(cursor, "participación")
        cursor.execute(
            "UPDATE unidades SET id_estado_unidad = %s "
            "WHERE id_unidad = %s AND id_estado_unidad = %s",
            (_unit_state_id(cursor, step["target"]), unit_id,
             _unit_state_id(cursor, step["source"])),
        )
        _expect_one_row(cursor, "unidad")
        _audit_unit(cursor, user_id, unit_id, step["detail"].format(id=dispatch_id))
        if kind == "return":
            _complete_dispatch_if_no_active_participations(
                cursor, dispatch_id, moment, user_id)
        if arrival and incident["state_code"] == "ASIGNADO":
            cursor.execute(
                "SELECT id_estado_incidente FROM estados_incidente WHERE codigo = %s",
                ("EN_ATENCION",),
            )
            attention_id = cursor.fetchone()["id_estado_incidente"]
            try:
                cursor.execute(
                    "UPDATE incidentes SET id_estado_incidente = %s, "
                    "fecha_inicio_atencion = COALESCE(fecha_inicio_atencion, %s) "
                    "WHERE id_incidente = %s",
                    (attention_id, moment, head["id_incidente"]),
                )
            except mysql.connector.Error as error:
                if error.errno == 3819:
                    raise IncidentDatesError(
                        "Las fechas del incidente no son coherentes con la llegada."
                    ) from error
                raise
            _audit_incident(cursor, user_id, head["id_incidente"],
                            "ASIGNADO → EN_ATENCION por primera llegada a escena.")
        return True

    return _write(operation)


def mark_unit_en_route(dispatch_id, unit_id, user_id):
    """DESPACHADA -> EN_RUTA con fecha_salida."""
    return _operate_unit("en_route", dispatch_id, unit_id, user_id)


def mark_unit_on_scene(dispatch_id, unit_id, user_id):
    """EN_RUTA -> EN_ESCENA; la primera llegada mueve el incidente a EN_ATENCION."""
    return _operate_unit("on_scene", dispatch_id, unit_id, user_id)


def start_unit_return(dispatch_id, unit_id, user_id):
    """EN_ESCENA -> REGRESANDO; libera la participación (fecha_liberacion)."""
    return _operate_unit("return", dispatch_id, unit_id, user_id)


def mark_unit_available(dispatch_id, unit_id, user_id):
    """REGRESANDO -> DISPONIBLE sobre una participación ya liberada."""
    return _operate_unit("available", dispatch_id, unit_id, user_id)


# Unidad de una participación cancelada: estado actual -> nuevo estado (máquina aprobada)
_CANCEL_UNIT_TRANSITIONS = {
    "DESPACHADA": "DISPONIBLE",
    "EN_RUTA": "REGRESANDO",
    "EN_ESCENA": "REGRESANDO",
}


def _collect_active_participations(cursor, dispatch_ids):
    """Participaciones activas de los despachos (lectura actual), por despacho y unidad."""
    if not dispatch_ids:
        return []
    placeholders = ", ".join(["%s"] * len(dispatch_ids))
    cursor.execute(
        f"SELECT id_despacho_unidad, id_despacho, id_unidad FROM despacho_unidades "
        f"WHERE id_despacho IN ({placeholders}) AND " + ACTIVE_PARTICIPATION_PREDICATE
        + " ORDER BY id_despacho, id_unidad FOR SHARE",
        tuple(dispatch_ids),
    )
    return cursor.fetchall()


def _lock_units_ascending(cursor, unit_ids):
    """Bloquea las unidades por id ascendente; devuelve {id: fila}."""
    unit_ids = sorted(set(unit_ids))
    if not unit_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(unit_ids))
    cursor.execute(
        f"""
        SELECT u.id_unidad, u.codigo, s.codigo AS state_code
        FROM unidades u
        INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
        WHERE u.id_unidad IN ({placeholders})
        ORDER BY u.id_unidad
        FOR UPDATE OF u
        """,
        tuple(unit_ids),
    )
    return {row["id_unidad"]: row for row in cursor.fetchall()}


def _cancel_active_dispatch_in_tx(cursor, dispatch_id, reason, moment, user_id,
                                  participations=None, units=None):
    """ACTIVO -> CANCELADO dentro de la transacción del llamador (sin commit).

    El despacho debe estar ya bloqueado FOR UPDATE. Si el llamador ya reunió y
    bloqueó las unidades (cancelación global del incidente) las pasa en
    participations/units; si no, se reúnen aquí. Nunca completa el despacho.
    """
    if participations is None:
        participations = _collect_active_participations(cursor, [dispatch_id])
        units = _lock_units_ascending(cursor, [p["id_unidad"] for p in participations])
    cancelled_id = _state_id_by_code(cursor, CANCELLED_STATE_CODE)
    active_id = _state_id_by_code(cursor, ACTIVATED_STATE_CODE)
    for part in participations:
        if part["id_despacho"] != dispatch_id:
            continue
        # La participación queda cancelada (no liberada, por ck_du_liberacion_cancelacion_excluyentes)
        cursor.execute(
            "UPDATE despacho_unidades SET fecha_cancelacion = %s, motivo_cancelacion = %s "
            "WHERE id_despacho_unidad = %s AND fecha_cancelacion IS NULL "
            "AND fecha_liberacion IS NULL",
            (moment, reason, part["id_despacho_unidad"]),
        )
        _expect_one_row(cursor, "participación")
        unit = units[part["id_unidad"]]
        target = _CANCEL_UNIT_TRANSITIONS.get(unit["state_code"])
        if target is None:
            continue
        cursor.execute(
            "UPDATE unidades SET id_estado_unidad = %s "
            "WHERE id_unidad = %s AND id_estado_unidad = %s",
            (_unit_state_id(cursor, target), part["id_unidad"],
             _unit_state_id(cursor, unit["state_code"])),
        )
        _expect_one_row(cursor, "unidad")
        _audit_unit(cursor, user_id, part["id_unidad"],
                    f"{unit['state_code']} → {target} por cancelación del despacho #{dispatch_id}.")
        unit["state_code"] = target
    cursor.execute(
        "UPDATE despachos SET id_estado_despacho = %s, fecha_cancelacion = %s, "
        "motivo_cancelacion = %s WHERE id_despacho = %s AND id_estado_despacho = %s",
        (cancelled_id, moment, reason, dispatch_id, active_id),
    )
    _expect_one_row(cursor, "despacho")
    _audit(cursor, user_id, "CAMBIO_ESTADO", dispatch_id,
           f"ACTIVO → CANCELADO. Motivo: {reason}")


def cancel_active_dispatch(dispatch_id, reason, user_id):
    """Cancela un despacho ACTIVO con motivo; False si no existe."""
    _require_ids(dispatch_id=dispatch_id, user_id=user_id)
    if not isinstance(reason, str):
        raise InvalidDispatchDataError("El motivo de cancelación no es válido.")
    reason = reason.strip()
    if not reason or len(reason) > _MAX_REASON_LENGTH:
        raise InvalidDispatchDataError("El motivo de cancelación no es válido.")

    def operation(cursor):
        cursor.execute(
            "SELECT id_incidente FROM despachos WHERE id_despacho = %s", (dispatch_id,)
        )
        head = cursor.fetchone()
        if head is None:
            return False
        # Orden de bloqueo: incidente (S, no se modifica) -> despacho (X) -> unidades por id
        # -> participaciones; igual que la cancelación global del incidente
        _lock_incident_shared(cursor, head["id_incidente"])
        cursor.execute(
            """
            SELECT s.codigo AS state_code
            FROM despachos d
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE d.id_despacho = %s
            FOR UPDATE OF d
            """,
            (dispatch_id,),
        )
        if cursor.fetchone()["state_code"] != ACTIVATED_STATE_CODE:
            raise DispatchStateError("Solo se puede cancelar con esta acción un despacho Activo.")
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
        _cancel_active_dispatch_in_tx(cursor, dispatch_id, reason, moment, user_id)
        return True

    # READ COMMITTED: sin bloqueos de hueco que choquen con add_unit_to_draft de otro despacho
    return _write(operation, isolation="READ COMMITTED")

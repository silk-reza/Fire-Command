import re
from decimal import Decimal

import mysql.connector

from config.database import get_connection
from models import dispatch_model

INITIAL_STATE_CODE = "REPORTADO"
CANCELLED_STATE_CODE = "CANCELADO"
# Estados terminales: no admiten modificaciones operativas normales
TERMINAL_STATE_CODES = ("CERRADO", "CANCELADO")
DISPATCH_DRAFT_CODE = "CREADO"
DISPATCH_ACTIVE_CODE = "ACTIVO"
DISPATCH_CANCELLED_CODE = "CANCELADO"
_MAX_REASON_LENGTH = 250

# Campo del modelo -> columna física; única lista de campos editables
_EDITABLE_COLUMNS = {
    "type_id": "id_tipo_incidente",
    "priority_id": "id_prioridad",
    "address": "direccion",
    "reference": "referencia",
    "latitude": "latitud",
    "longitude": "longitud",
    "description": "descripcion",
    "affected_people": "personas_afectadas",
}


class DuplicateIncidentCodeError(Exception):
    """El código de incidente ya existe."""


class InvalidReferenceError(Exception):
    """Una referencia (FK) no existe."""


class InvalidTypeError(Exception):
    """El tipo de incidente indicado no existe."""


class InactiveTypeError(Exception):
    """El tipo de incidente existe pero está inactivo."""


class InvalidPriorityError(Exception):
    """La prioridad indicada no existe."""


class InactivePriorityError(Exception):
    """La prioridad existe pero está inactiva."""


class InvalidIncidentDataError(Exception):
    """MySQL rechazó los datos por una restricción CHECK o de tipo."""


class IncidentStateError(Exception):
    """El estado actual del incidente no admite la operación."""


_INCIDENT_SELECT = """
    SELECT i.id_incidente AS incident_id,
           i.codigo AS code,
           t.id_tipo_incidente AS type_id,
           t.nombre AS type_name,
           p.id_prioridad AS priority_id,
           p.nombre AS priority_name,
           p.nivel AS priority_level,
           s.id_estado_incidente AS state_id,
           s.codigo AS state_code,
           s.nombre AS state_name,
           i.fecha_reporte AS reported_at,
           i.direccion AS address,
           i.referencia AS reference,
           i.latitud AS latitude,
           i.longitud AS longitude,
           i.descripcion AS description,
           i.personas_afectadas AS affected_people,
           u.id_usuario AS registered_by_id,
           u.nombre_usuario AS registered_by_username,
           i.fecha_asignacion AS assigned_at,
           i.fecha_inicio_atencion AS attention_started_at,
           i.fecha_control AS controlled_at,
           i.fecha_cierre AS closed_at,
           i.fecha_cancelacion AS cancelled_at,
           i.motivo_cancelacion AS cancel_reason
    FROM incidentes i
    INNER JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente
    INNER JOIN prioridades p ON p.id_prioridad = i.id_prioridad
    INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
    INNER JOIN usuarios u ON u.id_usuario = i.id_usuario_registro
"""

_ORDER = " ORDER BY i.fecha_reporte DESC, i.id_incidente DESC"


def _clean_incident(row):
    for key in ("latitude", "longitude"):
        if row[key] is not None:
            row[key] = float(row[key])
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


def search_incidents(term=None, type_id=None, priority_id=None, state_id=None,
                     date_from=None, date_to=None):
    """Busca por código, descripción o dirección, combinando filtros con AND.

    date_from y date_to son datetime inclusivos sobre fecha_reporte (o None).
    """
    conditions = []
    params = []
    if term:
        pattern = f"%{_escape_like(term)}%"
        conditions.append(
            "(i.codigo LIKE %s OR i.descripcion LIKE %s OR i.direccion LIKE %s)"
        )
        params.extend([pattern] * 3)
    if type_id is not None:
        conditions.append("i.id_tipo_incidente = %s")
        params.append(type_id)
    if priority_id is not None:
        conditions.append("i.id_prioridad = %s")
        params.append(priority_id)
    if state_id is not None:
        conditions.append("i.id_estado_incidente = %s")
        params.append(state_id)
    if date_from is not None:
        conditions.append("i.fecha_reporte >= %s")
        params.append(date_from)
    if date_to is not None:
        conditions.append("i.fecha_reporte <= %s")
        params.append(date_to)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _fetch_all(_INCIDENT_SELECT + where + _ORDER, tuple(params))
    return [_clean_incident(row) for row in rows]


def get_incident(incident_id):
    """Devuelve un incidente por id o None."""
    rows = _fetch_all(_INCIDENT_SELECT + " WHERE i.id_incidente = %s", (incident_id,))
    return _clean_incident(rows[0]) if rows else None


def list_incident_types():
    """Todos los tipos de incidente, con su bandera de activo."""
    rows = _fetch_all(
        "SELECT id_tipo_incidente AS type_id, nombre AS name, descripcion AS description,"
        " activo AS active FROM tipos_incidente ORDER BY nombre, id_tipo_incidente"
    )
    for row in rows:
        row["active"] = bool(row["active"])
    return rows


def list_priorities():
    """Todas las prioridades, con nivel y bandera de activo."""
    rows = _fetch_all(
        "SELECT id_prioridad AS priority_id, nombre AS name, nivel AS level,"
        " descripcion AS description, activo AS active"
        " FROM prioridades ORDER BY nivel, id_prioridad"
    )
    for row in rows:
        row["active"] = bool(row["active"])
    return rows


def list_incident_states():
    """Estados de incidente en su orden aprobado (solo lectura)."""
    return _fetch_all(
        "SELECT id_estado_incidente AS state_id, codigo AS code, nombre AS name,"
        " orden AS sort_order FROM estados_incidente ORDER BY orden, id_estado_incidente"
    )


def _translate_error(error):
    """Convierte errores de MySQL en excepciones del dominio."""
    message = str(error.msg or "")
    if error.errno == 1062:
        match = re.search(r"for key '([^']*)'\s*$", message)
        key = match.group(1).split(".")[-1] if match else ""
        if key == "uq_incidentes_codigo":
            return DuplicateIncidentCodeError("Ya existe un incidente con ese código.")
    if error.errno == 1452:
        return InvalidReferenceError("Una referencia del incidente no existe.")
    if error.errno in (3819, 1264, 1406):
        return InvalidIncidentDataError("Los datos del incidente no son válidos.")
    return None


def _write(operation, isolation=None):
    """Ejecuta operation(cursor) en su propia transacción (isolation opcional)."""
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


def _ensure_type_active(cursor, type_id):
    """Verifica en la misma transacción que el tipo exista y esté activo."""
    cursor.execute(
        "SELECT activo FROM tipos_incidente WHERE id_tipo_incidente = %s FOR SHARE",
        (type_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise InvalidTypeError("El tipo de incidente indicado no existe.")
    if not row["activo"]:
        raise InactiveTypeError("No se puede asignar un tipo de incidente inactivo.")


def _ensure_priority_active(cursor, priority_id):
    """Verifica en la misma transacción que la prioridad exista y esté activa."""
    cursor.execute(
        "SELECT activo FROM prioridades WHERE id_prioridad = %s FOR SHARE",
        (priority_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise InvalidPriorityError("La prioridad indicada no existe.")
    if not row["activo"]:
        raise InactivePriorityError("No se puede asignar una prioridad inactiva.")


def _state_id_by_code(cursor, state_code):
    cursor.execute(
        "SELECT id_estado_incidente FROM estados_incidente WHERE codigo = %s",
        (state_code,),
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Estado de incidente no encontrado: {state_code}")
    return row["id_estado_incidente"]


def _audit(cursor, user_id, action, incident_id, detail):
    cursor.execute(
        """
        INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
        VALUES (%s, %s, 'INCIDENTE', %s, %s)
        """,
        (user_id, action, incident_id, detail),
    )


def create_incident(code, type_id, priority_id, address, reference, latitude,
                    longitude, description, affected_people, user_id):
    """Inserta un incidente REPORTADO con su bitácora; devuelve el nuevo id."""

    def operation(cursor):
        _ensure_type_active(cursor, type_id)
        _ensure_priority_active(cursor, priority_id)
        state_id = _state_id_by_code(cursor, INITIAL_STATE_CODE)
        # La fecha de reporte siempre la fija el servidor
        cursor.execute(
            """
            INSERT INTO incidentes
                (codigo, id_tipo_incidente, id_prioridad, id_estado_incidente,
                 id_usuario_registro, fecha_reporte, direccion, referencia,
                 latitud, longitud, descripcion, personas_afectadas)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s)
            """,
            (code, type_id, priority_id, state_id, user_id, address,
             reference or None, latitude, longitude, description,
             0 if affected_people is None else affected_people),
        )
        new_id = cursor.lastrowid
        _audit(cursor, user_id, "CREAR", new_id, f"Registro inicial de {code}.")
        return new_id

    return _write(operation)


def _lock_incident(cursor, incident_id):
    cursor.execute(
        """
        SELECT i.id_incidente, i.codigo, i.id_tipo_incidente, i.id_prioridad,
               i.direccion, i.referencia, i.latitud, i.longitud, i.descripcion,
               i.personas_afectadas, i.fecha_control, s.codigo AS state_code
        FROM incidentes i
        INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
        WHERE i.id_incidente = %s
        FOR UPDATE OF i
        """,
        (incident_id,),
    )
    return cursor.fetchone()


def _same_value(old, new):
    if old is None or new is None:
        return old is None and new is None
    if isinstance(old, Decimal) or isinstance(new, Decimal):
        return Decimal(str(old)) == Decimal(str(new))
    return old == new


def update_incident(incident_id, fields, user_id):
    """Edita solo campos descriptivos permitidos; False si no existe."""
    unknown = set(fields) - set(_EDITABLE_COLUMNS)
    if unknown:
        raise ValueError(f"Campos no editables: {', '.join(sorted(unknown))}")

    def operation(cursor):
        current = _lock_incident(cursor, incident_id)
        if current is None:
            return False
        if current["state_code"] in TERMINAL_STATE_CODES:
            raise IncidentStateError(
                "Un incidente cerrado o cancelado no admite modificaciones."
            )
        new_values = dict(fields)
        if "reference" in new_values:
            new_values["reference"] = new_values["reference"] or None
        changed = [
            key for key, column in _EDITABLE_COLUMNS.items()
            if key in new_values and not _same_value(current[column], new_values[key])
        ]
        if not changed:
            # Idempotente: sin cambios no se escribe ni se audita
            return True
        # Las referencias solo se validan cuando cambian (se permite conservar una inactiva)
        if "type_id" in changed:
            _ensure_type_active(cursor, new_values["type_id"])
        if "priority_id" in changed:
            _ensure_priority_active(cursor, new_values["priority_id"])
        assignments = ", ".join(f"{_EDITABLE_COLUMNS[key]} = %s" for key in changed)
        params = [new_values[key] for key in changed] + [incident_id]
        cursor.execute(
            f"UPDATE incidentes SET {assignments} WHERE id_incidente = %s",
            tuple(params),
        )
        names = ", ".join(_EDITABLE_COLUMNS[key] for key in changed)
        _audit(cursor, user_id, "MODIFICAR", incident_id, f"Campos modificados: {names}.")
        return True

    return _write(operation)


def _cancel_pending_dispatches(cursor, incident_id, incident_code, reason, user_id,
                               moment=None, allow_active=False):
    """Resuelve los despachos CREADO (y ACTIVO si se permite) del incidente que se cancela."""
    if moment is None:
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
    # Los ids de estado se leen antes, sin bloqueo, para que el bloqueo no dependa de un JOIN
    cursor.execute(
        "SELECT id_estado_despacho, codigo FROM estados_despacho WHERE codigo IN (%s, %s, %s)",
        (DISPATCH_DRAFT_CODE, DISPATCH_ACTIVE_CODE, DISPATCH_CANCELLED_CODE),
    )
    state_ids = {row["codigo"]: row["id_estado_despacho"] for row in cursor.fetchall()}
    # Orden de bloqueo: incidente (ya bloqueado), sus despachos por id ascendente, luego las
    # unidades por id ascendente y por último las participaciones
    cursor.execute(
        """
        SELECT id_despacho, id_estado_despacho
        FROM despachos
        WHERE id_incidente = %s
        ORDER BY id_despacho
        FOR UPDATE
        """,
        (incident_id,),
    )
    all_dispatches = cursor.fetchall()
    active = [r for r in all_dispatches
              if r["id_estado_despacho"] == state_ids[DISPATCH_ACTIVE_CODE]]
    # Un REPORTADO con despacho ACTIVO es inconsistente: no se cancela desde aquí
    if active and not allow_active:
        raise IncidentStateError(
            "El incidente tiene despachos activos y no puede cancelarse desde este módulo."
        )
    dispatches = [
        r for r in all_dispatches
        if r["id_estado_despacho"] == state_ids[DISPATCH_DRAFT_CODE]
    ]
    participations = units = None
    if active:
        # Todas las unidades afectadas se bloquean antes de modificar participaciones
        participations = dispatch_model._collect_active_participations(
            cursor, [r["id_despacho"] for r in active])
        units = dispatch_model._lock_units_ascending(
            cursor, [p["id_unidad"] for p in participations])
    cancelled_dispatch_id = state_ids[DISPATCH_CANCELLED_CODE]
    for row in dispatches:
        # fecha_despacho queda NULL y las preselecciones no se tocan
        cursor.execute(
            """
            UPDATE despachos
            SET id_estado_despacho = %s, fecha_cancelacion = %s,
                motivo_cancelacion = %s
            WHERE id_despacho = %s
            """,
            # Mismo motivo del incidente, sin prefijos ni recortes
            (cancelled_dispatch_id, moment, reason, row["id_despacho"]),
        )
        cursor.execute(
            """
            INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
            VALUES (%s, 'CAMBIO_ESTADO', 'DESPACHO', %s, %s)
            """,
            (
                user_id,
                row["id_despacho"],
                f"CREADO → CANCELADO por cancelación del incidente {incident_code}.",
            ),
        )
    for row in active:
        # Mismo cursor y mismo momento: sin transacciones anidadas
        dispatch_model._cancel_active_dispatch_in_tx(
            cursor, row["id_despacho"], reason, moment, user_id, participations, units)


_CANCEL_STATE_MESSAGE = (
    "Solo se puede cancelar un incidente Reportado, Asignado o En atención sin control registrado."
)


def cancel_incident(incident_id, reason, user_id):
    """Cancela un incidente (REPORTADO, ASIGNADO o EN_ATENCION sin control); False si no existe."""
    if not isinstance(reason, str):
        raise InvalidIncidentDataError("El motivo de cancelación no es válido.")
    reason = reason.strip()
    if not reason or len(reason) > _MAX_REASON_LENGTH:
        raise InvalidIncidentDataError("El motivo de cancelación no es válido.")

    def operation(cursor):
        current = _lock_incident(cursor, incident_id)
        if current is None:
            return False
        state = current["state_code"]
        if state == CANCELLED_STATE_CODE:
            raise IncidentStateError("El incidente ya está cancelado.")
        cancellable = state in (INITIAL_STATE_CODE, "ASIGNADO") or (
            state == "EN_ATENCION" and current["fecha_control"] is None)
        if not cancellable:
            raise IncidentStateError(_CANCEL_STATE_MESSAGE)
        # Un solo momento para incidente, despachos y participaciones
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
        _cancel_pending_dispatches(
            cursor, incident_id, current["codigo"], reason, user_id, moment,
            allow_active=state != INITIAL_STATE_CODE)
        cancelled_id = _state_id_by_code(cursor, CANCELLED_STATE_CODE)
        cursor.execute(
            """
            UPDATE incidentes
            SET id_estado_incidente = %s, fecha_cancelacion = %s,
                motivo_cancelacion = %s
            WHERE id_incidente = %s
            """,
            (cancelled_id, moment, reason, incident_id),
        )
        _audit(
            cursor, user_id, "CAMBIO_ESTADO", incident_id,
            f"{state} → CANCELADO. Motivo: {reason}",
        )
        return True

    # READ COMMITTED: sin bloqueos de hueco en despachos/despacho_unidades que choquen con
    # add_unit_to_draft de otro incidente (mismo criterio que la activación)
    return _write(operation, isolation="READ COMMITTED")


class IncidentReactivationError(IncidentStateError):
    """La reactivación no está justificada por recursos operando."""


class IncidentCloseBlockedError(IncidentStateError):
    """El cierre está bloqueado por despachos o participaciones pendientes."""


# Participación activa (predicado compartido) calificada con el alias du
_DU_ACTIVE = dispatch_model.ACTIVE_PARTICIPATION_PREDICATE.replace("fecha_", "du.fecha_")
_CONTROLLED_CODE = "CONTROLADO"
_IN_ATTENTION_CODE = "EN_ATENCION"
_CLOSED_CODE = "CERRADO"


def _check_ids(**ids):
    for name, value in ids.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidIncidentDataError(f"El identificador {name} no es válido.")


def _guarded_state_update(cursor, incident_id, source_code, target_code, extra_sql, params):
    """UPDATE del incidente solo si sigue en el estado esperado; exige una fila."""
    cursor.execute(
        f"UPDATE incidentes SET id_estado_incidente = %s{extra_sql} "
        "WHERE id_incidente = %s AND id_estado_incidente = %s",
        (_state_id_by_code(cursor, target_code), *params, incident_id,
         _state_id_by_code(cursor, source_code)),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("No se pudo actualizar el estado del incidente.")


def mark_incident_controlled(incident_id, user_id):
    """EN_ATENCION -> CONTROLADO con fecha_control; False si no existe."""
    _check_ids(incident_id=incident_id, user_id=user_id)

    def operation(cursor):
        current = _lock_incident(cursor, incident_id)
        if current is None:
            return False
        if current["state_code"] != _IN_ATTENTION_CODE:
            raise IncidentStateError(
                "Solo se puede declarar bajo control un incidente En atención."
            )
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
        # Un segundo control (tras reactivar) reemplaza la fecha anterior
        _guarded_state_update(cursor, incident_id, _IN_ATTENTION_CODE, _CONTROLLED_CODE,
                              ", fecha_control = %s", (moment,))
        _audit(cursor, user_id, "CAMBIO_ESTADO", incident_id, "EN_ATENCION → CONTROLADO")
        return True

    return _write(operation)


def reactivate_incident(incident_id, reason, user_id):
    """CONTROLADO -> EN_ATENCION con motivo y recursos operando; False si no existe."""
    _check_ids(incident_id=incident_id, user_id=user_id)
    if not isinstance(reason, str):
        raise InvalidIncidentDataError("El motivo de reactivación no es válido.")
    reason = reason.strip()
    if not reason or len(reason) > _MAX_REASON_LENGTH:
        raise InvalidIncidentDataError("El motivo de reactivación no es válido.")

    def operation(cursor):
        current = _lock_incident(cursor, incident_id)
        if current is None:
            return False
        if current["state_code"] != _CONTROLLED_CODE:
            raise IncidentStateError("Solo se puede reactivar un incidente Controlado.")
        # Condición genuina: algún despacho ACTIVO con una participación activa (lectura actual)
        cursor.execute(
            f"""
            SELECT du.id_despacho_unidad
            FROM despacho_unidades du
            INNER JOIN despachos d ON d.id_despacho = du.id_despacho
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE d.id_incidente = %s AND s.codigo = %s AND {_DU_ACTIVE}
            LIMIT 1
            FOR SHARE OF du
            """,
            (incident_id, DISPATCH_ACTIVE_CODE),
        )
        if cursor.fetchone() is None:
            raise IncidentReactivationError(
                "No se puede reactivar: el incidente no tiene un despacho activo con "
                "unidades operando."
            )
        # fecha_control, inicio de atención y cierre se conservan
        _guarded_state_update(cursor, incident_id, _CONTROLLED_CODE, _IN_ATTENTION_CODE, "", ())
        _audit(cursor, user_id, "CAMBIO_ESTADO", incident_id,
               f"CONTROLADO → EN_ATENCION (reactivación): {reason}")
        return True

    # READ COMMITTED: las lecturas bloqueantes sobre despachos y participaciones del
    # incidente no toman bloqueos de hueco que choquen con altas de otros incidentes
    return _write(operation, isolation="READ COMMITTED")


def close_incident(incident_id, user_id):
    """CONTROLADO -> CERRADO con fecha_cierre; False si no existe."""
    _check_ids(incident_id=incident_id, user_id=user_id)

    def operation(cursor):
        current = _lock_incident(cursor, incident_id)
        if current is None:
            return False
        if current["state_code"] != _CONTROLLED_CODE:
            raise IncidentStateError("Solo se puede cerrar un incidente Controlado.")
        cursor.execute(
            f"""
            SELECT du.id_despacho_unidad
            FROM despacho_unidades du
            INNER JOIN despachos d ON d.id_despacho = du.id_despacho
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE d.id_incidente = %s AND s.codigo = %s AND {_DU_ACTIVE}
            FOR SHARE OF du
            """,
            (incident_id, DISPATCH_ACTIVE_CODE),
        )
        if cursor.fetchall():
            raise IncidentCloseBlockedError(
                "No se puede cerrar: hay unidades con participación activa en el incidente."
            )
        cursor.execute(
            """
            SELECT s.codigo AS state_code
            FROM despachos d
            INNER JOIN estados_despacho s ON s.id_estado_despacho = d.id_estado_despacho
            WHERE d.id_incidente = %s AND s.codigo IN (%s, %s)
            FOR SHARE OF d
            """,
            (incident_id, DISPATCH_DRAFT_CODE, DISPATCH_ACTIVE_CODE),
        )
        pending = {row["state_code"] for row in cursor.fetchall()}
        if DISPATCH_ACTIVE_CODE in pending:
            raise IncidentCloseBlockedError(
                "No se puede cerrar: el incidente tiene despachos activos pendientes."
            )
        if DISPATCH_DRAFT_CODE in pending:
            raise IncidentCloseBlockedError(
                "No se puede cerrar: el incidente tiene despachos en borrador pendientes."
            )
        cursor.execute("SELECT NOW() AS moment")
        moment = cursor.fetchone()["moment"]
        _guarded_state_update(cursor, incident_id, _CONTROLLED_CODE, _CLOSED_CODE,
                              ", fecha_cierre = %s", (moment,))
        _audit(cursor, user_id, "CAMBIO_ESTADO", incident_id, "CONTROLADO → CERRADO")
        return True

    return _write(operation, isolation="READ COMMITTED")

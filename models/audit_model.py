from datetime import date, datetime, time, timedelta

from config.database import get_connection

# Límite de filas devueltas; se consulta una más para saber si hubo truncamiento
AUDIT_LIMIT = 500


class InvalidAuditFilterError(Exception):
    """Un filtro de bitácora no es válido; el mensaje es apto para mostrar."""


def _escape_like(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_date(value, label):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise InvalidAuditFilterError(
                f"La fecha {label} no es válida. Use el formato AAAA-MM-DD."
            ) from None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise InvalidAuditFilterError(f"La fecha {label} no es válida. Use el formato AAAA-MM-DD.")


def _text(value, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidAuditFilterError(f"El filtro {label} no es válido.")
    return value.strip() or None


def normalize_filters(filters):
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise InvalidAuditFilterError("Los filtros de bitácora no son válidos.")
    unknown = set(filters) - {"date_from", "date_to", "username", "action", "entity"}
    if unknown:
        raise InvalidAuditFilterError(
            f"Filtro no permitido: {', '.join(sorted(map(str, unknown)))}."
        )
    date_from = _to_date(filters.get("date_from"), "desde")
    date_to = _to_date(filters.get("date_to"), "hasta")
    if date_from and date_to and date_from > date_to:
        raise InvalidAuditFilterError("La fecha desde no puede ser posterior a la fecha hasta.")
    return {
        "date_from": date_from, "date_to": date_to,
        "username": _text(filters.get("username"), "usuario"),
        "action": _text(filters.get("action"), "acción"),
        "entity": _text(filters.get("entity"), "entidad"),
    }


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


def search_audit(filters=None):
    """Consulta de bitácora, solo lectura: {rows, truncated}.

    Filtros: usuario = contiene (sin distinguir mayúsculas, con comodines escapados);
    acción y entidad = coincidencia exacta; fecha hasta inclusiva por día.
    """
    normalized = normalize_filters(filters)
    conditions = []
    params = []
    if normalized["date_from"] is not None:
        conditions.append("b.fecha_hora >= %s")
        params.append(datetime.combine(normalized["date_from"], time.min))
    if normalized["date_to"] is not None:
        conditions.append("b.fecha_hora < %s")
        params.append(datetime.combine(normalized["date_to"] + timedelta(days=1), time.min))
    if normalized["username"] is not None:
        conditions.append("u.nombre_usuario LIKE %s")
        params.append(f"%{_escape_like(normalized['username'])}%")
    if normalized["action"] is not None:
        conditions.append("b.accion = %s")
        params.append(normalized["action"])
    if normalized["entity"] is not None:
        conditions.append("b.entidad = %s")
        params.append(normalized["entity"])
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _fetch_all(
        """
        SELECT b.id_bitacora AS audit_id, b.fecha_hora AS occurred_at,
               b.id_usuario AS user_id, u.nombre_usuario AS username,
               b.accion AS action, b.entidad AS entity, b.id_entidad AS entity_id,
               b.detalle AS detail
        FROM bitacora b
        LEFT JOIN usuarios u ON u.id_usuario = b.id_usuario
        """ + where + " ORDER BY b.fecha_hora DESC, b.id_bitacora DESC LIMIT %s",
        tuple(params) + (AUDIT_LIMIT + 1,),
    )
    truncated = len(rows) > AUDIT_LIMIT
    return {"rows": rows[:AUDIT_LIMIT], "truncated": truncated}


def list_audit_filter_options():
    """Valores distintos para los selectores de la pantalla."""
    return {
        "actions": [r["accion"] for r in _fetch_all(
            "SELECT DISTINCT accion FROM bitacora ORDER BY accion")],
        "entities": [r["entidad"] for r in _fetch_all(
            "SELECT DISTINCT entidad FROM bitacora ORDER BY entidad")],
        "usernames": [r["nombre_usuario"] for r in _fetch_all(
            "SELECT nombre_usuario FROM usuarios ORDER BY nombre_usuario")],
    }

from datetime import date, datetime, time, timedelta

from config.database import get_connection

_TERMINAL_CLOSED = "CERRADO"
_TERMINAL_CANCELLED = "CANCELADO"
_TOP_LIMIT = 5
_ALLOWED_FILTERS = {
    "date_from", "date_to", "type_id", "priority_id", "state_id", "state_code",
}


class ReportIncidentNotFoundError(Exception):
    """El incidente indicado no existe."""


class InvalidReportFilterError(Exception):
    """Un filtro del reporte no es válido; el mensaje es apto para mostrar."""


def _fetch_all(cursor, query, params=()):
    cursor.execute(query, params)
    return cursor.fetchall()


def _minutes(start, end):
    """Minutos entre dos fechas con 1 decimal; None si falta alguna."""
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60, 1)


def _with_cursor(function):
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            return function(cursor)
        finally:
            cursor.close()
    finally:
        connection.close()


# Hitos del timeline: columna del esquema -> clave estable y etiqueta
_TIMELINE = (
    ("reported_at", "reported", "Reporte"),
    ("assigned_at", "assigned", "Asignación"),
    ("attention_started_at", "attention_started", "Inicio de atención"),
    ("controlled_at", "controlled", "Control"),
    ("closed_at", "closed", "Cierre"),
    ("cancelled_at", "cancelled", "Cancelación"),
)


def _participation_status(row):
    """ACTIVA solo con la definición aprobada; el resto son estados históricos."""
    if (row["assigned_at"] is not None and row["released_at"] is None
            and row["cancelled_at"] is None and row["dispatch_state_code"] == "ACTIVO"):
        return "ACTIVA"
    if row["cancelled_at"] is not None:
        return "CANCELADA"
    if row["released_at"] is not None:
        return "LIBERADA"
    return "HISTORICA"


def get_incident_report(incident_id):
    """Reporte completo de un incidente (solo lectura, pocas consultas, sin N+1)."""
    if isinstance(incident_id, bool) or not isinstance(incident_id, int):
        raise ReportIncidentNotFoundError("El incidente no existe.")
    return _with_cursor(lambda cursor: _incident_report(cursor, incident_id))


def _incident_report(cursor, incident_id):
    rows = _fetch_all(
        cursor,
        """
        SELECT i.id_incidente AS incident_id, i.codigo AS code,
               i.fecha_reporte AS reported_at, t.nombre AS type_name,
               p.nombre AS priority_name, p.nivel AS priority_level,
               s.codigo AS state_code, s.nombre AS state_name,
               i.direccion AS address, i.referencia AS reference,
               i.latitud AS latitude, i.longitud AS longitude,
               i.descripcion AS description, i.personas_afectadas AS affected_people,
               u.nombre_usuario AS registered_by_username,
               CONCAT(u.nombres, ' ', u.apellidos) AS registered_by_name,
               i.fecha_asignacion AS assigned_at,
               i.fecha_inicio_atencion AS attention_started_at,
               i.fecha_control AS controlled_at, i.fecha_cierre AS closed_at,
               i.fecha_cancelacion AS cancelled_at,
               i.motivo_cancelacion AS cancel_reason
        FROM incidentes i
        INNER JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente
        INNER JOIN prioridades p ON p.id_prioridad = i.id_prioridad
        INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
        LEFT JOIN usuarios u ON u.id_usuario = i.id_usuario_registro
        WHERE i.id_incidente = %s
        """,
        (incident_id,),
    )
    if not rows:
        raise ReportIncidentNotFoundError("El incidente no existe.")
    main = rows[0]
    for key in ("latitude", "longitude"):
        if main[key] is not None:
            main[key] = float(main[key])

    dispatches = _fetch_all(
        cursor,
        """
        SELECT d.id_despacho AS dispatch_id, sd.codigo AS state_code, sd.nombre AS state_name,
               d.creado_en AS created_at, d.fecha_despacho AS dispatched_at,
               d.fecha_finalizacion AS finished_at, d.fecha_cancelacion AS cancelled_at,
               d.motivo_cancelacion AS cancel_reason, d.observaciones AS observations,
               cu.nombre_usuario AS created_by_username
        FROM despachos d
        INNER JOIN estados_despacho sd ON sd.id_estado_despacho = d.id_estado_despacho
        LEFT JOIN usuarios cu ON cu.id_usuario = d.id_usuario
        WHERE d.id_incidente = %s
        ORDER BY d.id_despacho
        """,
        (incident_id,),
    )

    # Solo cuentan como participaciones las filas con fecha_asignacion; las
    # preselecciones de un borrador no son participaciones reales
    units = _fetch_all(
        cursor,
        """
        SELECT du.id_despacho_unidad AS dispatch_unit_id, d.id_despacho AS dispatch_id,
               sd.codigo AS dispatch_state_code, un.id_unidad AS unit_id,
               un.codigo AS unit_code, un.nombre AS unit_name,
               tu.nombre AS unit_type_name, eo.codigo AS origin_station_code,
               eo.nombre AS origin_station_name,
               du.fecha_asignacion AS assigned_at, du.fecha_salida AS departed_at,
               du.fecha_llegada_escena AS arrived_at, du.fecha_liberacion AS released_at,
               du.fecha_cancelacion AS cancelled_at, du.motivo_cancelacion AS cancel_reason
        FROM despacho_unidades du
        INNER JOIN despachos d ON d.id_despacho = du.id_despacho
        INNER JOIN estados_despacho sd ON sd.id_estado_despacho = d.id_estado_despacho
        INNER JOIN unidades un ON un.id_unidad = du.id_unidad
        INNER JOIN tipos_unidad tu ON tu.id_tipo_unidad = un.id_tipo_unidad
        LEFT JOIN estaciones eo ON eo.id_estacion = du.id_estacion_origen
        WHERE d.id_incidente = %s AND du.fecha_asignacion IS NOT NULL
        ORDER BY d.id_despacho, du.fecha_asignacion, du.id_despacho_unidad
        """,
        (incident_id,),
    )
    for unit in units:
        unit["participation_status"] = _participation_status(unit)
        del unit["dispatch_state_code"]

    crew = _fetch_all(
        cursor,
        """
        SELECT dp.id_despacho_personal AS crew_id, du.id_despacho_unidad AS dispatch_unit_id,
               d.id_despacho AS dispatch_id, un.codigo AS unit_code,
               p.codigo AS personnel_code, p.nombres AS first_name, p.apellidos AS last_name,
               CONCAT(p.nombres, ' ', p.apellidos) AS full_name, p.cargo AS role
        FROM despacho_personal dp
        INNER JOIN despacho_unidades du ON du.id_despacho_unidad = dp.id_despacho_unidad
        INNER JOIN despachos d ON d.id_despacho = du.id_despacho
        INNER JOIN unidades un ON un.id_unidad = du.id_unidad
        INNER JOIN personal p ON p.id_personal = dp.id_personal
        WHERE d.id_incidente = %s AND du.fecha_asignacion IS NOT NULL
        ORDER BY d.id_despacho, un.codigo, p.apellidos, p.nombres, p.codigo
        """,
        (incident_id,),
    )

    # Hitos reales, ordenados por fecha y, en empate, por el orden fijo de los hitos
    timeline = [
        {"key": key, "label": label, "at": main[column]}
        for position, (column, key, label) in enumerate(_TIMELINE)
        if main[column] is not None
    ]
    order = {key: position for position, (_, key, _) in enumerate(_TIMELINE)}
    timeline.sort(key=lambda event: (event["at"], order[event["key"]]))

    # Tiempo de respuesta = primera llegada a escena - fecha de reporte (distinto del
    # indicador del dashboard, que mide llegada - salida)
    arrivals = [u["arrived_at"] for u in units if u["arrived_at"] is not None]
    response_minutes = _minutes(main["reported_at"], min(arrivals)) if arrivals else None
    # Duración determinista (nunca contra NOW): solo incidentes cerrados o cancelados
    if main["state_code"] == _TERMINAL_CLOSED:
        duration_minutes = _minutes(main["reported_at"], main["closed_at"])
    elif main["state_code"] == _TERMINAL_CANCELLED:
        duration_minutes = _minutes(main["reported_at"], main["cancelled_at"])
    else:
        duration_minutes = None

    report = dict(main)
    report.update({
        "timeline": timeline,
        "dispatches": dispatches,
        "units": units,
        "crew": crew,
        "response_minutes": response_minutes,
        "duration_minutes": duration_minutes,
    })
    return report


# ---------------------------------------------------------------- reporte general

def _to_date(value, label):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            raise InvalidReportFilterError(
                f"La fecha {label} no es válida. Use el formato AAAA-MM-DD."
            ) from None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise InvalidReportFilterError(f"La fecha {label} no es válida. Use el formato AAAA-MM-DD.")


def _optional_id(value, label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidReportFilterError(f"El filtro {label} no es válido.")
    return value


def normalize_filters(filters):
    """Valida y normaliza los filtros; devuelve un dict con todas las claves."""
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise InvalidReportFilterError("Los filtros del reporte no son válidos.")
    unknown = set(filters) - _ALLOWED_FILTERS
    if unknown:
        raise InvalidReportFilterError(
            f"Filtro no permitido: {', '.join(sorted(map(str, unknown)))}."
        )
    date_from = _to_date(filters.get("date_from"), "desde")
    date_to = _to_date(filters.get("date_to"), "hasta")
    if date_from and date_to and date_from > date_to:
        raise InvalidReportFilterError("La fecha desde no puede ser posterior a la fecha hasta.")
    state_code = filters.get("state_code")
    if state_code is not None and not isinstance(state_code, str):
        raise InvalidReportFilterError("El filtro estado no es válido.")
    state_code = state_code.strip() if state_code else None
    return {
        "date_from": date_from,
        "date_to": date_to,
        "type_id": _optional_id(filters.get("type_id"), "tipo"),
        "priority_id": _optional_id(filters.get("priority_id"), "prioridad"),
        "state_id": _optional_id(filters.get("state_id"), "estado"),
        "state_code": state_code or None,
    }


# Un único FROM/WHERE para la tabla y todas las métricas del conjunto filtrado
_SET_FROM = """
    FROM incidentes i
    INNER JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente
    INNER JOIN prioridades p ON p.id_prioridad = i.id_prioridad
    INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
"""


def _where(filters):
    conditions = []
    params = []
    if filters["date_from"] is not None:
        conditions.append("i.fecha_reporte >= %s")
        params.append(datetime.combine(filters["date_from"], time.min))
    if filters["date_to"] is not None:
        # Inclusivo por día: menor que el inicio del día siguiente
        conditions.append("i.fecha_reporte < %s")
        params.append(datetime.combine(filters["date_to"] + timedelta(days=1), time.min))
    if filters["type_id"] is not None:
        conditions.append("i.id_tipo_incidente = %s")
        params.append(filters["type_id"])
    if filters["priority_id"] is not None:
        conditions.append("i.id_prioridad = %s")
        params.append(filters["priority_id"])
    if filters["state_id"] is not None:
        conditions.append("i.id_estado_incidente = %s")
        params.append(filters["state_id"])
    if filters["state_code"] is not None:
        conditions.append("s.codigo = %s")
        params.append(filters["state_code"])
    return (" WHERE " + " AND ".join(conditions)) if conditions else "", tuple(params)


def _applied(cursor, filters):
    """Eco de los filtros con nombres legibles para mostrar."""
    result = {
        "date_from": filters["date_from"].strftime("%Y-%m-%d") if filters["date_from"] else None,
        "date_to": filters["date_to"].strftime("%Y-%m-%d") if filters["date_to"] else None,
        "type_id": filters["type_id"], "type_name": None,
        "priority_id": filters["priority_id"], "priority_name": None,
        "state_id": filters["state_id"], "state_code": filters["state_code"],
        "state_name": None,
    }
    if filters["type_id"] is not None:
        rows = _fetch_all(cursor, "SELECT nombre FROM tipos_incidente "
                          "WHERE id_tipo_incidente = %s", (filters["type_id"],))
        result["type_name"] = rows[0]["nombre"] if rows else None
    if filters["priority_id"] is not None:
        rows = _fetch_all(cursor, "SELECT nombre FROM prioridades WHERE id_prioridad = %s",
                          (filters["priority_id"],))
        result["priority_name"] = rows[0]["nombre"] if rows else None
    if filters["state_id"] is not None:
        rows = _fetch_all(cursor, "SELECT codigo, nombre FROM estados_incidente "
                          "WHERE id_estado_incidente = %s", (filters["state_id"],))
        if rows:
            result["state_name"] = rows[0]["nombre"]
            result["state_code"] = result["state_code"] or rows[0]["codigo"]
    elif filters["state_code"] is not None:
        rows = _fetch_all(cursor, "SELECT nombre FROM estados_incidente WHERE codigo = %s",
                          (filters["state_code"],))
        result["state_name"] = rows[0]["nombre"] if rows else None
    return result


def search_general_report(filters=None):
    """Tabla de incidentes filtrados y métricas sobre exactamente ese conjunto."""
    normalized = normalize_filters(filters)
    return _with_cursor(lambda cursor: _general_report(cursor, normalized))


def _general_report(cursor, filters):
    where, params = _where(filters)
    # Orden de la tabla: fecha de reporte descendente y luego id descendente
    rows = _fetch_all(
        cursor,
        """
        SELECT i.id_incidente AS incident_id, i.codigo AS code, i.fecha_reporte AS reported_at,
               t.nombre AS type_name, p.nombre AS priority_name, s.codigo AS state_code,
               s.nombre AS state_name, i.direccion AS address,
               i.fecha_cierre AS closed_at, i.fecha_cancelacion AS cancelled_at,
               (SELECT MIN(du.fecha_llegada_escena)
                  FROM despacho_unidades du
                  INNER JOIN despachos d ON d.id_despacho = du.id_despacho
                 WHERE d.id_incidente = i.id_incidente
                   AND du.fecha_asignacion IS NOT NULL) AS first_arrival
        """ + _SET_FROM + where + " ORDER BY i.fecha_reporte DESC, i.id_incidente DESC",
        params,
    )
    incidents = []
    response_values = []
    for row in rows:
        response = _minutes(row["reported_at"], row["first_arrival"])
        if response is not None:
            response_values.append((row["first_arrival"] - row["reported_at"]).total_seconds())
        if row["state_code"] == _TERMINAL_CLOSED:
            duration = _minutes(row["reported_at"], row["closed_at"])
        elif row["state_code"] == _TERMINAL_CANCELLED:
            duration = _minutes(row["reported_at"], row["cancelled_at"])
        else:
            duration = None
        incidents.append({
            "incident_id": row["incident_id"], "code": row["code"],
            "reported_at": row["reported_at"], "type_name": row["type_name"],
            "priority_name": row["priority_name"], "state_code": row["state_code"],
            "state_name": row["state_name"], "address": row["address"],
            "response_minutes": response, "duration_minutes": duration,
        })
    average = (round(sum(response_values) / len(response_values) / 60, 1)
               if response_values else None)

    participation_from = (
        _SET_FROM
        + " INNER JOIN despachos d ON d.id_incidente = i.id_incidente"
        + " INNER JOIN despacho_unidades du ON du.id_despacho = d.id_despacho"
    )
    participation_where = (where + " AND " if where else " WHERE ") + "du.fecha_asignacion IS NOT NULL"
    top_stations = _fetch_all(
        cursor,
        """
        SELECT e.id_estacion AS station_id, e.codigo AS station_code,
               e.nombre AS station_name, COUNT(*) AS activity
        """ + participation_from + " INNER JOIN estaciones e ON e.id_estacion = du.id_estacion_origen"
        + participation_where
        + " GROUP BY e.id_estacion, e.codigo, e.nombre"
        + " ORDER BY activity DESC, e.codigo ASC LIMIT " + str(_TOP_LIMIT),
        params,
    )
    for row in top_stations:
        row["activity"] = int(row["activity"])
    units_used = _fetch_all(
        cursor,
        "SELECT COUNT(DISTINCT du.id_unidad) AS total" + participation_from
        + participation_where,
        params,
    )[0]["total"]
    top_units = _fetch_all(
        cursor,
        "SELECT un.codigo AS unit_code, COUNT(*) AS uses" + participation_from
        + " INNER JOIN unidades un ON un.id_unidad = du.id_unidad" + participation_where
        + " GROUP BY un.id_unidad, un.codigo ORDER BY uses DESC, un.codigo ASC LIMIT "
        + str(_TOP_LIMIT),
        params,
    )
    for row in top_units:
        row["uses"] = int(row["uses"])
    return {
        "incidents": incidents,
        "metrics": {
            "incident_count": len(incidents),
            "avg_response_minutes": average,
            "top_stations": top_stations,
            "units_used": int(units_used),
            "top_units": top_units,
        },
        "filters_applied": _applied(cursor, filters),
    }

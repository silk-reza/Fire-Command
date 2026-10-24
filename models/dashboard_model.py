from config.database import get_connection
from models import dispatch_model

# Estados terminales de incidente: no cuentan como activos
_TERMINAL_STATE_CODES = ("CERRADO", "CANCELADO")
_RECENT_ACTIVITY_LIMIT = 15


def _scalar(cursor, query, params=()):
    cursor.execute(query, params)
    return cursor.fetchone()["value"]


def get_dashboard_data():
    """Indicadores del panel operativo; solo lectura, varias consultas en una conexión."""
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            return _collect(cursor)
        finally:
            cursor.close()
    finally:
        connection.close()


def _collect(cursor):
    active_incidents = int(_scalar(
        cursor,
        """
        SELECT COUNT(*) AS value
        FROM incidentes i
        INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
        WHERE s.codigo NOT IN (%s, %s)
        """,
        _TERMINAL_STATE_CODES,
    ))
    # Crítico = incidente activo cuya prioridad tiene el mayor `nivel` entre las prioridades
    # activas (nivel más alto = más urgente; nivel es único). Sin prioridades activas, 0.
    critical_incidents = int(_scalar(
        cursor,
        """
        SELECT COUNT(*) AS value
        FROM incidentes i
        INNER JOIN estados_incidente s ON s.id_estado_incidente = i.id_estado_incidente
        INNER JOIN prioridades p ON p.id_prioridad = i.id_prioridad
        WHERE s.codigo NOT IN (%s, %s)
          AND p.nivel = (SELECT MAX(nivel) FROM prioridades WHERE activo = 1)
        """,
        _TERMINAL_STATE_CODES,
    ))
    # Mismo criterio de disponibilidad que el recomendador (OBJ 18): unidad activa, estado
    # DISPONIBLE, estación activa y tipo de unidad activo (ver dispatch_model._RECOMMENDATION_FROM)
    available_units = int(_scalar(
        cursor,
        """
        SELECT COUNT(*) AS value
        FROM unidades u
        INNER JOIN estados_unidad s ON s.id_estado_unidad = u.id_estado_unidad
        INNER JOIN estaciones e ON e.id_estacion = u.id_estacion
        INNER JOIN tipos_unidad t ON t.id_tipo_unidad = u.id_tipo_unidad
        WHERE u.activo = 1 AND s.codigo = %s AND e.activo = 1 AND t.activo = 1
        """,
        (dispatch_model.AVAILABLE_UNIT_STATE_CODE,),
    ))
    # Los CHECK del esquema garantizan llegada >= salida, no hace falta filtrar negativos
    average = _scalar(
        cursor,
        """
        SELECT AVG(TIMESTAMPDIFF(SECOND, fecha_salida, fecha_llegada_escena)) / 60 AS value
        FROM despacho_unidades
        WHERE fecha_salida IS NOT NULL AND fecha_llegada_escena IS NOT NULL
        """,
    )
    avg_response_minutes = None if average is None else round(float(average), 1)

    cursor.execute(
        """
        SELECT b.fecha_hora AS occurred_at, u.nombre_usuario AS username,
               b.accion AS action, b.entidad AS entity, b.id_entidad AS entity_id,
               b.detalle AS detail
        FROM bitacora b
        LEFT JOIN usuarios u ON u.id_usuario = b.id_usuario
        ORDER BY b.fecha_hora DESC, b.id_bitacora DESC
        LIMIT %s
        """,
        (_RECENT_ACTIVITY_LIMIT,),
    )
    recent_activity = cursor.fetchall()

    # Distribución por tipo sobre todos los incidentes (no solo los activos)
    cursor.execute(
        """
        SELECT t.id_tipo_incidente AS type_id, t.nombre AS type_name, COUNT(*) AS count
        FROM incidentes i
        INNER JOIN tipos_incidente t ON t.id_tipo_incidente = i.id_tipo_incidente
        GROUP BY t.id_tipo_incidente, t.nombre
        ORDER BY COUNT(*) DESC, t.nombre ASC
        """
    )
    incidents_by_type = [
        {"type_id": r["type_id"], "type_name": r["type_name"], "count": int(r["count"])}
        for r in cursor.fetchall()
    ]

    # Últimos 7 días (hoy y los 6 anteriores); solo días con datos, sin rellenar ceros
    cursor.execute(
        """
        SELECT DATE(fecha_reporte) AS day, COUNT(*) AS count
        FROM incidentes
        WHERE fecha_reporte >= CURRENT_DATE - INTERVAL 6 DAY
        GROUP BY DATE(fecha_reporte)
        ORDER BY DATE(fecha_reporte) ASC
        """
    )
    incidents_by_period = [
        {"date": r["day"].strftime("%Y-%m-%d"), "count": int(r["count"])}
        for r in cursor.fetchall()
    ]

    return {
        "active_incidents": active_incidents,
        "critical_incidents": critical_incidents,
        "available_units": available_units,
        "avg_response_minutes": avg_response_minutes,
        "recent_activity": recent_activity,
        "incidents_by_type": incidents_by_type,
        "incidents_by_period": incidents_by_period,
    }

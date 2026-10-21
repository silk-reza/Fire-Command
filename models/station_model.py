import re

import mysql.connector

from config.database import get_connection


class DuplicateStationCodeError(Exception):
    """El código de estación ya existe."""


class InvalidStationDataError(Exception):
    """MySQL rechazó los datos por una restricción CHECK."""


_STATION_SELECT = """
    SELECT id_estacion AS station_id,
           codigo AS code,
           nombre AS name,
           direccion AS address,
           referencia AS reference,
           latitud AS latitude,
           longitud AS longitude,
           telefono AS phone,
           activo AS active
    FROM estaciones
"""

_ORDER = " ORDER BY codigo, id_estacion"


def _clean_station(row):
    # DECIMAL llega como Decimal; se expone como float para la GUI
    row["active"] = bool(row["active"])
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


def list_stations():
    """Lista todas las estaciones, activas e inactivas."""
    return [_clean_station(row) for row in _fetch_all(_STATION_SELECT + _ORDER)]


def list_active_stations():
    """Lista solo las estaciones activas."""
    rows = _fetch_all(_STATION_SELECT + " WHERE activo = 1" + _ORDER)
    return [_clean_station(row) for row in rows]


def search_stations(term):
    """Busca por coincidencia parcial en código, nombre o dirección."""
    pattern = f"%{_escape_like(term)}%"
    rows = _fetch_all(
        _STATION_SELECT
        + " WHERE codigo LIKE %s OR nombre LIKE %s OR direccion LIKE %s"
        + _ORDER,
        (pattern, pattern, pattern),
    )
    return [_clean_station(row) for row in rows]


def get_station(station_id):
    """Devuelve una estación por id o None."""
    rows = _fetch_all(_STATION_SELECT + " WHERE id_estacion = %s", (station_id,))
    return _clean_station(rows[0]) if rows else None


def _translate_error(error):
    """Convierte errores de MySQL en excepciones del dominio."""
    message = str(error.msg or "")
    if error.errno == 1062:
        match = re.search(r"for key '([^']*)'\s*$", message)
        key = match.group(1).split(".")[-1] if match else ""
        if key == "uq_estaciones_codigo":
            return DuplicateStationCodeError("Ya existe una estación con ese código.")
    if error.errno == 3819:
        return InvalidStationDataError("Los datos de la estación no son válidos.")
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


def create_station(code, name, address, reference, latitude, longitude, phone):
    """Inserta una estación activa y devuelve su nuevo id."""

    def operation(cursor):
        cursor.execute(
            """
            INSERT INTO estaciones
                (codigo, nombre, direccion, referencia, latitud, longitud, telefono, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
            """,
            (code, name, address, reference, latitude, longitude, phone),
        )
        return cursor.lastrowid

    return _write(operation)


def update_station(station_id, fields):
    """Actualiza los datos de la estación; devuelve False si no existe."""

    def operation(cursor):
        # rowcount es 0 cuando no hay cambios, por eso se verifica existencia aparte
        cursor.execute(
            "SELECT id_estacion FROM estaciones WHERE id_estacion = %s FOR UPDATE",
            (station_id,),
        )
        if cursor.fetchone() is None:
            return False
        cursor.execute(
            """
            UPDATE estaciones
            SET codigo = %s, nombre = %s, direccion = %s, referencia = %s,
                latitud = %s, longitud = %s, telefono = %s
            WHERE id_estacion = %s
            """,
            (
                fields["code"],
                fields["name"],
                fields["address"],
                fields["reference"],
                fields["latitude"],
                fields["longitude"],
                fields["phone"],
                station_id,
            ),
        )
        return True

    return _write(operation)


def set_station_active(station_id, active):
    """Activa o desactiva una estación; devuelve False si no existe."""

    def operation(cursor):
        cursor.execute(
            "SELECT id_estacion FROM estaciones WHERE id_estacion = %s FOR UPDATE",
            (station_id,),
        )
        if cursor.fetchone() is None:
            return False
        cursor.execute(
            "UPDATE estaciones SET activo = %s WHERE id_estacion = %s",
            (1 if active else 0, station_id),
        )
        return True

    return _write(operation)

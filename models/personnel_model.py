import re

import mysql.connector

from config.database import get_connection


class DuplicatePersonCodeError(Exception):
    """El código de personal ya existe."""


class InvalidStationError(Exception):
    """La estación indicada no existe (violación de la FK)."""


class InactiveStationError(Exception):
    """La estación existe pero está inactiva."""


class InvalidPersonDataError(Exception):
    """MySQL rechazó los datos por una restricción CHECK."""


_PERSON_SELECT = """
    SELECT p.id_personal AS person_id,
           p.codigo AS code,
           p.nombres AS first_name,
           p.apellidos AS last_name,
           p.cargo AS position,
           p.telefono AS phone,
           p.activo AS active,
           e.id_estacion AS station_id,
           e.codigo AS station_code,
           e.nombre AS station_name
    FROM personal p
    INNER JOIN estaciones e ON e.id_estacion = p.id_estacion
"""

_ORDER = " ORDER BY p.codigo, p.id_personal"


def _clean_person(row):
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


def list_personnel(station_id=None):
    """Lista todo el personal, activo e inactivo, con filtro opcional de estación."""
    return search_personnel("", station_id)


def search_personnel(term, station_id=None):
    """Busca por código, nombres, apellidos, nombre completo o cargo."""
    conditions = []
    params = []
    if term:
        pattern = f"%{_escape_like(term)}%"
        conditions.append(
            "(p.codigo LIKE %s OR p.nombres LIKE %s OR p.apellidos LIKE %s"
            " OR CONCAT(p.nombres, ' ', p.apellidos) LIKE %s OR p.cargo LIKE %s)"
        )
        params.extend([pattern] * 5)
    if station_id is not None:
        conditions.append("p.id_estacion = %s")
        params.append(station_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _fetch_all(_PERSON_SELECT + where + _ORDER, tuple(params))
    return [_clean_person(row) for row in rows]


def list_active_personnel(station_id=None):
    """Lista solo personal activo, ordenado por apellidos, nombres y código."""
    where = " WHERE p.activo = 1"
    params = ()
    if station_id is not None:
        where += " AND p.id_estacion = %s"
        params = (station_id,)
    rows = _fetch_all(
        _PERSON_SELECT + where + " ORDER BY p.apellidos, p.nombres, p.codigo, p.id_personal",
        params,
    )
    return [_clean_person(row) for row in rows]


def get_person(person_id):
    """Devuelve una persona por id o None."""
    rows = _fetch_all(_PERSON_SELECT + " WHERE p.id_personal = %s", (person_id,))
    return _clean_person(rows[0]) if rows else None


def _translate_error(error):
    """Convierte errores de MySQL en excepciones del dominio."""
    message = str(error.msg or "")
    if error.errno == 1062:
        match = re.search(r"for key '([^']*)'\s*$", message)
        key = match.group(1).split(".")[-1] if match else ""
        if key == "uq_personal_codigo":
            return DuplicatePersonCodeError("Ya existe una persona con ese código.")
    if error.errno == 1452:
        return InvalidStationError("La estación indicada no existe.")
    if error.errno == 3819:
        return InvalidPersonDataError("Los datos de la persona no son válidos.")
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
        raise InactiveStationError("No se puede asignar personal a una estación inactiva.")


def create_person(code, first_name, last_name, position, phone, station_id):
    """Inserta una persona activa y devuelve su nuevo id."""

    def operation(cursor):
        # Lee el estado real de la estación al momento de guardar
        _ensure_station_active(cursor, station_id)
        cursor.execute(
            """
            INSERT INTO personal
                (id_estacion, codigo, nombres, apellidos, cargo, telefono, activo)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            """,
            (station_id, code, first_name, last_name, position, phone or None),
        )
        return cursor.lastrowid

    return _write(operation)


def update_person(person_id, fields):
    """Actualiza los datos de la persona; devuelve False si no existe."""

    def operation(cursor):
        # rowcount es 0 cuando no hay cambios, por eso se verifica existencia aparte
        cursor.execute(
            "SELECT id_personal, id_estacion FROM personal"
            " WHERE id_personal = %s FOR UPDATE",
            (person_id,),
        )
        current = cursor.fetchone()
        if current is None:
            return False
        # Solo se exige estación activa si la persona cambia de estación
        if fields["station_id"] != current["id_estacion"]:
            _ensure_station_active(cursor, fields["station_id"])
        cursor.execute(
            """
            UPDATE personal
            SET id_estacion = %s, codigo = %s, nombres = %s, apellidos = %s,
                cargo = %s, telefono = %s
            WHERE id_personal = %s
            """,
            (
                fields["station_id"],
                fields["code"],
                fields["first_name"],
                fields["last_name"],
                fields["position"],
                fields["phone"] or None,
                person_id,
            ),
        )
        return True

    return _write(operation)


def set_person_active(person_id, active):
    """Activa o desactiva una persona; devuelve False si no existe."""

    def operation(cursor):
        cursor.execute(
            "SELECT id_personal FROM personal WHERE id_personal = %s FOR UPDATE",
            (person_id,),
        )
        if cursor.fetchone() is None:
            return False
        cursor.execute(
            "UPDATE personal SET activo = %s WHERE id_personal = %s",
            (1 if active else 0, person_id),
        )
        return True

    return _write(operation)

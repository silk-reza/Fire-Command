import re

import mysql.connector

from config.database import get_connection
from utils.permissions import ROLE_ADMINISTRADOR


def find_by_username(username):
    """Busca un usuario con su rol por nombre_usuario. Devuelve dict o None."""
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT u.id_usuario AS user_id,
                       u.nombre_usuario AS username,
                       u.nombres AS first_name,
                       u.apellidos AS last_name,
                       u.password_hash AS password_hash,
                       u.activo AS active,
                       r.codigo AS role_code
                FROM usuarios u
                INNER JOIN roles r ON r.id_rol = u.id_rol
                WHERE u.nombre_usuario = %s
                """,
                (username,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()


class DuplicateUsernameError(Exception):
    """El nombre de usuario ya existe."""


class DuplicateEmailError(Exception):
    """El correo ya existe."""


class LastAdministratorError(Exception):
    """La operación dejaría al sistema sin administradores activos."""


class InvalidRoleError(Exception):
    """El rol indicado no existe."""


_USER_SELECT = """
    SELECT u.id_usuario AS user_id,
           u.nombre_usuario AS username,
           u.nombres AS first_name,
           u.apellidos AS last_name,
           u.email AS email,
           u.activo AS active,
           r.id_rol AS role_id,
           r.codigo AS role_code,
           r.nombre AS role_name
    FROM usuarios u
    INNER JOIN roles r ON r.id_rol = u.id_rol
"""


def _clean_user(row):
    if row is None:
        return None
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


def list_users():
    """Lista todos los usuarios con su rol (sin hash)."""
    rows = _fetch_all(_USER_SELECT + " ORDER BY u.apellidos, u.nombres, u.id_usuario")
    return [_clean_user(row) for row in rows]


def _escape_like(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_users(term):
    """Busca por coincidencia parcial en usuario, nombres, apellidos o nombre completo."""
    pattern = f"%{_escape_like(term)}%"
    rows = _fetch_all(
        _USER_SELECT
        + """
        WHERE u.nombre_usuario LIKE %s
           OR u.nombres LIKE %s
           OR u.apellidos LIKE %s
           OR CONCAT(u.nombres, ' ', u.apellidos) LIKE %s
        ORDER BY u.apellidos, u.nombres, u.id_usuario
        """,
        (pattern, pattern, pattern, pattern),
    )
    return [_clean_user(row) for row in rows]


def get_user(user_id):
    """Devuelve un usuario por id (sin hash) o None."""
    rows = _fetch_all(_USER_SELECT + " WHERE u.id_usuario = %s", (user_id,))
    return _clean_user(rows[0]) if rows else None


def list_active_roles():
    """Lista los roles activos."""
    rows = _fetch_all(
        """
        SELECT id_rol AS role_id, codigo AS role_code, nombre AS role_name
        FROM roles
        WHERE activo = 1
        ORDER BY nombre
        """
    )
    return rows


def get_role(role_id):
    """Devuelve un rol por id o None."""
    rows = _fetch_all(
        """
        SELECT id_rol AS role_id, codigo AS role_code, nombre AS role_name,
               activo AS active
        FROM roles
        WHERE id_rol = %s
        """,
        (role_id,),
    )
    if not rows:
        return None
    rows[0]["active"] = bool(rows[0]["active"])
    return rows[0]


def _translate_integrity_error(error):
    """Convierte IntegrityError de MySQL en excepciones del dominio."""
    message = str(error.msg or "")
    if error.errno == 1062:
        # El valor duplicado va dentro del mensaje: se lee solo el nombre de la llave final
        match = re.search(r"for key '([^']*)'\s*$", message)
        key = match.group(1).split(".")[-1] if match else ""
        if key == "uq_usuarios_email":
            return DuplicateEmailError("El correo ya está registrado.")
        if key == "uq_usuarios_nombre_usuario":
            return DuplicateUsernameError("El nombre de usuario ya existe.")
    if error.errno == 1452:
        return InvalidRoleError("El rol indicado no existe.")
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
        except mysql.connector.IntegrityError as error:
            connection.rollback()
            translated = _translate_integrity_error(error)
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


def _lock_active_admin_ids(cursor):
    """Bloquea y devuelve los ids de administradores activos."""
    cursor.execute(
        """
        SELECT u.id_usuario
        FROM usuarios u
        INNER JOIN roles r ON r.id_rol = u.id_rol
        WHERE r.codigo = %s AND u.activo = 1
        ORDER BY u.id_usuario
        FOR UPDATE
        """,
        (ROLE_ADMINISTRADOR,),
    )
    return {row["id_usuario"] for row in cursor.fetchall()}


def _ensure_not_last_admin(cursor, user_id):
    """Aborta si user_id es el único administrador activo."""
    if _lock_active_admin_ids(cursor) == {user_id}:
        raise LastAdministratorError("Debe existir al menos un administrador activo.")


def create_user(username, first_name, last_name, email, role_id, password_hash):
    """Inserta un usuario activo y devuelve su nuevo id."""

    def operation(cursor):
        cursor.execute(
            """
            INSERT INTO usuarios
                (id_rol, nombres, apellidos, nombre_usuario, password_hash, email, activo)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            """,
            (role_id, first_name, last_name, username, password_hash, email),
        )
        return cursor.lastrowid

    return _write(operation)


def update_user(user_id, fields, new_password_hash=None):
    """Actualiza datos del usuario; devuelve False si no existe.

    Si new_password_hash es None no se toca password_hash.
    """

    def operation(cursor):
        # rowcount es 0 cuando no hay cambios, por eso se verifica existencia aparte
        cursor.execute(
            """
            SELECT u.activo, r.codigo
            FROM usuarios u
            INNER JOIN roles r ON r.id_rol = u.id_rol
            WHERE u.id_usuario = %s
            FOR UPDATE
            """,
            (user_id,),
        )
        target = cursor.fetchone()
        if target is None:
            return False
        # Solo importa si un administrador activo pasa a otro rol
        if target["activo"] and target["codigo"] == ROLE_ADMINISTRADOR:
            cursor.execute(
                "SELECT codigo FROM roles WHERE id_rol = %s", (fields["role_id"],)
            )
            new_role = cursor.fetchone()
            if new_role is not None and new_role["codigo"] != ROLE_ADMINISTRADOR:
                _ensure_not_last_admin(cursor, user_id)
        sets = [
            "nombre_usuario = %s",
            "nombres = %s",
            "apellidos = %s",
            "email = %s",
            "id_rol = %s",
        ]
        params = [
            fields["username"],
            fields["first_name"],
            fields["last_name"],
            fields["email"],
            fields["role_id"],
        ]
        if new_password_hash is not None:
            sets.append("password_hash = %s")
            params.append(new_password_hash)
        params.append(user_id)
        cursor.execute(
            f"UPDATE usuarios SET {', '.join(sets)} WHERE id_usuario = %s",
            tuple(params),
        )
        return True

    return _write(operation)


def set_user_active(user_id, active):
    """Activa o desactiva un usuario; devuelve False si no existe."""

    def operation(cursor):
        cursor.execute(
            """
            SELECT u.activo, r.codigo
            FROM usuarios u
            INNER JOIN roles r ON r.id_rol = u.id_rol
            WHERE u.id_usuario = %s
            FOR UPDATE
            """,
            (user_id,),
        )
        target = cursor.fetchone()
        if target is None:
            return False
        if not active and target["activo"] and target["codigo"] == ROLE_ADMINISTRADOR:
            _ensure_not_last_admin(cursor, user_id)
        cursor.execute(
            "UPDATE usuarios SET activo = %s WHERE id_usuario = %s",
            (1 if active else 0, user_id),
        )
        return True

    return _write(operation)


def record_login(user_id):
    """Registra un inicio de sesión exitoso en bitácora (transacción corta propia)."""

    def operation(cursor):
        cursor.execute(
            """
            INSERT INTO bitacora (id_usuario, accion, entidad, id_entidad, detalle)
            VALUES (%s, 'LOGIN', 'USUARIO', %s, 'Inicio de sesión exitoso.')
            """,
            (user_id, user_id),
        )

    _write(operation)

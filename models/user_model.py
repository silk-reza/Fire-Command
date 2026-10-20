from config.database import get_connection


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

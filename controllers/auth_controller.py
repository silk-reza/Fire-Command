import logging

import bcrypt
import mysql.connector

from models import user_model

logger = logging.getLogger(__name__)


class AccessDenied(Exception):
    """Credenciales no válidas o usuario no autorizado a ingresar."""


class AuthServiceError(Exception):
    """Fallo técnico (por ejemplo MySQL); no equivale a credenciales incorrectas."""


def authenticate(username, password):
    """Autentica un usuario. Devuelve dict con sus datos o lanza AccessDenied."""
    if not username or not password:
        raise AccessDenied("Acceso denegado.")

    try:
        user = user_model.find_by_username(username)
    except mysql.connector.Error as error:
        raise AuthServiceError("No fue posible consultar la base de datos.") from error

    # El motivo real (inexistente, inactivo, clave errónea) no se revela al llamador.
    if user is None or not user["active"]:
        raise AccessDenied("Acceso denegado.")

    try:
        valid = bcrypt.checkpw(
            password.encode("utf-8"), user["password_hash"].encode("utf-8")
        )
    except ValueError as error:
        # Hash almacenado con formato inválido: es un problema técnico, no de credenciales.
        raise AuthServiceError("Hash de contraseña no válido.") from error

    if not valid:
        raise AccessDenied("Acceso denegado.")

    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "role_code": user["role_code"],
        "active": bool(user["active"]),
    }


def login(username, password):
    """Autentica y registra el LOGIN en bitácora; devuelve el dict del usuario.

    El inicio de sesión no es una operación de negocio transaccional: si la bitácora
    falla se registra el error técnico y el acceso continúa (decisión documentada).
    """
    user = authenticate(username, password)
    try:
        user_model.record_login(user["user_id"])
    except mysql.connector.Error:
        logger.exception("No se pudo registrar el inicio de sesión en la bitácora")
    return user

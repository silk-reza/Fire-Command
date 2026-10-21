import re

import bcrypt
import mysql.connector

from controllers import access_controller
from models import user_model
from utils import session
from utils.permissions import PERMISSION_MANAGE_USERS

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BCRYPT_MAX_BYTES = 72


class UserValidationError(Exception):
    """Dato de usuario inválido; el mensaje es apto para mostrar."""


class SelfDeactivationError(UserValidationError):
    """El administrador intentó desactivar su propia cuenta."""


class LastAdministratorError(UserValidationError):
    """La operación dejaría al sistema sin administradores activos."""


class DuplicateUserError(Exception):
    """Nombre de usuario o correo ya registrado."""


class UserNotFoundError(Exception):
    """El usuario indicado no existe."""


class UserServiceError(Exception):
    """Fallo técnico al acceder a la base de datos."""


def _require():
    access_controller.require(PERMISSION_MANAGE_USERS)


def _technical_error(error):
    return UserServiceError("No fue posible completar la operación con la base de datos.")


def _text(data, key, label, max_length, required=True):
    value = data.get(key)
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        if required:
            raise UserValidationError(f"{label} es obligatorio.")
        return None
    if len(value) > max_length:
        raise UserValidationError(f"{label} no puede superar {max_length} caracteres.")
    return value


def _validate_password(password, required):
    """Devuelve el hash nuevo o None si se conserva la contraseña actual."""
    if password is None or password == "":
        if required:
            raise UserValidationError("La contraseña es obligatoria.")
        return None
    if not isinstance(password, str):
        raise UserValidationError("La contraseña no es válida.")
    encoded = password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_BYTES:
        raise UserValidationError(
            "La contraseña es demasiado larga (máximo 72 bytes)."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def _validate_role(raw_role_id, current_role_id=None):
    if isinstance(raw_role_id, bool):
        raise UserValidationError("El rol no es válido.")
    try:
        role_id = int(raw_role_id)
    except (TypeError, ValueError):
        raise UserValidationError("El rol es obligatorio y debe ser válido.") from None
    if current_role_id is not None and role_id == current_role_id:
        return role_id
    try:
        active_ids = {role["role_id"] for role in user_model.list_active_roles()}
    except mysql.connector.Error as error:
        raise _technical_error(error) from error
    if role_id not in active_ids:
        raise UserValidationError("El rol seleccionado no es válido o está inactivo.")
    return role_id


def _validate_fields(data, current_role_id=None):
    if not isinstance(data, dict):
        raise UserValidationError("Los datos del usuario no son válidos.")
    username = _text(data, "username", "El nombre de usuario", 50)
    first_name = _text(data, "first_name", "Los nombres", 80)
    last_name = _text(data, "last_name", "Los apellidos", 80)
    email = _text(data, "email", "El correo", 120, required=False)
    if email is not None and not _EMAIL_PATTERN.match(email):
        raise UserValidationError("El correo no tiene un formato válido.")
    role_id = _validate_role(data.get("role_id"), current_role_id)
    return {
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "role_id": role_id,
    }


def _validate_user_id(user_id):
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise UserValidationError("El identificador de usuario no es válido.")
    return user_id


def _duplicate_error(error):
    if isinstance(error, user_model.DuplicateEmailError):
        return DuplicateUserError("Ya existe un usuario con ese correo.")
    return DuplicateUserError("Ya existe un usuario con ese nombre de usuario.")


def list_users():
    """Lista todos los usuarios (solo ADMINISTRADOR)."""
    _require()
    try:
        return user_model.list_users()
    except mysql.connector.Error as error:
        raise _technical_error(error) from error


def search_users(query):
    """Busca usuarios por coincidencia parcial; vacío equivale a listar todos."""
    _require()
    term = query.strip() if isinstance(query, str) else ""
    try:
        if not term:
            return user_model.list_users()
        return user_model.search_users(term)
    except mysql.connector.Error as error:
        raise _technical_error(error) from error


def get_user(user_id):
    """Devuelve un usuario o lanza UserNotFoundError."""
    _require()
    user_id = _validate_user_id(user_id)
    try:
        user = user_model.get_user(user_id)
    except mysql.connector.Error as error:
        raise _technical_error(error) from error
    if user is None:
        raise UserNotFoundError("El usuario no existe.")
    return user


def list_roles():
    """Lista los roles activos asignables."""
    _require()
    try:
        return user_model.list_active_roles()
    except mysql.connector.Error as error:
        raise _technical_error(error) from error


def create_user(data):
    """Crea un usuario activo y devuelve el usuario creado."""
    _require()
    fields = _validate_fields(data)
    password_hash = _validate_password(data.get("password"), required=True)
    try:
        new_id = user_model.create_user(
            fields["username"],
            fields["first_name"],
            fields["last_name"],
            fields["email"],
            fields["role_id"],
            password_hash,
        )
        return user_model.get_user(new_id)
    except (user_model.DuplicateUsernameError, user_model.DuplicateEmailError) as error:
        raise _duplicate_error(error) from error
    except user_model.InvalidRoleError as error:
        raise UserValidationError("El rol seleccionado no es válido.") from error
    except mysql.connector.Error as error:
        raise _technical_error(error) from error


def update_user(user_id, data):
    """Edita un usuario; contraseña vacía conserva la actual.

    Si el usuario edita su propio rol, la sesión se cierra tras guardar
    (la vista lo detecta con session.is_active()).
    """
    _require()
    user_id = _validate_user_id(user_id)
    current_session = session.get_session()
    own_user = bool(current_session and current_session.get("user_id") == user_id)
    try:
        current = user_model.get_user(user_id)
    except mysql.connector.Error as error:
        raise _technical_error(error) from error
    if current is None:
        raise UserNotFoundError("El usuario no existe.")
    fields = _validate_fields(data, current_role_id=current["role_id"])
    new_hash = _validate_password(data.get("password"), required=False)
    try:
        found = user_model.update_user(user_id, fields, new_hash)
        if not found:
            raise UserNotFoundError("El usuario no existe.")
        # Con el cambio ya confirmado, la sesión no puede conservar el rol anterior
        if own_user and fields["role_id"] != current["role_id"]:
            session.end_session()
        return user_model.get_user(user_id)
    except (user_model.DuplicateUsernameError, user_model.DuplicateEmailError) as error:
        raise _duplicate_error(error) from error
    except user_model.LastAdministratorError as error:
        raise LastAdministratorError(str(error)) from error
    except user_model.InvalidRoleError as error:
        raise UserValidationError("El rol seleccionado no es válido.") from error
    except mysql.connector.Error as error:
        raise _technical_error(error) from error


def _set_active(user_id, active):
    _require()
    user_id = _validate_user_id(user_id)
    if not active:
        current = session.get_session()
        if current and current.get("user_id") == user_id:
            raise SelfDeactivationError("No puede desactivar su propia cuenta.")
    try:
        found = user_model.set_user_active(user_id, active)
        if not found:
            raise UserNotFoundError("El usuario no existe.")
        return user_model.get_user(user_id)
    except user_model.LastAdministratorError as error:
        raise LastAdministratorError(str(error)) from error
    except mysql.connector.Error as error:
        raise _technical_error(error) from error


def activate_user(user_id):
    """Reactiva un usuario."""
    return _set_active(user_id, True)


def deactivate_user(user_id):
    """Desactiva un usuario sin borrarlo."""
    return _set_active(user_id, False)

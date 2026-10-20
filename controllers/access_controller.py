from utils import permissions, session


class AuthorizationDenied(Exception):
    """Sesión válida pero con permiso insuficiente."""


class NotAuthenticated(Exception):
    """No hay sesión iniciada o la sesión no trae role_code."""


def _current_role_code():
    """Devuelve el role_code de la sesión o None si no hay sesión/rol."""
    current = session.get_session()
    if not current:
        return None
    role_code = current.get("role_code")
    return role_code if isinstance(role_code, str) else None


def get_permissions() -> frozenset:
    """Permisos del rol actual; vacío si no hay sesión o el rol es desconocido."""
    role_code = _current_role_code()
    if role_code is None:
        return frozenset()
    return permissions.ROLE_PERMISSIONS.get(role_code, frozenset())


def can(permission) -> bool:
    """Indica si el rol actual tiene el permiso; falla cerrado ante cualquier duda."""
    try:
        if permission not in permissions.ALL_PERMISSIONS:
            return False
        return permission in get_permissions()
    except Exception:
        return False


def require(permission) -> None:
    """Exige el permiso o lanza NotAuthenticated / AuthorizationDenied."""
    if _current_role_code() is None:
        raise NotAuthenticated("Debe iniciar sesión para realizar esta acción.")
    if not can(permission):
        raise AuthorizationDenied("No tiene permiso para realizar esta acción.")


def is_read_only() -> bool:
    """True si el rol actual no tiene ningún permiso de escritura.

    Sin sesión también devuelve True: es el estado más restrictivo.
    """
    return not (get_permissions() & permissions.WRITE_PERMISSIONS)

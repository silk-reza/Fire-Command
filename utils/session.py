"""Sesión mínima en memoria del usuario autenticado."""

# Claves permitidas: nunca se copian password ni password_hash
_SESSION_KEYS = ("user_id", "username", "role_code", "first_name", "last_name", "active")

_current_session = None


def start_session(user: dict) -> None:
    """Guarda solo los campos permitidos del usuario autenticado."""
    global _current_session
    _current_session = {key: user[key] for key in _SESSION_KEYS if key in user}


def get_session():
    """Devuelve una copia de la sesión actual o None si no hay sesión."""
    if _current_session is None:
        return None
    return dict(_current_session)


def is_active() -> bool:
    """Indica si hay una sesión iniciada."""
    return _current_session is not None


def end_session() -> None:
    """Limpia la sesión actual."""
    global _current_session
    _current_session = None

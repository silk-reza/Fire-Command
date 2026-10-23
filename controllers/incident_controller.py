import re
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation

import mysql.connector

from controllers import access_controller
from models import incident_model
from utils import session
from utils.permissions import (
    PERMISSION_OPERATE_INCIDENTS,
    PERMISSION_VIEW_OPERATIONAL_DATA,
)

_MAX_DECIMALS = 6
_MAX_AFFECTED_PEOPLE = 65535
_MAX_REASON_LENGTH = 250

_EDITABLE_KEYS = {
    "type_id", "priority_id", "address", "reference", "latitude", "longitude",
    "description", "affected_people",
}
_CREATE_KEYS = _EDITABLE_KEYS | {"code"}
_STATE_KEYS = {"state", "state_id", "state_code", "status", "id_estado_incidente"}
_USER_KEYS = {
    "user_id", "id_usuario_registro", "registered_by", "registered_by_id", "created_by",
}
_DATE_KEYS = {
    "reported_at", "assigned_at", "attention_started_at", "controlled_at",
    "closed_at", "cancelled_at", "cancel_reason", "motivo_cancelacion",
}


class IncidentValidationError(Exception):
    """Dato de incidente inválido; el mensaje es apto para mostrar."""


class InvalidTypeError(IncidentValidationError):
    """El tipo de incidente indicado no existe."""


class InactiveTypeError(IncidentValidationError):
    """El tipo de incidente indicado está inactivo."""


class InvalidPriorityError(IncidentValidationError):
    """La prioridad indicada no existe."""


class InactivePriorityError(IncidentValidationError):
    """La prioridad indicada está inactiva."""


class DuplicateIncidentCodeError(Exception):
    """El código de incidente ya está registrado."""


class IncidentStateError(Exception):
    """El estado actual del incidente no admite la operación."""


class IncidentNotFoundError(Exception):
    """El incidente indicado no existe."""


class IncidentServiceError(Exception):
    """Fallo técnico al acceder a la base de datos."""


def _require_read():
    access_controller.require(PERMISSION_VIEW_OPERATIONAL_DATA)


def _require_write():
    access_controller.require(PERMISSION_OPERATE_INCIDENTS)


def _technical_error():
    return IncidentServiceError("No fue posible completar la operación con la base de datos.")


def _text(data, key, label, max_length, required=False):
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise IncidentValidationError(f"{label} no es válido.")
    value = value.strip() if value else ""
    if not value:
        if required:
            raise IncidentValidationError(f"{label} es obligatorio.")
        return None
    if max_length and len(value) > max_length:
        raise IncidentValidationError(f"{label} no puede superar {max_length} caracteres.")
    return value


def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _coordinate(raw, label, limit):
    """Convierte a Decimal; solo acepta punto decimal y hasta 6 decimales."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise IncidentValidationError(f"La {label} no es un número válido.")
    text = raw.strip() if isinstance(raw, str) else str(raw)
    # Rechaza notación científica, comas, nan/inf y separadores de guion bajo
    if not re.fullmatch(r"[+-]?[0-9]+(\.[0-9]+)?", text):
        raise IncidentValidationError(f"La {label} no es un número válido.")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise IncidentValidationError(f"La {label} no es un número válido.") from None
    # Se rechaza en lugar de redondear para no alterar lo que escribió el usuario
    if value.as_tuple().exponent < -_MAX_DECIMALS:
        raise IncidentValidationError(
            f"La {label} no puede tener más de {_MAX_DECIMALS} decimales."
        )
    if abs(value) > limit:
        raise IncidentValidationError(f"La {label} debe estar entre -{limit} y {limit}.")
    return value


def _affected_people(value):
    message = f"Las personas afectadas deben ser un entero entre 0 y {_MAX_AFFECTED_PEOPLE}."
    if value is None:
        return 0
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return 0
        if not re.fullmatch(r"[0-9]+", value):
            raise IncidentValidationError(message)
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) \
            or not 0 <= value <= _MAX_AFFECTED_PEOPLE:
        raise IncidentValidationError(message)
    return value


def _validate_id(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise IncidentValidationError(f"El identificador de {label} no es válido.")
    return value


def _optional_id(value, label):
    if value is None:
        return None
    return _validate_id(value, label)


def _reject_forbidden_keys(data, allowed):
    extra = set(data) - allowed
    if not extra:
        return
    if extra & _STATE_KEYS:
        raise IncidentValidationError(
            "El estado del incidente no se puede modificar desde este módulo."
        )
    if extra & _USER_KEYS:
        raise IncidentValidationError(
            "El usuario registrador se toma de la sesión y no se puede indicar."
        )
    if any(str(key).startswith("fecha_") or key in _DATE_KEYS for key in extra):
        raise IncidentValidationError(
            "Las fechas y el motivo de cancelación no se pueden modificar desde este módulo."
        )
    if "code" in extra:
        raise IncidentValidationError(
            "El código del incidente no se puede modificar después de crearlo."
        )
    raise IncidentValidationError(
        f"Campo no permitido: {', '.join(sorted(map(str, extra)))}."
    )


def _validate_fields(data, creating):
    if not isinstance(data, dict):
        raise IncidentValidationError("Los datos del incidente no son válidos.")
    _reject_forbidden_keys(data, _CREATE_KEYS if creating else _EDITABLE_KEYS)
    type_id = data.get("type_id")
    if type_id is None:
        raise IncidentValidationError("El tipo de incidente es obligatorio.")
    if isinstance(type_id, bool) or not isinstance(type_id, int):
        raise InvalidTypeError("El tipo de incidente indicado no existe.")
    priority_id = data.get("priority_id")
    if priority_id is None:
        raise IncidentValidationError("La prioridad es obligatoria.")
    if isinstance(priority_id, bool) or not isinstance(priority_id, int):
        raise InvalidPriorityError("La prioridad indicada no existe.")
    fields = {}
    if creating:
        fields["code"] = _text(data, "code", "El código", 25, required=True)
    fields["type_id"] = type_id
    fields["priority_id"] = priority_id
    fields["address"] = _text(data, "address", "La dirección", 200, required=True)
    fields["reference"] = _text(data, "reference", "La referencia", 200)
    raw_lat, raw_lon = data.get("latitude"), data.get("longitude")
    latitude = longitude = None
    if _is_blank(raw_lat) != _is_blank(raw_lon):
        raise IncidentValidationError(
            "Debe indicar latitud y longitud juntas, o dejar ambas vacías."
        )
    if not _is_blank(raw_lat):
        latitude = _coordinate(raw_lat, "latitud", 90)
        longitude = _coordinate(raw_lon, "longitud", 180)
    fields["latitude"] = latitude
    fields["longitude"] = longitude
    fields["description"] = _text(
        data, "description", "La descripción", 65535, required=True
    )
    fields["affected_people"] = _affected_people(data.get("affected_people"))
    return fields


def _domain_error(error):
    if isinstance(error, incident_model.DuplicateIncidentCodeError):
        return DuplicateIncidentCodeError("Ya existe un incidente con ese código.")
    if isinstance(error, incident_model.InactiveTypeError):
        return InactiveTypeError("No se puede asignar un tipo de incidente inactivo.")
    if isinstance(error, incident_model.InvalidTypeError):
        return InvalidTypeError("El tipo de incidente indicado no existe.")
    if isinstance(error, incident_model.InactivePriorityError):
        return InactivePriorityError("No se puede asignar una prioridad inactiva.")
    if isinstance(error, incident_model.InvalidPriorityError):
        return InvalidPriorityError("La prioridad indicada no existe.")
    if isinstance(error, incident_model.IncidentStateError):
        return IncidentStateError(str(error))
    if isinstance(error, incident_model.InvalidReferenceError):
        return IncidentValidationError("Una referencia del incidente no existe.")
    return IncidentValidationError("Los datos del incidente no son válidos.")


_MODEL_DOMAIN_ERRORS = (
    incident_model.DuplicateIncidentCodeError,
    incident_model.InvalidReferenceError,
    incident_model.InvalidTypeError,
    incident_model.InactiveTypeError,
    incident_model.InvalidPriorityError,
    incident_model.InactivePriorityError,
    incident_model.InvalidIncidentDataError,
    incident_model.IncidentStateError,
)


def _session_user_id():
    # El registrador siempre sale de la sesión autenticada
    return session.get_session()["user_id"]


_DATE_FORMATS = (
    ("%Y-%m-%d", 23, 59),
    ("%Y-%m-%d %H:%M", None, 59),
    ("%Y-%m-%d %H:%M:%S", None, None),
)


def _date_bound(value, label, is_end):
    """Normaliza una cota; en 'hasta' completa la hora o el segundo omitidos."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace("T", " ", 1)
        if not text:
            return None
        for pattern, end_hour, end_second in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, pattern)
            except ValueError:
                continue
            if is_end:
                # Sin hora llega a 23:59:59; con HH:MM incluye el minuto completo (:59)
                if end_hour is not None:
                    parsed = parsed.replace(hour=end_hour, minute=59)
                if end_second is not None:
                    parsed = parsed.replace(second=end_second)
            return parsed
    elif isinstance(value, datetime):
        return value.replace(microsecond=0)
    elif isinstance(value, date) and not isinstance(value, bool):
        time_part = datetime_time(23, 59, 59) if is_end else datetime_time.min
        return datetime.combine(value, time_part)
    raise IncidentValidationError(
        f"La fecha {label} no es válida. Use el formato AAAA-MM-DD."
    )


def filter_incidents(type_id=None, priority_id=None, state_id=None, query=None,
                     date_from=None, date_to=None):
    """Búsqueda combinada (AND); None o texto vacío significa sin filtro.

    Un id de catálogo con formato válido pero inexistente devuelve lista vacía;
    uno con tipo incorrecto lanza IncidentValidationError. Los catálogos inactivos
    siguen siendo filtros válidos (consulta histórica). Las fechas filtran
    fecha_reporte de forma inclusiva; sin hora, 'desde' es 00:00:00 y 'hasta' 23:59:59;
    con HH:MM, 'hasta' es HH:MM:59 y 'desde' HH:MM:00.
    """
    _require_read()
    type_id = _optional_id(type_id, "tipo de incidente")
    priority_id = _optional_id(priority_id, "prioridad")
    state_id = _optional_id(state_id, "estado")
    start = _date_bound(date_from, "desde", is_end=False)
    end = _date_bound(date_to, "hasta", is_end=True)
    if start is not None and end is not None and start > end:
        raise IncidentValidationError("La fecha desde no puede ser posterior a la fecha hasta.")
    term = query.strip() if isinstance(query, str) else ""
    try:
        return incident_model.search_incidents(
            term, type_id, priority_id, state_id, start, end)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_incidents():
    """Lista todos los incidentes, del más reciente al más antiguo."""
    return filter_incidents()


def search_incidents(query):
    """Busca por código, descripción o dirección; vacío equivale a listar todo."""
    return filter_incidents(query=query)


def get_incident(incident_id):
    """Devuelve un incidente o lanza IncidentNotFoundError."""
    _require_read()
    incident_id = _validate_id(incident_id, "incidente")
    try:
        incident = incident_model.get_incident(incident_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error
    if incident is None:
        raise IncidentNotFoundError("El incidente no existe.")
    return incident


def create_incident(data):
    """Registra un incidente REPORTADO a nombre del usuario de la sesión."""
    _require_write()
    fields = _validate_fields(data, creating=True)
    user_id = _session_user_id()
    try:
        new_id = incident_model.create_incident(
            fields["code"], fields["type_id"], fields["priority_id"],
            fields["address"], fields["reference"], fields["latitude"],
            fields["longitude"], fields["description"], fields["affected_people"],
            user_id,
        )
        return incident_model.get_incident(new_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def update_incident(incident_id, data):
    """Edita datos descriptivos; el estado y las fechas no se tocan."""
    _require_write()
    incident_id = _validate_id(incident_id, "incidente")
    fields = _validate_fields(data, creating=False)
    user_id = _session_user_id()
    try:
        if not incident_model.update_incident(incident_id, fields, user_id):
            raise IncidentNotFoundError("El incidente no existe.")
        return incident_model.get_incident(incident_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def cancel_incident(incident_id, reason):
    """Cancela un incidente REPORTADO con motivo obligatorio."""
    _require_write()
    incident_id = _validate_id(incident_id, "incidente")
    if not isinstance(reason, str) or not reason.strip():
        raise IncidentValidationError("El motivo de cancelación es obligatorio.")
    reason = reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise IncidentValidationError(
            f"El motivo de cancelación no puede superar {_MAX_REASON_LENGTH} caracteres."
        )
    user_id = _session_user_id()
    try:
        if not incident_model.cancel_incident(incident_id, reason, user_id):
            raise IncidentNotFoundError("El incidente no existe.")
        return incident_model.get_incident(incident_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_incident_types():
    """Tipos de incidente (activos e inactivos) para filtros y selectores."""
    _require_read()
    try:
        return incident_model.list_incident_types()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_priorities():
    """Prioridades (activas e inactivas) para filtros y selectores."""
    _require_read()
    try:
        return incident_model.list_priorities()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_incident_states():
    """Estados del incidente, solo para mostrar y filtrar."""
    _require_read()
    try:
        return incident_model.list_incident_states()
    except mysql.connector.Error as error:
        raise _technical_error() from error

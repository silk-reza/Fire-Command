import re

import mysql.connector

from controllers import access_controller
from models import station_model, unit_model
from utils import session
from utils.permissions import PERMISSION_MANAGE_RESOURCES

_ALLOWED_KEYS = {
    "code", "name", "plate", "capacity", "description", "station_id", "type_id",
}
_STATE_KEYS = {"state", "state_id", "state_code", "status", "id_estado_unidad"}


class UnitValidationError(Exception):
    """Dato de unidad inválido; el mensaje es apto para mostrar."""


class InvalidStationError(UnitValidationError):
    """La estación indicada no existe."""


class InactiveStationError(UnitValidationError):
    """La estación indicada está inactiva."""


class InvalidTypeError(UnitValidationError):
    """El tipo de unidad indicado no existe."""


class InactiveTypeError(UnitValidationError):
    """El tipo de unidad indicado está inactivo."""


class DuplicateUnitCodeError(Exception):
    """El código de unidad ya está registrado."""


class DuplicateUnitPlateError(Exception):
    """La placa de unidad ya está registrada."""


class UnitBusyError(Exception):
    """La unidad está en atención y no admite la operación."""


class UnitTransitionError(UnitValidationError):
    """La unidad no está en un estado que admita la transición pedida."""


class UnitNotFoundError(Exception):
    """La unidad indicada no existe."""


class UnitServiceError(Exception):
    """Fallo técnico al acceder a la base de datos."""


def _require():
    access_controller.require(PERMISSION_MANAGE_RESOURCES)


def _technical_error():
    return UnitServiceError("No fue posible completar la operación con la base de datos.")


def _text(data, key, label, max_length, required=False):
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise UnitValidationError(f"{label} no es válido.")
    value = value.strip() if value else ""
    if not value:
        if required:
            raise UnitValidationError(f"{label} es obligatorio.")
        return None
    if len(value) > max_length:
        raise UnitValidationError(f"{label} no puede superar {max_length} caracteres.")
    return value


def _capacity(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if not re.fullmatch(r"[0-9]+", value):
            raise UnitValidationError("La capacidad debe ser un número entero entre 1 y 255.")
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 255:
        raise UnitValidationError("La capacidad debe ser un número entero entre 1 y 255.")
    return value


def _validate_id(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnitValidationError(f"El identificador de {label} no es válido.")
    return value


def _validate_unit_id(unit_id):
    return _validate_id(unit_id, "unidad")


def _validate_fields(data):
    if not isinstance(data, dict):
        raise UnitValidationError("Los datos de la unidad no son válidos.")
    extra = set(data) - _ALLOWED_KEYS
    if extra & _STATE_KEYS:
        raise UnitValidationError("El estado operativo no se puede modificar desde este módulo.")
    if extra:
        raise UnitValidationError(f"Campo no permitido: {', '.join(sorted(map(str, extra)))}.")
    station_id = data.get("station_id")
    if station_id is None:
        raise UnitValidationError("La estación es obligatoria.")
    if isinstance(station_id, bool) or not isinstance(station_id, int):
        raise InvalidStationError("La estación indicada no existe.")
    type_id = data.get("type_id")
    if type_id is None:
        raise UnitValidationError("El tipo de unidad es obligatorio.")
    if isinstance(type_id, bool) or not isinstance(type_id, int):
        raise InvalidTypeError("El tipo de unidad indicado no existe.")
    return {
        "code": _text(data, "code", "El código", 20, required=True),
        "name": _text(data, "name", "El nombre", 80),
        "plate": _text(data, "plate", "La placa", 20),
        "capacity": _capacity(data.get("capacity")),
        "description": _text(data, "description", "La descripción", 200),
        "station_id": station_id,
        "type_id": type_id,
    }


def _check_references_exist(fields):
    # Pre-chequeo amigable; la transacción del modelo sigue siendo la salvaguarda final
    if station_model.get_station(fields["station_id"]) is None:
        raise InvalidStationError("La estación indicada no existe.")
    if fields["type_id"] not in {t["type_id"] for t in unit_model.list_unit_types()}:
        raise InvalidTypeError("El tipo de unidad indicado no existe.")


def _domain_error(error):
    if isinstance(error, unit_model.DuplicateUnitCodeError):
        return DuplicateUnitCodeError("Ya existe una unidad con ese código.")
    if isinstance(error, unit_model.DuplicateUnitPlateError):
        return DuplicateUnitPlateError("Ya existe una unidad con esa placa.")
    if isinstance(error, unit_model.InactiveStationError):
        return InactiveStationError("No se puede asignar una unidad a una estación inactiva.")
    if isinstance(error, unit_model.InvalidStationError):
        return InvalidStationError("La estación indicada no existe.")
    if isinstance(error, unit_model.InactiveTypeError):
        return InactiveTypeError("No se puede asignar un tipo de unidad inactivo.")
    if isinstance(error, unit_model.InvalidTypeError):
        return InvalidTypeError("El tipo de unidad indicado no existe.")
    if isinstance(error, unit_model.UnitBusyError):
        return UnitBusyError(str(error))
    if isinstance(error, unit_model.UnitTransitionError):
        return UnitTransitionError(str(error))
    if isinstance(error, unit_model.InvalidReferenceError):
        return UnitValidationError("Una referencia de la unidad no existe.")
    return UnitValidationError("Los datos de la unidad no son válidos.")


_MODEL_DOMAIN_ERRORS = (
    unit_model.DuplicateUnitCodeError,
    unit_model.DuplicateUnitPlateError,
    unit_model.InvalidReferenceError,
    unit_model.InvalidStationError,
    unit_model.InactiveStationError,
    unit_model.InvalidTypeError,
    unit_model.InactiveTypeError,
    unit_model.InvalidUnitDataError,
    unit_model.UnitBusyError,
    unit_model.UnitTransitionError,
)


def _optional_id(value, label):
    if value is None:
        return None
    return _validate_id(value, label)


def filter_units(station_id=None, type_id=None, state_id=None, query=None):
    """Búsqueda combinada; None o texto vacío significa sin filtro."""
    _require()
    station_id = _optional_id(station_id, "estación")
    type_id = _optional_id(type_id, "tipo de unidad")
    state_id = _optional_id(state_id, "estado")
    term = query.strip() if isinstance(query, str) else ""
    try:
        return unit_model.search_units(term, station_id, type_id, state_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_units():
    """Lista todas las unidades, activas e inactivas."""
    return filter_units()


def search_units(query):
    """Busca por código, placa o nombre; vacío equivale a listar todo."""
    return filter_units(query=query)


def get_unit(unit_id):
    """Devuelve una unidad o lanza UnitNotFoundError."""
    _require()
    unit_id = _validate_unit_id(unit_id)
    try:
        unit = unit_model.get_unit(unit_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error
    if unit is None:
        raise UnitNotFoundError("La unidad no existe.")
    return unit


def create_unit(data):
    """Crea una unidad activa y DISPONIBLE; devuelve el registro creado."""
    _require()
    fields = _validate_fields(data)
    try:
        _check_references_exist(fields)
        new_id = unit_model.create_unit(
            fields["code"], fields["name"], fields["plate"], fields["capacity"],
            fields["description"], fields["station_id"], fields["type_id"],
        )
        return unit_model.get_unit(new_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def update_unit(unit_id, data):
    """Edita datos administrativos; el estado operativo no se toca."""
    _require()
    unit_id = _validate_unit_id(unit_id)
    fields = _validate_fields(data)
    current = session.get_session()
    user_id = current["user_id"] if current else None
    try:
        _check_references_exist(fields)
        if not unit_model.update_unit(unit_id, fields, user_id):
            raise UnitNotFoundError("La unidad no existe.")
        return unit_model.get_unit(unit_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def _set_active(unit_id, active):
    _require()
    unit_id = _validate_unit_id(unit_id)
    current = session.get_session()
    user_id = current["user_id"] if current else None
    try:
        if not unit_model.set_unit_active(unit_id, active, user_id):
            raise UnitNotFoundError("La unidad no existe.")
        return unit_model.get_unit(unit_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def activate_unit(unit_id):
    """Reactiva una unidad; queda FUERA_DE_SERVICIO."""
    return _set_active(unit_id, True)


def deactivate_unit(unit_id):
    """Retira una unidad sin borrarla; queda FUERA_DE_SERVICIO."""
    return _set_active(unit_id, False)


def list_unit_types():
    """Tipos de unidad (activos e inactivos) para filtros y selectores."""
    _require()
    try:
        return unit_model.list_unit_types()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_unit_states():
    """Estados operativos, solo para mostrar y filtrar."""
    _require()
    try:
        return unit_model.list_unit_states()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_stations_for_units():
    """Estaciones (activas e inactivas) para el formulario; solo lectura."""
    _require()
    try:
        stations = station_model.list_stations()
    except mysql.connector.Error as error:
        raise _technical_error() from error
    return [
        {
            "station_id": s["station_id"],
            "code": s["code"],
            "name": s["name"],
            "active": s["active"],
        }
        for s in stations
    ]


def _transition(function, unit_id):
    _require()
    unit_id = _validate_unit_id(unit_id)
    current = session.get_session()
    user_id = current["user_id"] if current else None
    try:
        if not function(unit_id, user_id):
            raise UnitNotFoundError("La unidad no existe.")
        return unit_model.get_unit(unit_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def send_unit_to_maintenance(unit_id):
    """DISPONIBLE -> MANTENIMIENTO."""
    return _transition(unit_model.send_unit_to_maintenance, unit_id)


def return_unit_to_service(unit_id):
    """MANTENIMIENTO o FUERA_DE_SERVICIO -> DISPONIBLE."""
    return _transition(unit_model.return_unit_to_service, unit_id)

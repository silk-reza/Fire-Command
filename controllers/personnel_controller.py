import mysql.connector

from controllers import access_controller
from models import personnel_model, station_model
from utils.permissions import PERMISSION_MANAGE_RESOURCES


class PersonnelValidationError(Exception):
    """Dato de personal inválido; el mensaje es apto para mostrar."""


class InvalidStationError(PersonnelValidationError):
    """La estación indicada no existe."""


class InactiveStationError(PersonnelValidationError):
    """La estación indicada está inactiva."""


class DuplicatePersonCodeError(Exception):
    """El código de personal ya está registrado."""


class PersonNotFoundError(Exception):
    """La persona indicada no existe."""


class PersonnelServiceError(Exception):
    """Fallo técnico al acceder a la base de datos."""


def _require():
    access_controller.require(PERMISSION_MANAGE_RESOURCES)


def _technical_error():
    return PersonnelServiceError("No fue posible completar la operación con la base de datos.")


def _text(data, key, label, max_length, required=True):
    value = data.get(key)
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        if required:
            raise PersonnelValidationError(f"{label} es obligatorio.")
        return None
    if len(value) > max_length:
        raise PersonnelValidationError(f"{label} no puede superar {max_length} caracteres.")
    return value


def _validate_id(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PersonnelValidationError(f"El identificador de {label} no es válido.")
    return value


def _validate_person_id(person_id):
    return _validate_id(person_id, "persona")


def _validate_station_id(station_id):
    if station_id is None:
        raise PersonnelValidationError("La estación es obligatoria.")
    if isinstance(station_id, bool) or not isinstance(station_id, int):
        raise InvalidStationError("La estación indicada no existe.")
    return station_id


def _validate_fields(data):
    if not isinstance(data, dict):
        raise PersonnelValidationError("Los datos de la persona no son válidos.")
    code = _text(data, "code", "El código", 20)
    first_name = _text(data, "first_name", "El nombre", 80)
    last_name = _text(data, "last_name", "El apellido", 80)
    position = _text(data, "position", "El cargo", 60)
    phone = _text(data, "phone", "El teléfono", 25, required=False)
    station_id = _validate_station_id(data.get("station_id"))
    return {
        "code": code,
        "first_name": first_name,
        "last_name": last_name,
        "position": position,
        "phone": phone,
        "station_id": station_id,
    }


def _check_station_exists(station_id):
    # Pre-chequeo amigable; la FK sigue siendo la salvaguarda final
    if station_model.get_station(station_id) is None:
        raise InvalidStationError("La estación indicada no existe.")


def _domain_error(error):
    if isinstance(error, personnel_model.DuplicatePersonCodeError):
        return DuplicatePersonCodeError("Ya existe una persona con ese código.")
    if isinstance(error, personnel_model.InactiveStationError):
        return InactiveStationError("No se puede asignar personal a una estación inactiva.")
    if isinstance(error, personnel_model.InvalidStationError):
        return InvalidStationError("La estación indicada no existe.")
    return PersonnelValidationError("Los datos de la persona no son válidos.")


_MODEL_DOMAIN_ERRORS = (
    personnel_model.DuplicatePersonCodeError,
    personnel_model.InvalidStationError,
    personnel_model.InactiveStationError,
    personnel_model.InvalidPersonDataError,
)


def _clean_query(query):
    return query.strip() if isinstance(query, str) else ""


def _optional_station_id(station_id):
    if station_id is None:
        return None
    if isinstance(station_id, bool) or not isinstance(station_id, int):
        raise PersonnelValidationError("El identificador de estación no es válido.")
    return station_id


def list_personnel():
    """Lista todo el personal, activo e inactivo."""
    _require()
    try:
        return personnel_model.list_personnel()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def search_personnel(query):
    """Busca por código, nombres, apellidos o cargo; vacío equivale a listar todo."""
    return find_personnel(query, None)


def filter_personnel_by_station(station_id):
    """Lista el personal de una estación."""
    _require()
    station_id = _validate_station_id(station_id)
    return find_personnel(None, station_id)


def find_personnel(query=None, station_id=None):
    """Búsqueda combinada con filtro opcional de estación (None = todas)."""
    _require()
    term = _clean_query(query)
    station_id = _optional_station_id(station_id)
    try:
        return personnel_model.search_personnel(term, station_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def get_person(person_id):
    """Devuelve una persona o lanza PersonNotFoundError."""
    _require()
    person_id = _validate_person_id(person_id)
    try:
        person = personnel_model.get_person(person_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error
    if person is None:
        raise PersonNotFoundError("La persona no existe.")
    return person


def create_person(data):
    """Crea una persona activa y devuelve el registro creado."""
    _require()
    fields = _validate_fields(data)
    try:
        _check_station_exists(fields["station_id"])
        new_id = personnel_model.create_person(
            fields["code"],
            fields["first_name"],
            fields["last_name"],
            fields["position"],
            fields["phone"],
            fields["station_id"],
        )
        return personnel_model.get_person(new_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def update_person(person_id, data):
    """Edita una persona y devuelve el resultado."""
    _require()
    person_id = _validate_person_id(person_id)
    fields = _validate_fields(data)
    try:
        _check_station_exists(fields["station_id"])
        if not personnel_model.update_person(person_id, fields):
            raise PersonNotFoundError("La persona no existe.")
        return personnel_model.get_person(person_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def _set_active(person_id, active):
    _require()
    person_id = _validate_person_id(person_id)
    try:
        if not personnel_model.set_person_active(person_id, active):
            raise PersonNotFoundError("La persona no existe.")
        return personnel_model.get_person(person_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def activate_person(person_id):
    """Reactiva a una persona."""
    return _set_active(person_id, True)


def deactivate_person(person_id):
    """Desactiva a una persona sin borrarla."""
    return _set_active(person_id, False)


def list_active_personnel(station_id=None):
    """Personal activo, reutilizable por futuros módulos de tripulación."""
    _require()
    station_id = _optional_station_id(station_id)
    try:
        return personnel_model.list_active_personnel(station_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_stations_for_personnel():
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

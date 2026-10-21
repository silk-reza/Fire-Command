from decimal import Decimal, InvalidOperation

import mysql.connector

from controllers import access_controller
from models import station_model
from utils.permissions import PERMISSION_MANAGE_CATALOGS

_MAX_DECIMALS = 6


class StationValidationError(Exception):
    """Dato de estación inválido; el mensaje es apto para mostrar."""


class DuplicateStationCodeError(Exception):
    """El código de estación ya está registrado."""


class StationNotFoundError(Exception):
    """La estación indicada no existe."""


class StationServiceError(Exception):
    """Fallo técnico al acceder a la base de datos."""


def _require():
    access_controller.require(PERMISSION_MANAGE_CATALOGS)


def _technical_error():
    return StationServiceError("No fue posible completar la operación con la base de datos.")


def _text(data, key, label, max_length, required=True):
    value = data.get(key)
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        if required:
            raise StationValidationError(f"{label} es obligatorio.")
        return None
    if len(value) > max_length:
        raise StationValidationError(f"{label} no puede superar {max_length} caracteres.")
    return value


def _coordinate(raw, label, limit):
    """Convierte a Decimal; solo acepta punto decimal y hasta 6 decimales."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise StationValidationError(f"La {label} no es un número válido.")
    text = raw.strip() if isinstance(raw, str) else str(raw)
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise StationValidationError(f"La {label} no es un número válido.") from None
    if not value.is_finite() or "_" in text:
        raise StationValidationError(f"La {label} no es un número válido.")
    # Se rechaza en lugar de redondear para no alterar lo que escribió el usuario
    if value.as_tuple().exponent < -_MAX_DECIMALS:
        raise StationValidationError(
            f"La {label} no puede tener más de {_MAX_DECIMALS} decimales."
        )
    if abs(value) > limit:
        raise StationValidationError(f"La {label} debe estar entre -{limit} y {limit}.")
    return value


def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _validate_fields(data):
    if not isinstance(data, dict):
        raise StationValidationError("Los datos de la estación no son válidos.")
    code = _text(data, "code", "El código", 20)
    name = _text(data, "name", "El nombre", 100)
    address = _text(data, "address", "La dirección", 200)
    reference = _text(data, "reference", "La referencia", 200, required=False)
    phone = _text(data, "phone", "El teléfono", 25, required=False)
    raw_lat, raw_lon = data.get("latitude"), data.get("longitude")
    latitude = longitude = None
    if _is_blank(raw_lat) != _is_blank(raw_lon):
        raise StationValidationError(
            "Debe indicar latitud y longitud juntas, o dejar ambas vacías."
        )
    if not _is_blank(raw_lat):
        latitude = _coordinate(raw_lat, "latitud", 90)
        longitude = _coordinate(raw_lon, "longitud", 180)
    return {
        "code": code,
        "name": name,
        "address": address,
        "reference": reference,
        "phone": phone,
        "latitude": latitude,
        "longitude": longitude,
    }


def _validate_station_id(station_id):
    if isinstance(station_id, bool) or not isinstance(station_id, int):
        raise StationValidationError("El identificador de estación no es válido.")
    return station_id


def _domain_error(error):
    if isinstance(error, station_model.DuplicateStationCodeError):
        return DuplicateStationCodeError("Ya existe una estación con ese código.")
    return StationValidationError("Los datos de la estación no son válidos.")


def list_stations():
    """Lista todas las estaciones (solo ADMINISTRADOR)."""
    _require()
    try:
        return station_model.list_stations()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_active_stations():
    """Lista estaciones activas; por ahora protegida con manage_catalogs."""
    _require()
    try:
        return station_model.list_active_stations()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def search_stations(query):
    """Busca por código, nombre o dirección; vacío equivale a listar todas."""
    _require()
    term = query.strip() if isinstance(query, str) else ""
    try:
        if not term:
            return station_model.list_stations()
        return station_model.search_stations(term)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def get_station(station_id):
    """Devuelve una estación o lanza StationNotFoundError."""
    _require()
    station_id = _validate_station_id(station_id)
    try:
        station = station_model.get_station(station_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error
    if station is None:
        raise StationNotFoundError("La estación no existe.")
    return station


def create_station(data):
    """Crea una estación activa y devuelve la estación creada."""
    _require()
    fields = _validate_fields(data)
    try:
        new_id = station_model.create_station(
            fields["code"],
            fields["name"],
            fields["address"],
            fields["reference"],
            fields["latitude"],
            fields["longitude"],
            fields["phone"],
        )
        return station_model.get_station(new_id)
    except (
        station_model.DuplicateStationCodeError,
        station_model.InvalidStationDataError,
    ) as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def update_station(station_id, data):
    """Edita una estación y devuelve el resultado."""
    _require()
    station_id = _validate_station_id(station_id)
    fields = _validate_fields(data)
    try:
        if not station_model.update_station(station_id, fields):
            raise StationNotFoundError("La estación no existe.")
        return station_model.get_station(station_id)
    except (
        station_model.DuplicateStationCodeError,
        station_model.InvalidStationDataError,
    ) as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def _set_active(station_id, active):
    _require()
    station_id = _validate_station_id(station_id)
    try:
        if not station_model.set_station_active(station_id, active):
            raise StationNotFoundError("La estación no existe.")
        return station_model.get_station(station_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def activate_station(station_id):
    """Reactiva una estación."""
    return _set_active(station_id, True)


def deactivate_station(station_id):
    """Desactiva una estación sin borrarla."""
    return _set_active(station_id, False)

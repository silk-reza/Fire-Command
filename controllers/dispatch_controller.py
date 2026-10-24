import mysql.connector

from controllers import access_controller
from models import dispatch_model
from utils import session
from utils.permissions import (
    PERMISSION_OPERATE_DISPATCHES,
    PERMISSION_VIEW_OPERATIONAL_DATA,
)

_MAX_REASON_LENGTH = 250
_MAX_OBSERVATIONS_LENGTH = 65535

_CREATE_KEYS = {"incident_id", "observations"}
_UPDATE_KEYS = {"observations"}
_STATE_KEYS = {"state", "status", "state_id", "state_code", "id_estado_despacho"}
_DATE_KEYS = {
    "dispatched_at", "assigned_at", "released_at", "cancelled_at", "finished_at",
    "cancel_reason", "motivo_cancelacion",
}
_ORIGIN_KEYS = {"id_estacion_origen", "origin_station_id", "origin_station"}
_USER_KEYS = {"user", "user_id", "id_usuario", "created_by", "created_by_id"}


class DispatchValidationError(Exception):
    """Dato de despacho inválido; el mensaje es apto para mostrar."""


class DispatchIncidentNotFoundError(Exception):
    """El incidente indicado no existe."""


class IncidentTerminalError(Exception):
    """El incidente está cerrado o cancelado."""


class DispatchNotFoundError(Exception):
    """El despacho indicado no existe."""


class DispatchStateError(Exception):
    """El despacho no está en estado CREADO."""


class DraftIncidentClosedError(DispatchStateError):
    """El borrador pertenece a un incidente cerrado o cancelado."""


class UnitStateError(DispatchStateError):
    """La unidad no está en el estado que exige la acción."""


class NoActiveParticipationError(DispatchStateError):
    """La unidad no tiene participación activa en el despacho."""


class DispatchHasNoUnitsError(DispatchValidationError):
    """El borrador no tiene unidades preseleccionadas."""


class UnitNotFoundError(Exception):
    """La unidad indicada no existe."""


class DispatchUnitNotFoundError(UnitNotFoundError):
    """La unidad no pertenece al despacho."""


class UnitNotEligibleError(Exception):
    """La unidad no puede preseleccionarse."""


class DuplicateDraftUnitError(Exception):
    """La unidad ya está preseleccionada en el borrador."""


class DraftUnitNotFoundError(Exception):
    """La unidad no está preseleccionada en el borrador."""


class DispatchServiceError(Exception):
    """Fallo técnico al acceder a la base de datos."""


def _require_read():
    access_controller.require(PERMISSION_VIEW_OPERATIONAL_DATA)


def _require_write():
    access_controller.require(PERMISSION_OPERATE_DISPATCHES)


def _technical_error():
    return DispatchServiceError("No fue posible completar la operación con la base de datos.")


def _session_user_id():
    # El creador y el autor de la bitácora siempre salen de la sesión
    return session.get_session()["user_id"]


def _validate_id(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise DispatchValidationError(f"El identificador de {label} no es válido.")
    return value


def _optional_id(value, label):
    if value is None:
        return None
    return _validate_id(value, label)


def _reject_forbidden_keys(data, allowed, updating=False):
    if not isinstance(data, dict):
        raise DispatchValidationError("Los datos del despacho no son válidos.")
    extra = set(data) - allowed
    if not extra:
        return
    if extra & _STATE_KEYS:
        raise DispatchValidationError(
            "El estado del despacho no se puede modificar desde este módulo."
        )
    if any(str(key).startswith("fecha_") or key in _DATE_KEYS for key in extra):
        raise DispatchValidationError(
            "Las fechas y los motivos de cancelación no se pueden modificar desde este módulo."
        )
    if extra & _ORIGIN_KEYS:
        raise DispatchValidationError(
            "La estación de origen no se puede indicar en un borrador."
        )
    if extra & _USER_KEYS:
        raise DispatchValidationError(
            "El usuario del despacho se toma de la sesión y no se puede indicar."
        )
    if updating and "incident_id" in extra:
        raise DispatchValidationError(
            "El incidente de un despacho no se puede cambiar."
        )
    raise DispatchValidationError(
        f"Campo no permitido: {', '.join(sorted(map(str, extra)))}."
    )


def _observations(data):
    value = data.get("observations")
    if value is None:
        return None
    if not isinstance(value, str):
        raise DispatchValidationError("Las observaciones no son válidas.")
    value = value.strip()
    if len(value) > _MAX_OBSERVATIONS_LENGTH:
        raise DispatchValidationError(
            f"Las observaciones no pueden superar {_MAX_OBSERVATIONS_LENGTH} caracteres."
        )
    return value or None


def _domain_error(error):
    if isinstance(error, dispatch_model.DispatchIncidentNotFoundError):
        return DispatchIncidentNotFoundError("El incidente no existe.")
    if isinstance(error, dispatch_model.IncidentTerminalError):
        return IncidentTerminalError(str(error))
    if isinstance(error, dispatch_model.DispatchNotFoundError):
        return DispatchNotFoundError("El despacho no existe.")
    if isinstance(error, dispatch_model.DispatchHasNoUnitsError):
        return DispatchHasNoUnitsError("El borrador no tiene unidades preseleccionadas.")
    if isinstance(error, dispatch_model.DispatchUnitNotFoundError):
        return DispatchUnitNotFoundError(str(error))
    if isinstance(error, dispatch_model.UnitStateError):
        return UnitStateError(str(error))
    if isinstance(error, dispatch_model.NoActiveParticipationError):
        return NoActiveParticipationError(str(error))
    if isinstance(error, dispatch_model.OperationDatesError):
        return DispatchValidationError(str(error))
    if isinstance(error, dispatch_model.DraftIncidentClosedError):
        return DraftIncidentClosedError(str(error))
    if isinstance(error, dispatch_model.DispatchStateError):
        return DispatchStateError(str(error))
    if isinstance(error, dispatch_model.UnitNotFoundError):
        return UnitNotFoundError("La unidad no existe.")
    if isinstance(error, dispatch_model.UnitNotEligibleError):
        return UnitNotEligibleError(str(error))
    if isinstance(error, dispatch_model.DuplicateDraftUnitError):
        return DuplicateDraftUnitError("La unidad ya está preseleccionada en este despacho.")
    if isinstance(error, dispatch_model.DraftUnitNotFoundError):
        return DraftUnitNotFoundError("La unidad no está preseleccionada en este despacho.")
    if isinstance(error, dispatch_model.InvalidReferenceError):
        return DispatchValidationError("Una referencia del despacho no existe.")
    return DispatchValidationError("Los datos del despacho no son válidos.")


_MODEL_DOMAIN_ERRORS = (
    dispatch_model.DispatchIncidentNotFoundError,
    dispatch_model.IncidentTerminalError,
    dispatch_model.DispatchNotFoundError,
    dispatch_model.DispatchStateError,
    dispatch_model.UnitNotFoundError,
    dispatch_model.UnitNotEligibleError,
    dispatch_model.DuplicateDraftUnitError,
    dispatch_model.DraftUnitNotFoundError,
    dispatch_model.InvalidReferenceError,
    dispatch_model.InvalidDispatchDataError,
)


def _full_dispatch(dispatch_id):
    """Despacho con su lista de unidades preseleccionadas (una consulta extra)."""
    dispatch = dispatch_model.get_dispatch(dispatch_id)
    if dispatch is None:
        raise DispatchNotFoundError("El despacho no existe.")
    dispatch["units"] = dispatch_model.list_draft_units(dispatch_id)
    return dispatch


def list_dispatches(incident_id=None, state_id=None):
    """Lista despachos, del más reciente al más antiguo."""
    _require_read()
    incident_id = _optional_id(incident_id, "incidente")
    state_id = _optional_id(state_id, "estado")
    try:
        return dispatch_model.list_dispatches(incident_id, state_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_draft_dispatches(incident_id=None):
    """Lista solo los despachos en estado CREADO."""
    _require_read()
    incident_id = _optional_id(incident_id, "incidente")
    try:
        return dispatch_model.list_dispatches(
            incident_id, None, dispatch_model.DRAFT_STATE_CODE
        )
    except mysql.connector.Error as error:
        raise _technical_error() from error


def get_dispatch(dispatch_id):
    """Devuelve el despacho con la clave 'units' o lanza DispatchNotFoundError."""
    _require_read()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    try:
        return _full_dispatch(dispatch_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_draft_units(dispatch_id):
    """Unidades preseleccionadas con su estado real; vacío si no hay o no existe."""
    _require_read()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    try:
        return dispatch_model.list_draft_units(dispatch_id)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_available_units(dispatch_id=None, type_id=None, station_id=None, query=None):
    """Unidades elegibles (activas, DISPONIBLE, estación activa) para preseleccionar."""
    _require_read()
    dispatch_id = _optional_id(dispatch_id, "despacho")
    type_id = _optional_id(type_id, "tipo de unidad")
    station_id = _optional_id(station_id, "estación")
    if query is not None and not isinstance(query, str):
        raise DispatchValidationError("El texto de búsqueda no es válido.")
    term = query.strip() if query else ""
    try:
        return dispatch_model.list_available_units(dispatch_id, type_id, station_id, term)
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_selectable_incidents():
    """Incidentes que admiten un nuevo despacho (no cerrados ni cancelados)."""
    _require_read()
    try:
        return dispatch_model.list_selectable_incidents()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def list_dispatch_states():
    """Estados de despacho, solo para mostrar y filtrar."""
    _require_read()
    try:
        return dispatch_model.list_dispatch_states()
    except mysql.connector.Error as error:
        raise _technical_error() from error


def create_dispatch_draft(data):
    """Crea un despacho CREADO para un incidente no terminal; no reserva nada."""
    _require_write()
    _reject_forbidden_keys(data, _CREATE_KEYS)
    incident_id = data.get("incident_id")
    if incident_id is None:
        raise DispatchValidationError("El incidente es obligatorio.")
    incident_id = _validate_id(incident_id, "incidente")
    observations = _observations(data)
    user_id = _session_user_id()
    try:
        new_id = dispatch_model.create_dispatch_draft(incident_id, observations, user_id)
        return _full_dispatch(new_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def update_dispatch_draft(dispatch_id, data):
    """Edita las observaciones de un borrador CREADO (único campo editable)."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    _reject_forbidden_keys(data, _UPDATE_KEYS, updating=True)
    if "observations" not in data:
        raise DispatchValidationError("No se indicó ningún campo para modificar.")
    observations = _observations(data)
    try:
        if not dispatch_model.update_dispatch_draft(dispatch_id, observations):
            raise DispatchNotFoundError("El despacho no existe.")
        return _full_dispatch(dispatch_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def add_unit_to_draft(dispatch_id, unit_id):
    """Preselecciona una unidad elegible; devuelve el id de despacho_unidades."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    unit_id = _validate_id(unit_id, "unidad")
    try:
        return dispatch_model.add_unit_to_draft(dispatch_id, unit_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def remove_unit_from_draft(dispatch_id, unit_id):
    """Quita una preselección del borrador; devuelve True."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    unit_id = _validate_id(unit_id, "unidad")
    try:
        if not dispatch_model.remove_unit_from_draft(dispatch_id, unit_id):
            raise DispatchNotFoundError("El despacho no existe.")
        return True
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def cancel_dispatch_draft(dispatch_id, reason):
    """Cancela un despacho CREADO con motivo obligatorio."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    if not isinstance(reason, str) or not reason.strip():
        raise DispatchValidationError("El motivo de cancelación es obligatorio.")
    reason = reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise DispatchValidationError(
            f"El motivo de cancelación no puede superar {_MAX_REASON_LENGTH} caracteres."
        )
    user_id = _session_user_id()
    try:
        if not dispatch_model.cancel_dispatch_draft(dispatch_id, reason, user_id):
            raise DispatchNotFoundError("El despacho no existe.")
        return _full_dispatch(dispatch_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def activate_dispatch(dispatch_id):
    """Activa un borrador CREADO: unidades DESPACHADA e incidente ASIGNADO si estaba REPORTADO."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    user_id = _session_user_id()
    try:
        if not dispatch_model.activate_dispatch(dispatch_id, user_id):
            raise DispatchNotFoundError("El despacho no existe.")
        return _full_dispatch(dispatch_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def _operate(function, dispatch_id, unit_id):
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    unit_id = _validate_id(unit_id, "unidad")
    user_id = _session_user_id()
    try:
        function(dispatch_id, unit_id, user_id)
        return _full_dispatch(dispatch_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def mark_unit_en_route(dispatch_id, unit_id):
    """DESPACHADA -> EN_RUTA."""
    return _operate(dispatch_model.mark_unit_en_route, dispatch_id, unit_id)


def mark_unit_on_scene(dispatch_id, unit_id):
    """EN_RUTA -> EN_ESCENA (la primera llegada pone el incidente EN_ATENCION)."""
    return _operate(dispatch_model.mark_unit_on_scene, dispatch_id, unit_id)


def start_unit_return(dispatch_id, unit_id):
    """EN_ESCENA -> REGRESANDO (libera la participación)."""
    return _operate(dispatch_model.start_unit_return, dispatch_id, unit_id)


def mark_unit_available(dispatch_id, unit_id):
    """REGRESANDO -> DISPONIBLE."""
    return _operate(dispatch_model.mark_unit_available, dispatch_id, unit_id)


def cancel_active_dispatch(dispatch_id, reason):
    """Cancela un despacho activo con motivo obligatorio; no toca el incidente."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    if not isinstance(reason, str) or not reason.strip():
        raise DispatchValidationError("El motivo de cancelación es obligatorio.")
    reason = reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise DispatchValidationError(
            f"El motivo de cancelación no puede superar {_MAX_REASON_LENGTH} caracteres."
        )
    user_id = _session_user_id()
    try:
        if not dispatch_model.cancel_active_dispatch(dispatch_id, reason, user_id):
            raise DispatchNotFoundError("El despacho no existe.")
        return _full_dispatch(dispatch_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error


def report_unit_failure(dispatch_id, unit_id, reason):
    """Reporta falla de una unidad en operación: queda FUERA_DE_SERVICIO."""
    _require_write()
    dispatch_id = _validate_id(dispatch_id, "despacho")
    unit_id = _validate_id(unit_id, "unidad")
    if not isinstance(reason, str) or not reason.strip():
        raise DispatchValidationError("El motivo de la falla es obligatorio.")
    reason = reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise DispatchValidationError(
            f"El motivo de la falla no puede superar {_MAX_REASON_LENGTH} caracteres."
        )
    user_id = _session_user_id()
    try:
        dispatch_model.report_unit_failure(dispatch_id, unit_id, reason, user_id)
        return _full_dispatch(dispatch_id)
    except _MODEL_DOMAIN_ERRORS as error:
        raise _domain_error(error) from error
    except mysql.connector.Error as error:
        raise _technical_error() from error

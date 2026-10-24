import logging

import mysql.connector

from controllers import access_controller
from models import audit_model
from utils.permissions import PERMISSION_VIEW_AUDIT

logger = logging.getLogger(__name__)


class AuditValidationError(Exception):
    """Filtro de bitácora inválido; el mensaje es apto para mostrar."""


class AuditServiceError(Exception):
    """Fallo técnico al consultar la bitácora."""


def _technical_error():
    return AuditServiceError("No fue posible consultar la bitácora.")


def search_audit(filters=None):
    """Bitácora filtrada, solo lectura: {rows, truncated} (permiso view_audit)."""
    access_controller.require(PERMISSION_VIEW_AUDIT)
    try:
        return audit_model.search_audit(filters)
    except audit_model.InvalidAuditFilterError as error:
        raise AuditValidationError(str(error)) from error
    except mysql.connector.Error as error:
        logger.exception("Error al consultar la bitácora")
        raise _technical_error() from error


def list_audit_filter_options():
    """Acciones, entidades y usuarios disponibles para los filtros."""
    access_controller.require(PERMISSION_VIEW_AUDIT)
    try:
        return audit_model.list_audit_filter_options()
    except mysql.connector.Error as error:
        logger.exception("Error al consultar las opciones de bitácora")
        raise _technical_error() from error

import logging
import os
import uuid

import mysql.connector

from controllers import access_controller
from models import report_model
from utils import report_pdf
from utils.permissions import PERMISSION_VIEW_REPORTS

logger = logging.getLogger(__name__)


class ReportNotFoundError(Exception):
    """El incidente del reporte no existe."""


class ReportValidationError(Exception):
    """Filtro o dato de reporte inválido; el mensaje es apto para mostrar."""


class ReportExportError(Exception):
    """No fue posible generar o escribir el PDF; el mensaje es apto para mostrar."""


class ReportServiceError(Exception):
    """Fallo técnico al leer los datos del reporte."""


def _technical_error():
    return ReportServiceError("No fue posible cargar los datos del reporte.")


def _validate_id(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportValidationError("El identificador del incidente no es válido.")
    return value


def _incident_report(incident_id):
    try:
        return report_model.get_incident_report(incident_id)
    except report_model.ReportIncidentNotFoundError as error:
        raise ReportNotFoundError("El incidente no existe.") from error
    except mysql.connector.Error as error:
        logger.exception("Error al consultar el reporte de incidente")
        raise _technical_error() from error


def _general_report(filters):
    try:
        return report_model.search_general_report(filters)
    except report_model.InvalidReportFilterError as error:
        raise ReportValidationError(str(error)) from error
    except mysql.connector.Error as error:
        logger.exception("Error al consultar el reporte general")
        raise _technical_error() from error


def get_incident_report(incident_id):
    """Reporte detallado de un incidente (solo lectura)."""
    access_controller.require(PERMISSION_VIEW_REPORTS)
    return _incident_report(_validate_id(incident_id))


def search_general_report(filters=None):
    """Tabla y métricas del reporte general con filtros opcionales."""
    access_controller.require(PERMISSION_VIEW_REPORTS)
    return _general_report(filters)


def _checked_path(path):
    """Exige texto no vacío; agrega .pdf si no tiene extensión y rechaza otras."""
    if not isinstance(path, str) or not path.strip():
        raise ReportExportError("Debe indicar la ruta del archivo PDF.")
    path = path.strip()
    extension = os.path.splitext(path)[1].lower()
    if not extension:
        return path + ".pdf"
    if extension != ".pdf":
        raise ReportExportError("El archivo debe tener la extensión .pdf.")
    return path


def _write_pdf(builder, data, path, compress):
    # Se construye en un temporal del mismo directorio y solo se reemplaza el destino al
    # terminar bien: un PDF previo del usuario nunca se pierde por un fallo
    temporary = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        builder(data, temporary, compress=compress)
        os.replace(temporary, path)
    except Exception as error:  # noqa: BLE001 (se informa sin traceback)
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
        logger.exception("Error al generar el PDF")
        if isinstance(error, OSError):
            raise ReportExportError(
                "No fue posible escribir el archivo PDF. Verifique la ruta y los permisos."
            ) from error
        raise ReportExportError("No fue posible generar el archivo PDF.") from error
    return path


def export_incident_pdf(incident_id, path, compress=True):
    """Genera el PDF del incidente en `path` y devuelve la ruta escrita."""
    access_controller.require(PERMISSION_VIEW_REPORTS)
    incident_id = _validate_id(incident_id)
    path = _checked_path(path)
    report = _incident_report(incident_id)
    return _write_pdf(report_pdf.build_incident_pdf, report, path, compress)


def export_general_pdf(filters, path, compress=True):
    """Genera el PDF del reporte general filtrado y devuelve la ruta escrita."""
    access_controller.require(PERMISSION_VIEW_REPORTS)
    path = _checked_path(path)
    general = _general_report(filters)
    return _write_pdf(report_pdf.build_general_pdf, general, path, compress)

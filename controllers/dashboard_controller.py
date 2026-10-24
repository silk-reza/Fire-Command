import logging

import mysql.connector

from controllers import access_controller
from models import dashboard_model
from utils.permissions import PERMISSION_VIEW_REPORTS

logger = logging.getLogger(__name__)


class DashboardServiceError(Exception):
    """Fallo técnico al leer los indicadores; el mensaje es apto para mostrar."""


def get_dashboard_data():
    """Indicadores del panel operativo (permiso view_reports; solo lectura)."""
    access_controller.require(PERMISSION_VIEW_REPORTS)
    try:
        return dashboard_model.get_dashboard_data()
    except mysql.connector.Error as error:
        logger.exception("Error al consultar el dashboard")
        raise DashboardServiceError(
            "No fue posible cargar los indicadores del panel."
        ) from error

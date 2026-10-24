import logging
from tkinter import ttk

import customtkinter as ctk

from controllers import dashboard_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.dashboard_controller import DashboardServiceError

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para consultar el panel operativo."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_UPDATED = "Datos actualizados."
EMPTY_TEXT = "Sin datos"
EMPTY_VALUE = "—"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"
BAR_WIDTH = 14

KPI_CARDS = (
    ("active", "Incidentes activos"),
    ("critical", "Incidentes críticos"),
    ("units", "Unidades disponibles"),
    ("response", "Tiempo promedio de respuesta"),
)
ACTIVITY_COLUMNS = (
    ("when", "Fecha/hora", 135, "w"),
    ("user", "Usuario", 100, "w"),
    ("action", "Acción", 100, "w"),
    ("entity", "Entidad", 90, "w"),
    ("detail", "Detalle", 300, "w"),
)
TYPE_COLUMNS = (
    ("type", "Tipo", 140, "w"),
    ("count", "Cantidad", 70, "e"),
    ("bar", "", 120, "w"),
)
PERIOD_COLUMNS = (
    ("date", "Fecha", 110, "w"),
    ("count", "Cantidad", 70, "e"),
    ("bar", "", 120, "w"),
)


def _format_datetime(value):
    return value.strftime("%d/%m/%Y %H:%M:%S") if value else EMPTY_VALUE


def _bar(count, maximum):
    """Barra de texto proporcional; sin dependencias gráficas."""
    if not maximum or not count:
        return ""
    return "█" * max(1, round(count / maximum * BAR_WIDTH))


class DashboardView(ctk.CTkToplevel):
    """Panel operativo de solo lectura como ventana secundaria de la principal."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Dashboard operativo")
        self.resizable(False, False)
        self.data = None

        self._build_cards()
        self._build_tables()
        self._build_buttons()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(parent)
        self.update_idletasks()
        try:
            self.grab_set()
        except Exception:
            # Si la ventana aún no es visible el foco modal es opcional
            pass
        self.load()

    def _build_cards(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(12, 6))
        self.kpi_labels = {}
        for key, title in KPI_CARDS:
            card = ctk.CTkFrame(row)
            card.pack(side="left", expand=True, fill="x", padx=4)
            ctk.CTkLabel(card, text=title, text_color="gray70").pack(pady=(8, 0))
            value = ctk.CTkLabel(card, text=EMPTY_VALUE, font=ctk.CTkFont(size=26, weight="bold"))
            value.pack(pady=(0, 8))
            self.kpi_labels[key] = value

    def _tree(self, parent, columns, height):
        tree = ttk.Treeview(
            parent, columns=[c[0] for c in columns], show="headings",
            selectmode="none", height=height, style="Dashboard.Treeview",
        )
        for key, title, width, anchor in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=width, anchor=anchor)
        return tree

    def _build_tables(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Dashboard.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=24, borderwidth=0,
        )
        style.configure("Dashboard.Treeview.Heading", background="#3a3a3a", foreground="white")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=4)

        left = ctk.CTkFrame(body)
        left.pack(side="left", fill="both", padx=(4, 6))
        ctk.CTkLabel(left, text="Actividad reciente", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=8, pady=(6, 2)
        )
        self.activity_tree = self._tree(left, ACTIVITY_COLUMNS, 13)
        self.activity_tree.pack(padx=8, pady=(0, 8))

        right = ctk.CTkFrame(body)
        right.pack(side="left", fill="both", padx=(0, 4))
        ctk.CTkLabel(right, text="Incidentes por tipo", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=8, pady=(6, 2)
        )
        self.types_tree = self._tree(right, TYPE_COLUMNS, 6)
        self.types_tree.pack(padx=8, pady=(0, 6))
        ctk.CTkLabel(
            right, text="Incidentes por período (últimos 7 días)", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=8, pady=(4, 2))
        self.period_tree = self._tree(right, PERIOD_COLUMNS, 6)
        self.period_tree.pack(padx=8, pady=(0, 8))

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=900, justify="left")
        self.message_label.pack(fill="x", padx=18)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(4, 12))
        self.refresh_button = ctk.CTkButton(bar, text="Refrescar", width=100, command=self.load)
        self.refresh_button.pack(side="left", padx=4)
        self.back_button = ctk.CTkButton(
            bar, text="Regresar", width=100, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        self.back_button.pack(side="left", padx=4)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def show_message(self, text, error=False):
        self.message_label.configure(text=text, text_color=COLOR_ERROR if error else COLOR_OK)

    def load(self):
        """Una sola consulta al controlador; ante un fallo se conservan los datos previos."""
        try:
            data = dashboard_controller.get_dashboard_data()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
            return
        except DashboardServiceError as error:
            self.show_message(str(error), error=True)
            return
        except Exception:
            logger.exception("Error en el panel operativo")
            self.show_message(MSG_INTERNAL, error=True)
            return
        self.data = data
        self._render(data)
        self.show_message(MSG_UPDATED)

    def _render(self, data):
        average = data["avg_response_minutes"]
        values = {
            "active": str(data["active_incidents"]),
            "critical": str(data["critical_incidents"]),
            "units": str(data["available_units"]),
            "response": EMPTY_VALUE if average is None else f"{average:.1f} min",
        }
        for key, text in values.items():
            self.kpi_labels[key].configure(text=text)

        self._fill(self.activity_tree, [
            (
                _format_datetime(item["occurred_at"]), item["username"] or EMPTY_VALUE,
                item["action"], item["entity"], item["detail"] or "",
            )
            for item in data["recent_activity"]
        ], 5)
        types = data["incidents_by_type"]
        top = max((t["count"] for t in types), default=0)
        self._fill(self.types_tree, [
            (t["type_name"], t["count"], _bar(t["count"], top)) for t in types
        ], 3)
        periods = data["incidents_by_period"]
        top = max((p["count"] for p in periods), default=0)
        self._fill(self.period_tree, [
            (p["date"], p["count"], _bar(p["count"], top)) for p in periods
        ], 3)

    def _fill(self, tree, rows, width):
        tree.delete(*tree.get_children())
        if not rows:
            tree.insert("", "end", values=(EMPTY_TEXT,) + ("",) * (width - 1))
            return
        for row in rows:
            tree.insert("", "end", values=row)

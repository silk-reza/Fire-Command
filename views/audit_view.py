import logging
from tkinter import ttk

import customtkinter as ctk

from controllers import audit_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.audit_controller import AuditServiceError, AuditValidationError

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para consultar la bitácora."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_TRUNCATED = "Se muestran los 500 más recientes; use filtros para acotar."
EMPTY_TEXT = "Sin registros"
EMPTY_VALUE = "—"
ALL_USERS = "Todos"
ALL_ACTIONS = "Todas"
ALL_ENTITIES = "Todas"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"
COLOR_WARNING = "#d97706"

COLUMNS = (
    ("when", "Fecha/hora", 140, "w"),
    ("user", "Usuario", 110, "w"),
    ("action", "Acción", 110, "w"),
    ("entity", "Entidad", 100, "w"),
    ("entity_id", "ID entidad", 80, "e"),
    ("detail", "Detalle", 520, "w"),
)


class AuditView(ctk.CTkToplevel):
    """Consulta de solo lectura de la bitácora de auditoría."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Bitácora")
        self.resizable(False, False)
        self.applied_filters = {}

        self._build_filters()
        self._build_table()
        self._build_status()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(parent)
        self.update_idletasks()
        try:
            self.grab_set()
        except Exception:
            # Si la ventana aún no es visible el foco modal es opcional
            pass

        self.load_options()
        self.search()

    def _build_filters(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(10, 4))

        def label(text, column):
            ctk.CTkLabel(bar, text=text).grid(row=0, column=column, sticky="e", padx=(8, 4))

        def combo(default, column):
            widget = ctk.CTkComboBox(bar, values=[default], width=140, state="readonly")
            widget.set(default)
            widget.grid(row=0, column=column, sticky="w")
            return widget

        label("Fecha desde", 0)
        self.date_from_entry = ctk.CTkEntry(bar, width=105, placeholder_text="AAAA-MM-DD")
        self.date_from_entry.grid(row=0, column=1, sticky="w")
        label("Fecha hasta", 2)
        self.date_to_entry = ctk.CTkEntry(bar, width=105, placeholder_text="AAAA-MM-DD")
        self.date_to_entry.grid(row=0, column=3, sticky="w")
        label("Usuario", 4)
        self.user_filter = combo(ALL_USERS, 5)
        label("Acción", 6)
        self.action_filter = combo(ALL_ACTIONS, 7)
        label("Entidad", 8)
        self.entity_filter = combo(ALL_ENTITIES, 9)
        self.search_button = ctk.CTkButton(bar, text="Buscar", width=80, command=self.search)
        self.search_button.grid(row=0, column=10, padx=(10, 4))
        self.clear_button = ctk.CTkButton(
            bar, text="Limpiar", width=80, fg_color="gray40", hover_color="gray30",
            command=self.clear_filters,
        )
        self.clear_button.grid(row=0, column=11)
        for entry in (self.date_from_entry, self.date_to_entry):
            entry.bind("<Return>", lambda event: self.search())

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Audit.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=24, borderwidth=0,
        )
        style.configure("Audit.Treeview.Heading", background="#3a3a3a", foreground="white")
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=4)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=17, style="Audit.Treeview",
        )
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, anchor=anchor)
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.tree.pack(side="left", fill="x", expand=True)
        self.scrollbar.pack(side="left", fill="y")

    def _build_status(self):
        self.count_label = ctk.CTkLabel(self, text="", anchor="w")
        self.count_label.pack(fill="x", padx=20)
        self.message_label = ctk.CTkLabel(self, text="", wraplength=1000, justify="left")
        self.message_label.pack(fill="x", padx=18)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(4, 10))
        self.refresh_button = ctk.CTkButton(
            bar, text="Refrescar", width=100, command=self.refresh
        )
        self.back_button = ctk.CTkButton(
            bar, text="Regresar", width=100, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        self.refresh_button.pack(side="left", padx=4)
        self.back_button.pack(side="left", padx=4)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def show_message(self, text, error=False):
        self.message_label.configure(text=text, text_color=COLOR_ERROR if error else COLOR_OK)

    def _run(self, function, *args):
        """Ejecuta una llamada al controlador; devuelve (éxito, resultado)."""
        try:
            return True, function(*args)
        except (AuditValidationError, AuditServiceError) as error:
            self.show_message(str(error), error=True)
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en la consulta de bitácora")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def load_options(self):
        ok, options = self._run(audit_controller.list_audit_filter_options)
        if not ok:
            return
        self.user_filter.configure(values=[ALL_USERS] + list(options["usernames"]))
        self.action_filter.configure(values=[ALL_ACTIONS] + list(options["actions"]))
        self.entity_filter.configure(values=[ALL_ENTITIES] + list(options["entities"]))

    def _current_filters(self):
        """Filtros del formulario; solo se incluyen los que tienen valor."""
        filters = {}
        for key, widget in (("date_from", self.date_from_entry), ("date_to", self.date_to_entry)):
            value = widget.get().strip()
            if value:
                filters[key] = value
        for key, widget, default in (
            ("username", self.user_filter, ALL_USERS),
            ("action", self.action_filter, ALL_ACTIONS),
            ("entity", self.entity_filter, ALL_ENTITIES),
        ):
            value = widget.get()
            if value and value != default:
                filters[key] = value
        return filters

    def search(self):
        self._query(self._current_filters())

    def refresh(self):
        """Repite la última búsqueda aplicada."""
        self._query(self.applied_filters)

    def clear_filters(self):
        self.date_from_entry.delete(0, "end")
        self.date_to_entry.delete(0, "end")
        self.user_filter.set(ALL_USERS)
        self.action_filter.set(ALL_ACTIONS)
        self.entity_filter.set(ALL_ENTITIES)
        self.search()

    def _query(self, filters):
        """Una sola consulta; ante un fallo se conservan los datos previos."""
        ok, data = self._run(audit_controller.search_audit, filters)
        if not ok:
            return
        self.applied_filters = filters
        self._render(data)

    def _render(self, data):
        self.tree.delete(*self.tree.get_children())
        rows = data["rows"]
        for item in rows:
            when = item["occurred_at"]
            self.tree.insert("", "end", values=(
                when.strftime("%d/%m/%Y %H:%M:%S") if when else EMPTY_VALUE,
                item["username"] or EMPTY_VALUE,
                item["action"], item["entity"],
                EMPTY_VALUE if item["entity_id"] is None else item["entity_id"],
                item["detail"] or "",
            ))
        if not rows:
            self.tree.insert("", "end", values=(EMPTY_TEXT, "", "", "", "", ""))
            self.count_label.configure(text=EMPTY_TEXT, text_color="gray70")
        else:
            self.count_label.configure(text=f"{len(rows)} registros", text_color="gray70")
        if data.get("truncated"):
            self.show_message(MSG_TRUNCATED)
            self.message_label.configure(text_color=COLOR_WARNING)
        else:
            self.message_label.configure(text="")

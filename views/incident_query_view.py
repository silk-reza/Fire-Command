import logging
from tkinter import ttk

import customtkinter as ctk

from controllers import incident_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.incident_controller import IncidentNotFoundError, IncidentValidationError

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para consultar incidentes."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "El incidente ya no existe."
MSG_SELECT_FIRST = "Seleccione un incidente de la tabla para ver su detalle."
ALL_TYPES = "Todos los tipos"
ALL_PRIORITIES = "Todas las prioridades"
ALL_STATES = "Todos los estados"
EMPTY_VALUE = "—"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"

COLUMNS = (
    ("code", "Código", 120, "w"),
    ("date", "Fecha de reporte", 130, "w"),
    ("type", "Tipo", 120, "w"),
    ("priority", "Prioridad", 90, "w"),
    ("state", "Estado", 100, "w"),
    ("address", "Dirección", 240, "w"),
    ("people", "Personas afectadas", 120, "e"),
)

# Campos cortos del detalle, en tres columnas de pares etiqueta/valor
SHORT_FIELDS = (
    ("code", "Código"), ("reported_at", "Fecha de reporte"), ("type", "Tipo"),
    ("priority", "Prioridad"), ("state", "Estado"), ("affected_people", "Personas afectadas"),
    ("latitude", "Latitud"), ("longitude", "Longitud"), ("registered_by", "Registrado por"),
    ("assigned_at", "Fecha de asignación"), ("attention_started_at", "Inicio de atención"),
    ("controlled_at", "Fecha de control"), ("closed_at", "Fecha de cierre"),
    ("cancelled_at", "Fecha de cancelación"),
)
# Campos de texto largo, uno por fila
LONG_FIELDS = (
    ("address", "Dirección"), ("reference", "Referencia"),
    ("description", "Descripción"), ("cancel_reason", "Motivo de cancelación"),
)


def _catalog_text(item):
    """Texto de un tipo o prioridad marcando los inactivos."""
    return item["name"] if item["active"] else f"{item['name']} (inactivo/a)"


def _format_date(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else EMPTY_VALUE


def _text(value):
    return EMPTY_VALUE if value is None or value == "" else str(value)


class IncidentQueryView(ctk.CTkToplevel):
    """Consulta de solo lectura de incidentes, con filtros y panel de detalle."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Consultar incidentes")
        self.resizable(False, False)
        self.selected_id = None
        self._refreshing = False
        # Solo presentación: texto del selector -> id
        self._type_ids = {}
        self._type_texts = {}
        self._priority_ids = {}
        self._priority_texts = {}
        self._state_ids = {}
        self._state_texts = {}

        self._build_filters()
        self._build_table()
        self._build_detail()
        self._build_buttons()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(parent)
        self.update_idletasks()
        try:
            self.grab_set()
        except Exception:
            # Si la ventana aún no es visible el foco modal es opcional
            pass

        self.load_options()
        self.refresh_list()

    def _build_filters(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(8, 2))

        def label(text, row, column):
            ctk.CTkLabel(bar, text=text).grid(row=row, column=column, sticky="e", padx=(8, 4), pady=2)

        def combo(default, row, column):
            widget = ctk.CTkComboBox(bar, values=[default], width=170, state="readonly")
            widget.set(default)
            widget.grid(row=row, column=column, sticky="w", pady=2)
            return widget

        label("Buscar", 0, 0)
        self.search_entry = ctk.CTkEntry(
            bar, width=250, placeholder_text="Código, dirección o descripción"
        )
        self.search_entry.grid(row=0, column=1, sticky="w", pady=2)
        label("Tipo", 0, 2)
        self.type_filter = combo(ALL_TYPES, 0, 3)
        label("Prioridad", 0, 4)
        self.priority_filter = combo(ALL_PRIORITIES, 0, 5)
        label("Estado", 0, 6)
        self.state_filter = combo(ALL_STATES, 0, 7)

        label("Fecha desde", 1, 0)
        self.date_from_entry = ctk.CTkEntry(bar, width=250, placeholder_text="AAAA-MM-DD o AAAA-MM-DD HH:MM")
        self.date_from_entry.grid(row=1, column=1, sticky="w", pady=2)
        label("Fecha hasta", 1, 2)
        self.date_to_entry = ctk.CTkEntry(
            bar, width=170, placeholder_text="AAAA-MM-DD o con HH:MM"
        )
        self.date_to_entry.grid(row=1, column=3, sticky="w", pady=2)

        actions = ctk.CTkFrame(bar, fg_color="transparent")
        actions.grid(row=1, column=4, columnspan=4, sticky="e", pady=2)
        self.apply_button = ctk.CTkButton(
            actions, text="Aplicar filtros", width=110, command=self.apply_filters
        )
        self.clear_button = ctk.CTkButton(
            actions, text="Quitar filtros", width=110, fg_color="gray40",
            hover_color="gray30", command=self.clear_filters,
        )
        self.refresh_button = ctk.CTkButton(
            actions, text="Refrescar", width=90, fg_color="gray40",
            hover_color="gray30", command=self.refresh,
        )
        for button in (self.apply_button, self.clear_button, self.refresh_button):
            button.pack(side="left", padx=4)

        # Enter aplica los filtros desde los campos de texto y fecha
        for entry in (self.search_entry, self.date_from_entry, self.date_to_entry):
            entry.bind("<Return>", lambda event: self.apply_filters())


    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "IncidentQuery.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=24, borderwidth=0,
        )
        style.configure("IncidentQuery.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("IncidentQuery.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=(4, 0))
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=5, style="IncidentQuery.Treeview",
        )
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, anchor=anchor)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda event: self.show_detail())

        self.count_label = ctk.CTkLabel(self, text="", anchor="w")
        self.count_label.pack(fill="x", padx=20, pady=(2, 0))

    def _build_detail(self):
        panel = ctk.CTkFrame(self)
        panel.pack(fill="x", padx=16, pady=4)
        self.detail_title = ctk.CTkLabel(
            panel, text="Detalle del incidente", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.detail_title.grid(row=0, column=0, columnspan=6, sticky="w", padx=12, pady=(6, 2))
        # Solo etiquetas: el detalle no tiene ningún campo editable
        self.detail_values = {}
        for index, (key, title) in enumerate(SHORT_FIELDS):
            row, pair = 1 + index % 5, index // 5
            ctk.CTkLabel(panel, text=title, text_color="gray60").grid(
                row=row, column=pair * 2, sticky="e", padx=(12, 6), pady=1
            )
            value = ctk.CTkLabel(panel, text=EMPTY_VALUE, anchor="w", width=150)
            value.grid(row=row, column=pair * 2 + 1, sticky="w", padx=(0, 12), pady=1)
            self.detail_values[key] = value
        for offset, (key, title) in enumerate(LONG_FIELDS):
            row = 6 + offset
            ctk.CTkLabel(panel, text=title, text_color="gray60").grid(
                row=row, column=0, sticky="ne", padx=(12, 6), pady=1
            )
            value = ctk.CTkLabel(
                panel, text=EMPTY_VALUE, anchor="w", justify="left", wraplength=780
            )
            value.grid(row=row, column=1, columnspan=5, sticky="w", padx=(0, 12), pady=1)
            self.detail_values[key] = value

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=900, justify="left")
        self.message_label.pack(fill="x", padx=18)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(2, 8))
        self.detail_button = ctk.CTkButton(
            bar, text="Ver detalle", width=110, command=self.show_detail
        )
        self.back_button = ctk.CTkButton(
            bar, text="Regresar", width=90, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        self.detail_button.pack(side="left", padx=4)
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
        except IncidentValidationError as error:
            self.show_message(str(error), error=True)
        except IncidentNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._clear_detail()
            self.refresh_list()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en la consulta de incidentes")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def load_options(self):
        """Relee tipos, prioridades y estados conservando las elecciones actuales."""
        ok_t, types = self._run(incident_controller.list_incident_types)
        if not ok_t:
            return
        ok_p, priorities = self._run(incident_controller.list_priorities)
        if not ok_p:
            return
        ok_s, states = self._run(incident_controller.list_incident_states)
        if not ok_s:
            return
        current = (self.type_filter_id(), self.priority_filter_id(), self.state_filter_id())
        self._type_ids = {_catalog_text(t): t["type_id"] for t in types}
        self._type_texts = {t["type_id"]: _catalog_text(t) for t in types}
        self._priority_ids = {_catalog_text(p): p["priority_id"] for p in priorities}
        self._priority_texts = {p["priority_id"]: _catalog_text(p) for p in priorities}
        self._state_ids = {s["name"]: s["state_id"] for s in states}
        self._state_texts = {s["state_id"]: s["name"] for s in states}
        # Los filtros incluyen elementos inactivos para consultar incidentes históricos
        self.type_filter.configure(values=[ALL_TYPES] + list(self._type_ids))
        self.type_filter.set(self._type_texts.get(current[0], ALL_TYPES))
        self.priority_filter.configure(values=[ALL_PRIORITIES] + list(self._priority_ids))
        self.priority_filter.set(self._priority_texts.get(current[1], ALL_PRIORITIES))
        self.state_filter.configure(values=[ALL_STATES] + list(self._state_ids))
        self.state_filter.set(self._state_texts.get(current[2], ALL_STATES))

    def type_filter_id(self):
        return self._type_ids.get(self.type_filter.get())

    def priority_filter_id(self):
        return self._priority_ids.get(self.priority_filter.get())

    def state_filter_id(self):
        return self._state_ids.get(self.state_filter.get())

    def refresh_list(self):
        """Recarga la tabla con los valores actuales de los filtros."""
        ok, incidents = self._run(
            incident_controller.filter_incidents,
            self.type_filter_id(), self.priority_filter_id(), self.state_filter_id(),
            self.search_entry.get(), self.date_from_entry.get(), self.date_to_entry.get(),
        )
        if not ok:
            # Ante un error los resultados anteriores se conservan
            return False
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for incident in incidents:
                self.tree.insert("", "end", iid=str(incident["incident_id"]), values=(
                    incident["code"],
                    incident["reported_at"].strftime("%Y-%m-%d %H:%M"),
                    incident["type_name"],
                    incident["priority_name"],
                    incident["state_name"],
                    incident["address"] or "",
                    incident.get("affected_people", ""),
                ))
            target = str(self.selected_id)
            if self.selected_id is not None and self.tree.exists(target):
                self.tree.selection_set(target)
                self.tree.see(target)
        finally:
            self._refreshing = False
        total = len(incidents)
        self.count_label.configure(text=f"{total} incidente" + ("" if total == 1 else "s"))
        return True

    def apply_filters(self):
        if self.refresh_list():
            self.show_message("")

    def clear_filters(self):
        self.search_entry.delete(0, "end")
        self.date_from_entry.delete(0, "end")
        self.date_to_entry.delete(0, "end")
        self.type_filter.set(ALL_TYPES)
        self.priority_filter.set(ALL_PRIORITIES)
        self.state_filter.set(ALL_STATES)
        if self.refresh_list():
            self.show_message("")

    def refresh(self):
        """Relee MySQL conservando los filtros tal como están escritos en pantalla."""
        self.load_options()
        if not self.refresh_list():
            return
        self.show_message("")
        if self.selected_id is not None:
            self._load_detail(self.selected_id)

    def _on_select(self, event=None):
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection or int(selection[0]) == self.selected_id:
            return
        self._load_detail(int(selection[0]))

    def show_detail(self):
        """Carga el detalle de la fila seleccionada, siempre desde MySQL."""
        selection = self.tree.selection()
        if not selection:
            self.show_message(MSG_SELECT_FIRST, error=True)
            return
        self._load_detail(int(selection[0]))

    def _load_detail(self, incident_id):
        ok, incident = self._run(incident_controller.get_incident, incident_id)
        if not ok:
            return
        self.selected_id = incident["incident_id"]
        values = self.detail_values
        priority = f"{incident['priority_name']} (nivel {incident['priority_level']})"
        shown = {
            "code": incident["code"],
            "reported_at": _format_date(incident["reported_at"]),
            "type": incident["type_name"],
            "priority": priority,
            "state": incident["state_name"],
            "affected_people": incident["affected_people"],
            "latitude": incident["latitude"],
            "longitude": incident["longitude"],
            "registered_by": incident["registered_by_username"],
            "assigned_at": _format_date(incident["assigned_at"]),
            "attention_started_at": _format_date(incident["attention_started_at"]),
            "controlled_at": _format_date(incident["controlled_at"]),
            "closed_at": _format_date(incident["closed_at"]),
            "cancelled_at": _format_date(incident["cancelled_at"]),
            "address": incident["address"],
            "reference": incident["reference"],
            "description": incident["description"],
            "cancel_reason": incident["cancel_reason"],
        }
        for key, value in shown.items():
            values[key].configure(text=_text(value))
        self.detail_title.configure(text=f"Detalle del incidente {incident['code']}")

    def _clear_detail(self):
        self.selected_id = None
        for label in self.detail_values.values():
            label.configure(text=EMPTY_VALUE)
        self.detail_title.configure(text="Detalle del incidente")

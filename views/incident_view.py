import logging
from tkinter import messagebox, ttk

import customtkinter as ctk

from controllers import access_controller, incident_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.incident_controller import (
    DuplicateIncidentCodeError,
    IncidentNotFoundError,
    IncidentStateError,
    IncidentValidationError,
)
from utils import session
from utils.permissions import PERMISSION_OPERATE_INCIDENTS

logger = logging.getLogger(__name__)

MSG_DENIED_WRITE = "No tiene permiso para modificar incidentes."
MSG_DENIED_READ = "No tiene permiso para consultar incidentes."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "El incidente ya no existe."
MSG_TERMINAL = "Un incidente cerrado o cancelado no admite modificaciones."
MSG_REASON_REQUIRED = "No se canceló el incidente: el motivo es obligatorio."
READ_ONLY_TEXT = "Modo solo lectura"
ALL_TYPES = "Todos los tipos"
ALL_PRIORITIES = "Todas las prioridades"
ALL_STATES = "Todos los estados"
TYPE_PLACEHOLDER = "Seleccione un tipo"
PRIORITY_PLACEHOLDER = "Seleccione una prioridad"
COORDINATES_HINT = "Ambas o ninguna"
INITIAL_STATE_TEXT = "Estado inicial: Reportado"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"
COLOR_WARNING = "#d97706"
# Estados sin edición; solo se usa para presentar y habilitar controles
TERMINAL_STATES = ("CERRADO", "CANCELADO")
CANCELLABLE_STATES = ("REPORTADO", "ASIGNADO")
ATTENTION_STATE = "EN_ATENCION"
CONTROLLED_STATE = "CONTROLADO"
DESCRIPTION_LIMIT = 60

COLUMNS = (
    ("code", "Código", 120, 120),
    ("date", "Fecha", 130, 130),
    ("type", "Tipo", 130, 130),
    ("priority", "Prioridad", 90, 90),
    ("state", "Estado", 110, 110),
    ("address", "Dirección", 240, 240),
    ("description", "Descripción", 300, 300),
)


def _catalog_text(item):
    """Texto de un tipo o prioridad para los selectores."""
    return item["name"] if item["active"] else f"{item['name']} (inactiva)"


def _format_date(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _shorten(text, limit=DESCRIPTION_LIMIT):
    """Acorta solo para la tabla; el detalle conserva el texto completo."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class IncidentView(ctk.CTkToplevel):
    """Módulo de incidentes como ventana secundaria modal de la principal."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Incidentes")
        self.resizable(False, False)
        self.selected_id = None
        self.selected_state_code = None
        self.selected_controlled_at = None
        self._refreshing = False
        # Solo presentación: texto del selector -> id
        self._type_ids = {}
        self._type_texts = {}
        self._types = []
        self._priority_ids = {}
        self._priority_texts = {}
        self._priorities = []
        self._state_ids = {}
        self._state_texts = {}
        # Tipo y prioridad actuales del incidente en edición; pueden estar inactivos
        self.selected_type_id = None
        self.selected_priority_id = None
        # La escritura la decide el permiso; el controlador vuelve a validarlo
        self.can_write = access_controller.can(PERMISSION_OPERATE_INCIDENTS)

        self._build_search()
        self._build_table()
        self._build_form()
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
        self._reset_form()

    def _build_search(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(bar, text="Buscar").pack(side="left", padx=(0, 8))
        self.search_entry = ctk.CTkEntry(
            bar, width=200, placeholder_text="Código, descripción o dirección"
        )
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda event: self.search())
        self.search_button = ctk.CTkButton(bar, text="Buscar", width=70, command=self.search)
        self.search_button.pack(side="left", padx=6)

        def combo(label, default, width):
            ctk.CTkLabel(bar, text=label).pack(side="left", padx=(8, 4))
            widget = ctk.CTkComboBox(
                bar, values=[default], width=width, state="readonly",
                command=lambda value: self.refresh_list(),
            )
            widget.set(default)
            widget.pack(side="left")
            return widget

        self.type_filter = combo("Tipo", ALL_TYPES, 150)
        self.priority_filter = combo("Prioridad", ALL_PRIORITIES, 150)
        self.state_filter = combo("Estado", ALL_STATES, 140)
        self.clear_search_button = ctk.CTkButton(
            bar, text="Quitar filtros", width=100, fg_color="gray40", hover_color="gray30",
            command=self.clear_search,
        )
        self.clear_search_button.pack(side="left", padx=6)

        self.read_only_label = ctk.CTkLabel(self, text=READ_ONLY_TEXT, text_color=COLOR_WARNING)
        if not self.can_write:
            self.read_only_label.pack(anchor="w", padx=18)

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Incident.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=26, borderwidth=0,
        )
        style.configure("Incident.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Incident.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=4)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=6, style="Incident.Treeview",
        )
        for key, title, width, minwidth in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=minwidth, anchor="w")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_form(self):
        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=16, pady=4)
        self.form_title = ctk.CTkLabel(
            form, text="Nuevo incidente", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.form_title.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(6, 2))

        self.code_entry = self._field(form, "Código", 1, 0)
        ctk.CTkLabel(form, text="Tipo").grid(row=1, column=2, sticky="e", padx=(12, 6), pady=2)
        self.type_combo = ctk.CTkComboBox(
            form, values=[TYPE_PLACEHOLDER], width=230, state="readonly"
        )
        self.type_combo.set(TYPE_PLACEHOLDER)
        self.type_combo.grid(row=1, column=3, sticky="w", padx=(0, 12), pady=2)

        ctk.CTkLabel(form, text="Prioridad").grid(row=2, column=0, sticky="e", padx=(12, 6), pady=2)
        self.priority_combo = ctk.CTkComboBox(
            form, values=[PRIORITY_PLACEHOLDER], width=230, state="readonly"
        )
        self.priority_combo.set(PRIORITY_PLACEHOLDER)
        self.priority_combo.grid(row=2, column=1, sticky="w", padx=(0, 12), pady=2)
        self.affected_entry = self._field(form, "Personas afectadas", 2, 2)

        self.address_entry = self._field(form, "Dirección", 3, 0, width=230)
        self.reference_entry = self._field(form, "Referencia (opcional)", 3, 2, width=230)
        self.latitude_entry = self._field(form, "Latitud (opcional)", 4, 0, width=230)
        self.longitude_entry = self._field(form, "Longitud (opcional)", 4, 2, width=230)
        ctk.CTkLabel(form, text=COORDINATES_HINT, text_color="gray60").grid(
            row=4, column=4, sticky="w", padx=(0, 12)
        )

        ctk.CTkLabel(form, text="Descripción").grid(
            row=5, column=0, sticky="ne", padx=(12, 6), pady=2
        )
        self.description_text = ctk.CTkTextbox(form, width=640, height=52)
        self.description_text.grid(
            row=5, column=1, columnspan=3, sticky="w", padx=(0, 12), pady=2
        )

        # Estado, registrador y fecha solo se muestran; el estado cambia por acciones de negocio
        info = ctk.CTkFrame(form, fg_color="transparent")
        info.grid(row=6, column=0, columnspan=4, sticky="w", padx=12, pady=(2, 0))
        self.state_label = ctk.CTkLabel(info, text=INITIAL_STATE_TEXT)
        self.state_label.pack(side="left", padx=(0, 24))
        self.registrar_label = ctk.CTkLabel(info, text="")
        self.registrar_label.pack(side="left", padx=(0, 24))
        self.reported_label = ctk.CTkLabel(info, text="")
        self.reported_label.pack(side="left", padx=(0, 24))
        self.control_label = ctk.CTkLabel(info, text="")
        self.control_label.pack(side="left", padx=(0, 24))
        self.closed_label = ctk.CTkLabel(info, text="")
        self.closed_label.pack(side="left")
        self.hint_label = ctk.CTkLabel(form, text="", text_color=COLOR_WARNING)
        self.hint_label.grid(row=7, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 4))

    def _field(self, form, label, row, column, width=210):
        ctk.CTkLabel(form, text=label).grid(
            row=row, column=column, sticky="e", padx=(12, 6), pady=2
        )
        entry = ctk.CTkEntry(form, width=width)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=2)
        return entry

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=1000, justify="left")
        self.message_label.pack(fill="x", padx=18, pady=(0, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(4, 10))
        self.create_button = ctk.CTkButton(bar, text="Crear", width=100, command=self.create)
        self.save_button = ctk.CTkButton(bar, text="Guardar cambios", width=130, command=self.save)
        self.cancel_button = ctk.CTkButton(
            bar, text="Cancelar incidente", width=140, fg_color="#b45309",
            hover_color="#92400e", command=self.cancel_incident,
        )
        self.control_button = ctk.CTkButton(
            bar, text="Marcar controlado", width=130, command=self.mark_controlled,
        )
        self.reactivate_button = ctk.CTkButton(
            bar, text="Reactivar atención", width=130, command=self.reactivate,
        )
        self.close_incident_button = ctk.CTkButton(
            bar, text="Cerrar incidente", width=120, command=self.close_incident,
        )
        self.clear_button = ctk.CTkButton(
            bar, text="Limpiar", width=90, fg_color="gray40", hover_color="gray30",
            command=self.clear_form,
        )
        self.back_button = ctk.CTkButton(
            bar, text="Regresar", width=90, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        for button in (
            self.create_button, self.save_button, self.cancel_button,
            self.control_button, self.reactivate_button, self.close_incident_button,
            self.clear_button, self.back_button,
        ):
            button.pack(side="left", padx=4)

    def confirm(self, message):
        """Pide confirmación; se puede reemplazar en pruebas."""
        return messagebox.askyesno("Confirmar", message, parent=self)

    def notify(self, message):
        """Aviso informativo; se puede reemplazar en pruebas."""
        messagebox.showinfo("FireCommand", message, parent=self)

    def ask_reason(self, prompt):
        """Pide el motivo de cancelación; devuelve None si se cierra el diálogo."""
        try:
            dialog = ctk.CTkInputDialog(text=prompt, title="Motivo")
            return dialog.get_input()
        finally:
            # El diálogo toma el grab y Tk no lo devuelve; se restablece el foco modal
            self._restore_modal()

    def _restore_modal(self):
        try:
            if self.winfo_exists():
                self.lift()
                self.grab_set()
                self.focus_set()
        except Exception:
            pass

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def show_message(self, text, error=False):
        self.message_label.configure(text=text, text_color=COLOR_ERROR if error else COLOR_OK)

    def _run(self, function, *args, write=False):
        """Ejecuta una llamada al controlador; devuelve (éxito, resultado)."""
        try:
            return True, function(*args)
        except (
            IncidentValidationError, DuplicateIncidentCodeError, IncidentStateError,
        ) as error:
            self.show_message(str(error), error=True)
        except IncidentNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._reset_form()
            self.refresh_list()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED_WRITE if write else MSG_DENIED_READ, error=True)
        except Exception:
            logger.exception("Error en el módulo de incidentes")
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
        filter_ids = (self.type_filter_id(), self.priority_filter_id(), self.state_filter_id())
        form_ids = (self.type_form_id(), self.priority_form_id())
        self._types = types
        self._type_ids = {_catalog_text(t): t["type_id"] for t in types}
        self._type_texts = {t["type_id"]: _catalog_text(t) for t in types}
        self._priorities = priorities
        self._priority_ids = {_catalog_text(p): p["priority_id"] for p in priorities}
        self._priority_texts = {p["priority_id"]: _catalog_text(p) for p in priorities}
        self._state_ids = {s["name"]: s["state_id"] for s in states}
        self._state_texts = {s["state_id"]: s["name"] for s in states}
        # Los filtros conservan todo el catálogo para alcanzar incidentes de elementos inactivos
        self.type_filter.configure(values=[ALL_TYPES] + list(self._type_ids))
        self.type_filter.set(self._type_texts.get(filter_ids[0], ALL_TYPES))
        self.priority_filter.configure(values=[ALL_PRIORITIES] + list(self._priority_ids))
        self.priority_filter.set(self._priority_texts.get(filter_ids[1], ALL_PRIORITIES))
        self.state_filter.configure(values=[ALL_STATES] + list(self._state_ids))
        self.state_filter.set(self._state_texts.get(filter_ids[2], ALL_STATES))
        self._apply_form_options(form_ids[0], form_ids[1])

    def _apply_form_options(self, type_id=None, priority_id=None):
        """Reconstruye las opciones del formulario según el modo (alta o edición)."""
        types = [t for t in self._types if t["active"] or t["type_id"] == self.selected_type_id]
        texts = [_catalog_text(t) for t in types]
        self.type_combo.configure(values=texts)
        text = self._type_texts.get(type_id)
        self.type_combo.set(text if text in texts else TYPE_PLACEHOLDER)

        priorities = [
            p for p in self._priorities
            if p["active"] or p["priority_id"] == self.selected_priority_id
        ]
        texts = [_catalog_text(p) for p in priorities]
        self.priority_combo.configure(values=texts)
        text = self._priority_texts.get(priority_id)
        self.priority_combo.set(text if text in texts else PRIORITY_PLACEHOLDER)

    def type_filter_id(self):
        return self._type_ids.get(self.type_filter.get())

    def priority_filter_id(self):
        return self._priority_ids.get(self.priority_filter.get())

    def state_filter_id(self):
        return self._state_ids.get(self.state_filter.get()) if self._state_ids else None

    def type_form_id(self):
        return self._type_ids.get(self.type_combo.get())

    def priority_form_id(self):
        return self._priority_ids.get(self.priority_combo.get())

    def refresh_list(self, select_id=None):
        """Recarga la tabla con el texto de búsqueda y los filtros actuales."""
        ok, incidents = self._run(
            incident_controller.filter_incidents,
            self.type_filter_id(), self.priority_filter_id(), self.state_filter_id(),
            self.search_entry.get(),
        )
        if not ok:
            return
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for incident in incidents:
                self.tree.insert("", "end", iid=str(incident["incident_id"]), values=(
                    incident["code"],
                    _format_date(incident["reported_at"]),
                    incident["type_name"],
                    incident["priority_name"],
                    incident["state_name"],
                    incident["address"] or "",
                    _shorten(incident["description"]),
                ))
            target = str(select_id if select_id is not None else self.selected_id)
            if self.tree.exists(target):
                self.tree.selection_set(target)
                self.tree.see(target)
        finally:
            self._refreshing = False

    def search(self):
        self.refresh_list()

    def clear_search(self):
        self.search_entry.delete(0, "end")
        self.type_filter.set(ALL_TYPES)
        self.priority_filter.set(ALL_PRIORITIES)
        self.state_filter.set(ALL_STATES)
        self.refresh_list()

    def _on_select(self, event=None):
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection:
            return
        # Evita recargar (y perder lo escrito) cuando la fila ya está cargada
        if self.selected_id is not None and int(selection[0]) == self.selected_id:
            return
        ok, incident = self._run(incident_controller.get_incident, int(selection[0]))
        if ok:
            self._load_incident(incident)

    def _load_incident(self, incident):
        # Se relee el catálogo por si cambió con la ventana abierta
        self.selected_type_id = incident["type_id"]
        self.selected_priority_id = incident["priority_id"]
        self.load_options()
        self._apply_form_options(incident["type_id"], incident["priority_id"])
        self.selected_id = incident["incident_id"]
        self.selected_state_code = incident["state_code"]
        self.selected_controlled_at = incident.get("controlled_at")
        self._fill_form(incident)
        self.form_title.configure(text=f"Editando: {incident['code']}")
        self.state_label.configure(text=f"Estado actual: {incident['state_name']}")
        self.registrar_label.configure(
            text=f"Registrado por: {incident['registered_by_username']}"
        )
        self.reported_label.configure(
            text=f"Fecha de reporte: {_format_date(incident['reported_at'])}"
        )
        self._show_lifecycle_dates(incident)
        self._update_controls()

    def _entries(self):
        return (
            self.code_entry, self.address_entry, self.reference_entry,
            self.latitude_entry, self.longitude_entry, self.affected_entry,
        )

    def _set_editable(self, enabled):
        """Habilita o bloquea los campos descriptivos (el código se trata aparte)."""
        state = "normal" if enabled else "disabled"
        for entry in self._entries()[1:]:
            entry.configure(state=state)
        self.description_text.configure(state=state)
        combo_state = "readonly" if enabled else "disabled"
        self.type_combo.configure(state=combo_state)
        self.priority_combo.configure(state=combo_state)
        code_enabled = enabled and self.selected_id is None
        self.code_entry.configure(state="normal" if code_enabled else "disabled")

    def _fill_form(self, incident):
        # Los campos se desbloquean para poder escribir y luego se aplica el modo vigente
        self._set_editable(True)
        latitude, longitude = incident["latitude"], incident["longitude"]
        values = (
            incident["code"], incident["address"] or "", incident["reference"] or "",
            "" if latitude is None else str(latitude),
            "" if longitude is None else str(longitude),
            str(incident["affected_people"]),
        )
        self.code_entry.configure(state="normal")
        for entry, value in zip(self._entries(), values):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", incident["description"] or "")

    def _reset_form(self):
        self._set_editable(True)
        self.code_entry.configure(state="normal")
        for entry in self._entries():
            entry.delete(0, "end")
        self.description_text.delete("1.0", "end")
        self.selected_type_id = None
        self.selected_priority_id = None
        self._apply_form_options(None, None)
        self.selected_id = None
        self.selected_state_code = None
        self.selected_controlled_at = None
        self._refreshing = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._refreshing = False
        self.form_title.configure(text="Nuevo incidente")
        self.state_label.configure(text=INITIAL_STATE_TEXT)
        # Solo presentación: el registrador real lo fija el controlador desde la sesión
        current = session.get_session()
        username = current["username"] if current else ""
        self.registrar_label.configure(text=f"Registrado por: {username}")
        self.reported_label.configure(text="")
        self._show_lifecycle_dates(None)
        self._update_controls()

    def clear_form(self):
        self._reset_form()
        self.message_label.configure(text="")

    def _is_terminal(self):
        return self.selected_state_code in TERMINAL_STATES

    def _is_cancellable(self):
        """Presentación: el controlador decide si la cancelación procede."""
        if self.selected_state_code in CANCELLABLE_STATES:
            return True
        return self.selected_state_code == ATTENTION_STATE and self.selected_controlled_at is None

    def _update_controls(self):
        selected = self.selected_id is not None
        terminal = selected and self._is_terminal()
        self._set_editable(self.can_write and not terminal)
        can_create = self.can_write and not selected
        can_save = self.can_write and selected and not terminal
        can_cancel = self.can_write and selected and self._is_cancellable()
        self.create_button.configure(state="normal" if can_create else "disabled")
        self.save_button.configure(state="normal" if can_save else "disabled")
        self.cancel_button.configure(state="normal" if can_cancel else "disabled")
        # Presentación: el controlador valida de nuevo el estado y las condiciones
        usable = self.can_write and selected
        self.control_button.configure(
            state="normal" if usable and self.selected_state_code == ATTENTION_STATE
            else "disabled"
        )
        controlled = usable and self.selected_state_code == CONTROLLED_STATE
        self.reactivate_button.configure(state="normal" if controlled else "disabled")
        self.close_incident_button.configure(state="normal" if controlled else "disabled")
        self.hint_label.configure(text=MSG_TERMINAL if terminal else "")

    def _form_data(self):
        # Nunca se envía estado, usuario ni fechas; coordenadas y personas viajan como texto
        data = {
            "type_id": self.type_form_id(),
            "priority_id": self.priority_form_id(),
            "address": self.address_entry.get(),
            "reference": self.reference_entry.get(),
            "latitude": self.latitude_entry.get(),
            "longitude": self.longitude_entry.get(),
            "description": self.description_text.get("1.0", "end-1c"),
            "affected_people": self.affected_entry.get(),
        }
        if self.selected_id is None:
            data["code"] = self.code_entry.get()
        return data

    def create(self):
        if self.selected_id is not None:
            return
        ok, incident = self._run(
            incident_controller.create_incident, self._form_data(), write=True
        )
        if ok:
            # Sin selección: el formulario vuelve al modo alta
            self._reset_form()
            self.refresh_list()
            self.show_message(f"Incidente {incident['code']} creado correctamente.")

    def save(self):
        if self.selected_id is None:
            return
        ok, incident = self._run(
            incident_controller.update_incident, self.selected_id, self._form_data(), write=True
        )
        if ok:
            self._load_incident(incident)
            self.refresh_list()
            self.show_message(f"Incidente {incident['code']} actualizado correctamente.")

    def cancel_incident(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        prompt = (
            f"Motivo de la cancelación del incidente {code}.\n"
            "Los despachos en borrador y activos del incidente también se cancelarán."
        )
        reason = self.ask_reason(prompt)
        if reason is None or reason == "":
            self.show_message(MSG_REASON_REQUIRED, error=True)
            return
        message = (
            f"¿Cancelar el incidente {code}? Sus despachos en borrador y activos también "
            "se cancelarán y sus unidades se liberarán: las Despachada vuelven a "
            "Disponible y las En ruta o En escena pasan a Regresando. Esta acción no "
            "se puede deshacer."
        )
        if not self.confirm(message):
            return
        ok, incident = self._run(
            incident_controller.cancel_incident, self.selected_id, reason, write=True
        )
        if ok:
            self._load_incident(incident)
            self.refresh_list()
            self.show_message(
                f"Incidente {incident['code']} cancelado correctamente. "
                "Sus despachos en borrador y activos también quedaron cancelados."
            )

    def _show_lifecycle_dates(self, incident):
        """Muestra, solo cuando existen, las fechas de control y de cierre."""
        controlled = incident.get("controlled_at") if incident else None
        closed = incident.get("closed_at") if incident else None
        self.control_label.configure(
            text=f"Fecha de control: {_format_date(controlled)}" if controlled else ""
        )
        self.closed_label.configure(
            text=f"Fecha de cierre: {_format_date(closed)}" if closed else ""
        )

    def mark_controlled(self):
        code = self.code_entry.get()
        message = (
            f"¿Marcar el incidente {code} como controlado? Pasará a Controlado; "
            "no cierra el incidente."
        )
        self._lifecycle(
            incident_controller.mark_incident_controlled, message,
            "Incidente {code} marcado como controlado.",
        )

    def reactivate(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        reason = self.ask_reason(f"Motivo de la reactivación del incidente {code}.")
        if reason is None or reason == "":
            self.show_message("No se reactivó el incidente: el motivo es obligatorio.", error=True)
            return
        message = f"¿Reactivar la atención del incidente {code}? Volverá a En atención."
        self._lifecycle(
            incident_controller.reactivate_incident, message,
            "Incidente {code} reactivado: volvió a En atención.", reason,
        )

    def close_incident(self):
        code = self.code_entry.get()
        message = (
            f"¿Cerrar el incidente {code}? Pasará a Cerrado y será definitivo: ya no podrá "
            "modificarse, cancelarse ni reactivarse."
        )
        self._lifecycle(
            incident_controller.close_incident, message, "Incidente {code} cerrado correctamente.",
        )

    def _lifecycle(self, function, confirmation, success_text, *extra):
        if self.selected_id is None:
            return
        if not self.confirm(confirmation):
            return
        ok, incident = self._run(function, self.selected_id, *extra, write=True)
        if ok:
            self._load_incident(incident)
            self.refresh_list()
            self.show_message(success_text.format(code=incident["code"]))
        elif self.selected_id is not None:
            # El estado pudo cambiar: se relee el incidente sin tocar el mensaje de error
            try:
                self._load_incident(incident_controller.get_incident(self.selected_id))
            except Exception:
                pass
            self.refresh_list()

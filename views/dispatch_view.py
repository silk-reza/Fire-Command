import logging
from tkinter import messagebox, ttk

import customtkinter as ctk

from controllers import dispatch_controller, access_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.dispatch_controller import (
    DispatchHasNoUnitsError,
    DispatchIncidentNotFoundError,
    DispatchNotFoundError,
    DispatchStateError,
    DispatchValidationError,
    DraftUnitNotFoundError,
    DuplicateDraftUnitError,
    IncidentTerminalError,
    UnitNotEligibleError,
    UnitNotFoundError,
)
from utils.permissions import PERMISSION_OPERATE_DISPATCHES

logger = logging.getLogger(__name__)

MSG_DENIED_WRITE = "No tiene permiso para modificar despachos."
MSG_DENIED_READ = "No tiene permiso para consultar despachos."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "El despacho ya no existe."
MSG_REASON_REQUIRED = "No se canceló el borrador: el motivo es obligatorio."
MSG_SELECT_AVAILABLE = "Seleccione una unidad de la lista de unidades disponibles."
MSG_SELECT_DRAFT_UNIT = "Seleccione una unidad de la lista de unidades del despacho."
MSG_NOT_DRAFT = "Las observaciones y las unidades solo se editan en un borrador."
READ_ONLY_TEXT = "Modo solo lectura"
SHOW_ALL = "Todos"
SHOW_DRAFTS = "Solo borradores"
ALL_INCIDENTS = "Todos los incidentes"
ALL_UNIT_TYPES = "Todos los tipos"
ALL_STATIONS = "Todas las estaciones"
INCIDENT_PLACEHOLDER = "Seleccione un incidente"
NO_ADDRESS = "Sin dirección"
UNAVAILABLE_MARK = "No disponible ahora"
AVAILABLE_MARK = "Disponible"
EMPTY_VALUE = "—"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"
COLOR_WARNING = "#d97706"
# Solo presentación: habilita o bloquea controles; el controlador valida el estado
DRAFT_STATE = "CREADO"
CANCELLED_STATE = "CANCELADO"
ACTIVE_STATE = "ACTIVO"
COMPLETED_STATE = "COMPLETADO"
# Solo presentación: cada estado real de la unidad habilita una única acción
# (botón, función del controlador, texto de confirmación, mensaje de éxito,
# estados de despacho en los que se ofrece); el controlador valida de nuevo
UNIT_ACTIONS = {
    "DESPACHADA": ("en_route_button", "mark_unit_en_route", "Marcar en ruta",
                   "en ruta", (ACTIVE_STATE,)),
    "EN_RUTA": ("on_scene_button", "mark_unit_on_scene", "Marcar en escena",
                "en escena", (ACTIVE_STATE,)),
    "EN_ESCENA": ("return_button", "start_unit_return", "Iniciar regreso",
                  "con regreso iniciado", (ACTIVE_STATE,)),
    "REGRESANDO": ("available_button", "mark_unit_available", "Marcar disponible",
                   "disponible", (ACTIVE_STATE, COMPLETED_STATE, CANCELLED_STATE)),
}
OBSERVATIONS_LIMIT = 60

DISPATCH_COLUMNS = (
    ("number", "N.º", 60, "e"),
    ("incident", "Incidente", 110, "w"),
    ("state", "Estado", 90, "w"),
    ("units", "Unidades", 70, "e"),
    ("created_by", "Creado por", 120, "w"),
    ("observations", "Observaciones", 290, "w"),
)
DRAFT_UNIT_COLUMNS = (
    ("code", "Código", 70),
    ("name", "Nombre", 100),
    ("type", "Tipo", 80),
    ("station", "Estación", 90),
    ("state", "Estado real", 80),
    ("mark", "Disponibilidad", 110),
)
AVAILABLE_COLUMNS = (
    ("code", "Código", 70),
    ("name", "Nombre", 110),
    ("type", "Tipo", 90),
    ("station", "Estación", 110),
    ("state", "Estado", 80),
)


def _format_date(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else EMPTY_VALUE


def _shorten(text, limit=OBSERVATIONS_LIMIT):
    """Acorta solo para la tabla; el detalle conserva el texto completo."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _incident_text(incident):
    address = incident["address"] or NO_ADDRESS
    return f"{incident['code']} — {address} ({incident['state_name']})"


def _station_text(unit):
    return f"{unit['station_code']} — {unit['station_name']}"


class DispatchView(ctk.CTkToplevel):
    """Módulo de borradores de despacho como ventana secundaria modal."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Despachos (borradores)")
        self.resizable(False, False)
        self.selected_id = None
        self.selected_state_code = None
        self._loaded_observations = ""
        self._refreshing = False
        self._units = {}
        # Solo presentación: texto del selector -> id
        self._incident_options = {}
        self._filter_incidents = {}
        self._type_options = {}
        self._station_options = {}
        # La escritura la decide el permiso; el controlador vuelve a validarlo
        self.can_write = access_controller.can(PERMISSION_OPERATE_DISPATCHES)

        self._build_filters()
        self._build_table()
        self._build_detail()
        self._build_units()
        self._build_buttons()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(parent)
        self.update_idletasks()
        try:
            self.grab_set()
        except Exception:
            # Si la ventana aún no es visible el foco modal es opcional
            pass

        self.load_incident_options()
        self.refresh_list()
        self._reset_form()

    def _combo(self, parent, default, width, command=None):
        widget = ctk.CTkComboBox(
            parent, values=[default], width=width, state="readonly", command=command
        )
        widget.set(default)
        return widget

    def _build_filters(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(10, 2))
        ctk.CTkLabel(bar, text="Mostrar").pack(side="left", padx=(0, 6))
        self.show_filter = self._combo(
            bar, SHOW_ALL, 150, lambda value: self.refresh_list()
        )
        self.show_filter.configure(values=[SHOW_ALL, SHOW_DRAFTS])
        self.show_filter.pack(side="left")
        ctk.CTkLabel(bar, text="Incidente").pack(side="left", padx=(14, 6))
        self.incident_filter = self._combo(
            bar, ALL_INCIDENTS, 200, lambda value: self.refresh_list()
        )
        self.incident_filter.pack(side="left")
        self.clear_filters_button = ctk.CTkButton(
            bar, text="Quitar filtros", width=100, fg_color="gray40", hover_color="gray30",
            command=self.clear_filters,
        )
        self.clear_filters_button.pack(side="left", padx=10)
        self.read_only_label = ctk.CTkLabel(bar, text=READ_ONLY_TEXT, text_color=COLOR_WARNING)
        if not self.can_write:
            self.read_only_label.pack(side="left", padx=10)

    def _make_tree(self, parent, columns, height, anchors=None):
        tree = ttk.Treeview(
            parent, columns=[c[0] for c in columns], show="headings",
            selectmode="browse", height=height, style="Dispatch.Treeview",
        )
        for index, column in enumerate(columns):
            key, title, width = column[0], column[1], column[2]
            anchor = anchors[index] if anchors else "w"
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=width, anchor=anchor)
        return tree

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Dispatch.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=24, borderwidth=0,
        )
        style.configure("Dispatch.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Dispatch.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=2)
        self.tree = self._make_tree(
            holder, DISPATCH_COLUMNS, 5, anchors=[c[3] for c in DISPATCH_COLUMNS]
        )
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_detail(self):
        detail = ctk.CTkFrame(self)
        detail.pack(fill="x", padx=16, pady=4)
        self.form_title = ctk.CTkLabel(
            detail, text="Nuevo borrador", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.form_title.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(4, 0))

        self.incident_label = ctk.CTkLabel(detail, text="", anchor="w", wraplength=600)
        self.incident_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=12)
        self.incident_state_label = ctk.CTkLabel(detail, text="", anchor="w")
        self.incident_state_label.grid(row=1, column=2, sticky="w", padx=12)
        # El estado del despacho solo se muestra; cambia por acciones de negocio
        self.state_label = ctk.CTkLabel(detail, text="Estado del despacho: Creado")
        self.state_label.grid(row=1, column=3, sticky="w", padx=12)
        self.created_label = ctk.CTkLabel(detail, text="", anchor="w")
        self.created_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12)
        self.cancel_label = ctk.CTkLabel(
            detail, text="", anchor="w", wraplength=600, text_color=COLOR_WARNING
        )
        self.cancel_label.grid(row=2, column=2, columnspan=2, sticky="w", padx=12)

        ctk.CTkLabel(detail, text="Incidente").grid(row=3, column=0, sticky="e", padx=(12, 6))
        self.incident_combo = ctk.CTkComboBox(
            detail, values=[INCIDENT_PLACEHOLDER], width=330, state="readonly"
        )
        self.incident_combo.set(INCIDENT_PLACEHOLDER)
        self.incident_combo.grid(row=3, column=1, sticky="w", pady=2)
        ctk.CTkLabel(detail, text="Observaciones").grid(
            row=3, column=2, sticky="ne", padx=(12, 6), pady=2
        )
        self.observations_text = ctk.CTkTextbox(detail, width=300, height=48)
        self.observations_text.grid(row=3, column=3, sticky="w", padx=(0, 12), pady=2)
        self.hint_label = ctk.CTkLabel(detail, text="", text_color=COLOR_WARNING)
        self.hint_label.grid(row=4, column=0, columnspan=2, sticky="w", padx=12)
        self.unit_times_label = ctk.CTkLabel(detail, text="", anchor="w")
        self.unit_times_label.grid(row=4, column=2, columnspan=2, sticky="w", padx=12)

    def _build_units(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="x", padx=16, pady=2)

        left = ctk.CTkFrame(container)
        left.pack(side="left", fill="both", padx=(0, 6))
        ctk.CTkLabel(
            left, text="Unidades del despacho", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=8, pady=(4, 0))
        self.units_tree = self._make_tree(left, DRAFT_UNIT_COLUMNS, 4)
        self.units_tree.pack(padx=8, pady=(2, 2))
        self.units_tree.bind("<<TreeviewSelect>>", lambda event: self._update_controls())
        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.pack(anchor="w", padx=8, pady=(0, 6))
        for attribute, _, label, _, _ in UNIT_ACTIONS.values():
            button = ctk.CTkButton(
                actions, text=label, width=118, state="disabled",
                command=lambda name=attribute: self.run_unit_action(name),
            )
            button.pack(side="left", padx=(0, 4))
            setattr(self, attribute, button)

        right = ctk.CTkFrame(container)
        right.pack(side="left", fill="both")
        ctk.CTkLabel(
            right, text="Unidades disponibles", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=8, pady=(4, 0))
        bar = ctk.CTkFrame(right, fg_color="transparent")
        bar.pack(fill="x", padx=8)
        self.unit_search_entry = ctk.CTkEntry(
            bar, width=90, placeholder_text="Código/nombre"
        )
        self.unit_search_entry.pack(side="left")
        self.unit_search_entry.bind("<Return>", lambda event: self.refresh_available())
        self.unit_type_filter = self._combo(
            bar, ALL_UNIT_TYPES, 100, lambda value: self.refresh_available()
        )
        self.unit_type_filter.pack(side="left", padx=4)
        self.unit_station_filter = self._combo(
            bar, ALL_STATIONS, 110, lambda value: self.refresh_available()
        )
        self.unit_station_filter.pack(side="left")
        self.search_units_button = ctk.CTkButton(
            bar, text="Buscar", width=55, command=self.refresh_available
        )
        self.search_units_button.pack(side="left", padx=4)
        self.clear_units_button = ctk.CTkButton(
            bar, text="Limpiar filtros", width=90, fg_color="gray40",
            hover_color="gray30", command=self.clear_unit_filters,
        )
        self.clear_units_button.pack(side="left")
        self.available_tree = self._make_tree(right, AVAILABLE_COLUMNS, 4)
        self.available_tree.pack(padx=8, pady=(2, 8))

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=1000, justify="left")
        self.message_label.pack(fill="x", padx=18)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(2, 8))
        self.create_button = ctk.CTkButton(
            bar, text="Nuevo borrador", width=105, command=self.create
        )
        self.save_button = ctk.CTkButton(
            bar, text="Guardar cambios", width=110, command=self.save
        )
        self.add_button = ctk.CTkButton(
            bar, text="Agregar unidad", width=105, command=self.add_unit
        )
        self.remove_button = ctk.CTkButton(
            bar, text="Quitar unidad", width=95, command=self.remove_unit
        )
        self.cancel_button = ctk.CTkButton(
            bar, text="Cancelar borrador", width=120, fg_color="#b45309",
            hover_color="#92400e", command=self.cancel_draft,
        )
        self.cancel_dispatch_button = ctk.CTkButton(
            bar, text="Cancelar despacho", width=120, fg_color="#b45309",
            hover_color="#92400e", command=self.cancel_active,
        )
        self.activate_button = ctk.CTkButton(
            bar, text="Activar despacho", width=115, fg_color="#2e7d32",
            hover_color="#1b5e20", command=self.activate,
        )
        self.refresh_button = ctk.CTkButton(
            bar, text="Refrescar", width=75, fg_color="gray40", hover_color="gray30",
            command=self.refresh_all,
        )
        self.clear_button = ctk.CTkButton(
            bar, text="Limpiar selección", width=115, fg_color="gray40",
            hover_color="gray30", command=self.clear_form,
        )
        self.back_button = ctk.CTkButton(
            bar, text="Regresar", width=70, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        for button in (
            self.create_button, self.save_button, self.add_button, self.remove_button,
            self.cancel_button, self.cancel_dispatch_button, self.activate_button, self.refresh_button, self.clear_button, self.back_button,
        ):
            button.pack(side="left", padx=3)

    def confirm(self, message):
        """Pide confirmación; se puede reemplazar en pruebas."""
        return messagebox.askyesno("Confirmar", message, parent=self)

    def ask_reason(self, prompt):
        """Pide el motivo de cancelación; devuelve None si se cierra el diálogo."""
        try:
            dialog = ctk.CTkInputDialog(text=prompt, title="Cancelar")
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
        except DispatchNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._reset_form()
            self.refresh_list()
        except DispatchIncidentNotFoundError as error:
            self.show_message(str(error), error=True)
            self.load_incident_options()
        except (
            DispatchStateError, UnitNotEligibleError, DuplicateDraftUnitError,
            DraftUnitNotFoundError, UnitNotFoundError, DispatchHasNoUnitsError,
            IncidentTerminalError,
        ) as error:
            self.show_message(str(error), error=True)
            # Los datos en pantalla pudieron quedar desactualizados
            self._reload_after_conflict()
        except DispatchValidationError as error:
            self.show_message(str(error), error=True)
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED_WRITE if write else MSG_DENIED_READ, error=True)
        except Exception:
            logger.exception("Error en el módulo de despachos")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def _reload_after_conflict(self):
        if self._refreshing or self.selected_id is None:
            return
        try:
            dispatch = dispatch_controller.get_dispatch(self.selected_id)
        except Exception:
            return
        self._show_dispatch(dispatch, keep_text=True)
        self.refresh_list()
        self.refresh_available()

    def load_incident_options(self):
        """Relee los incidentes que admiten un nuevo borrador."""
        ok, incidents = self._run(dispatch_controller.list_selectable_incidents)
        if not ok:
            return
        current = self.incident_combo.get()
        self._incident_options = {_incident_text(i): i["incident_id"] for i in incidents}
        values = list(self._incident_options) or [INCIDENT_PLACEHOLDER]
        self.incident_combo.configure(values=values)
        if self.selected_id is None:
            self.incident_combo.set(current if current in values else INCIDENT_PLACEHOLDER)

    def incident_form_id(self):
        return self._incident_options.get(self.incident_combo.get())

    def incident_filter_id(self):
        return self._filter_incidents.get(self.incident_filter.get())

    def refresh_list(self, select_id=None):
        """Recarga la lista de despachos con los filtros actuales."""
        incident_id = self.incident_filter_id()
        drafts_only = self.show_filter.get() == SHOW_DRAFTS
        ok, every = self._run(dispatch_controller.list_dispatches)
        if not ok:
            return
        if incident_id is None and not drafts_only:
            rows = every
        else:
            if drafts_only:
                ok, rows = self._run(dispatch_controller.list_draft_dispatches, incident_id)
            else:
                ok, rows = self._run(dispatch_controller.list_dispatches, incident_id)
            if not ok:
                return
        current = self.incident_filter.get()
        self._filter_incidents = {}
        for dispatch in every:
            self._filter_incidents.setdefault(dispatch["incident_code"], dispatch["incident_id"])
        values = [ALL_INCIDENTS] + list(self._filter_incidents)
        self.incident_filter.configure(values=values)
        self.incident_filter.set(current if current in values else ALL_INCIDENTS)
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for dispatch in rows:
                self.tree.insert("", "end", iid=str(dispatch["dispatch_id"]), values=(
                    dispatch["dispatch_id"],
                    dispatch["incident_code"],
                    dispatch["state_name"],
                    dispatch["unit_count"],
                    dispatch["created_by_username"],
                    _shorten(dispatch["observations"]),
                ))
            target = str(select_id if select_id is not None else self.selected_id)
            if self.tree.exists(target):
                self.tree.selection_set(target)
                self.tree.see(target)
        finally:
            self._refreshing = False

    def clear_filters(self):
        self.show_filter.set(SHOW_ALL)
        self.incident_filter.set(ALL_INCIDENTS)
        self.refresh_list()

    def refresh_available(self):
        """Recarga las unidades disponibles para el borrador seleccionado."""
        dispatch_id = self.selected_id
        ok, every = self._run(dispatch_controller.list_available_units, dispatch_id)
        if not ok:
            return
        self._type_options = self._merge_options(
            self._type_options, self.unit_type_filter.get(), ALL_UNIT_TYPES,
            {u["type_name"]: u["type_id"] for u in every},
        )
        self._station_options = self._merge_options(
            self._station_options, self.unit_station_filter.get(), ALL_STATIONS,
            {_station_text(u): u["station_id"] for u in every},
        )
        self.unit_type_filter.configure(values=[ALL_UNIT_TYPES] + list(self._type_options))
        self.unit_station_filter.configure(values=[ALL_STATIONS] + list(self._station_options))
        type_id = self._type_options.get(self.unit_type_filter.get())
        station_id = self._station_options.get(self.unit_station_filter.get())
        text = self.unit_search_entry.get().strip()
        if type_id is None and station_id is None and not text:
            units = every
        else:
            ok, units = self._run(
                dispatch_controller.list_available_units, dispatch_id, type_id, station_id, text
            )
            if not ok:
                return
        selected = self.available_tree.selection()
        self.available_tree.delete(*self.available_tree.get_children())
        for unit in units:
            self.available_tree.insert("", "end", iid=str(unit["unit_id"]), values=(
                unit["unit_code"], unit["unit_name"] or "", unit["type_name"],
                unit["station_name"], unit["state_name"],
            ))
        if selected and self.available_tree.exists(selected[0]):
            self.available_tree.selection_set(selected[0])

    @staticmethod
    def _merge_options(old, current_text, default, fresh):
        """Conserva la opción elegida aunque ya no haya unidades de ese tipo o estación."""
        if current_text != default and current_text not in fresh and current_text in old:
            fresh[current_text] = old[current_text]
        return fresh

    def clear_unit_filters(self):
        self.unit_search_entry.delete(0, "end")
        self.unit_type_filter.set(ALL_UNIT_TYPES)
        self.unit_station_filter.set(ALL_STATIONS)
        self.refresh_available()

    def refresh_all(self):
        """Relee todo desde la base conservando selección, filtros y texto sin guardar."""
        self.message_label.configure(text="")
        self.load_incident_options()
        self.refresh_list()
        if self.selected_id is not None:
            ok, dispatch = self._run(dispatch_controller.get_dispatch, self.selected_id)
            if ok:
                self._show_dispatch(dispatch, keep_text=True)
        self.refresh_available()
        if not self.message_label.cget("text"):
            self.show_message("Datos actualizados.")

    def _on_select(self, event=None):
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection:
            return
        # Evita recargar (y perder lo escrito) cuando la fila ya está cargada
        if self.selected_id is not None and int(selection[0]) == self.selected_id:
            return
        ok, dispatch = self._run(dispatch_controller.get_dispatch, int(selection[0]))
        if ok:
            self._show_dispatch(dispatch)
            self.refresh_available()

    def _observations_value(self):
        return self.observations_text.get("1.0", "end-1c")

    def _set_observations(self, text):
        self.observations_text.configure(state="normal")
        self.observations_text.delete("1.0", "end")
        self.observations_text.insert("1.0", text)

    def _show_dispatch(self, dispatch, keep_text=False):
        """Muestra el detalle recién leído; keep_text conserva lo escrito sin guardar."""
        same = dispatch["dispatch_id"] == self.selected_id
        dirty = same and self._observations_value() != self._loaded_observations
        self.selected_id = dispatch["dispatch_id"]
        self.selected_state_code = dispatch["state_code"]
        self._loaded_observations = dispatch["observations"] or ""
        if not (keep_text and dirty):
            self._set_observations(self._loaded_observations)
        self.form_title.configure(text=f"Despacho N.º {dispatch['dispatch_id']}")
        address = dispatch["incident_address"] or NO_ADDRESS
        self.incident_label.configure(
            text=f"Incidente: {dispatch['incident_code']} — {address}"
        )
        self.incident_state_label.configure(
            text=f"Estado del incidente: {dispatch['incident_state_name']}"
        )
        self.state_label.configure(text=f"Estado del despacho: {dispatch['state_name']}")
        self.created_label.configure(
            text=f"Creado por: {dispatch['created_by_username']} el "
                 f"{_format_date(dispatch['created_at'])}"
        )
        reason = dispatch["cancel_reason"]
        self.cancel_label.configure(
            text=f"Motivo de cancelación: {reason}" if dispatch["state_code"] == CANCELLED_STATE
            and reason else ""
        )
        previous_unit = self.units_tree.selection()
        self.units_tree.delete(*self.units_tree.get_children())
        self._units = {unit["unit_id"]: unit for unit in dispatch["units"]}
        for unit in dispatch["units"]:
            mark = AVAILABLE_MARK if unit["eligible_now"] else UNAVAILABLE_MARK
            self.units_tree.insert("", "end", iid=str(unit["unit_id"]), values=(
                unit["unit_code"], unit["unit_name"] or "", unit["type_name"],
                unit["station_name"], unit["state_name"], mark,
            ))
        if same and previous_unit and self.units_tree.exists(previous_unit[0]):
            self.units_tree.selection_set(previous_unit[0])
        self.selected_state_code = dispatch["state_code"]
        self._update_controls()

    def _reset_form(self):
        self.selected_id = None
        self.selected_state_code = None
        self._loaded_observations = ""
        self._set_observations("")
        self.incident_combo.set(INCIDENT_PLACEHOLDER)
        self._refreshing = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._refreshing = False
        self.units_tree.delete(*self.units_tree.get_children())
        self._units = {}
        self.form_title.configure(text="Nuevo borrador")
        self.incident_label.configure(text="")
        self.incident_state_label.configure(text="")
        self.state_label.configure(text="Estado del despacho: Creado")
        self.created_label.configure(text="")
        self.cancel_label.configure(text="")
        self._update_controls()
        self.refresh_available()

    def clear_form(self):
        self._reset_form()
        self.message_label.configure(text="")

    def _is_draft(self):
        return self.selected_id is not None and self.selected_state_code == DRAFT_STATE

    def _update_controls(self):
        selected = self.selected_id is not None
        draft = self._is_draft()
        editable = self.can_write and (draft or not selected)
        self.observations_text.configure(state="normal" if editable else "disabled")
        self.incident_combo.configure(
            state="readonly" if self.can_write and not selected else "disabled"
        )
        enabled = {
            self.create_button: self.can_write and not selected,
            self.save_button: self.can_write and draft,
            self.add_button: self.can_write and draft,
            self.remove_button: self.can_write and draft,
            self.cancel_button: self.can_write and draft,
            self.activate_button: (
                self.can_write and draft and bool(self.units_tree.get_children())
            ),
        }
        enabled[self.cancel_dispatch_button] = (
            self.can_write and selected and self.selected_state_code == ACTIVE_STATE
        )
        for button, on in enabled.items():
            button.configure(state="normal" if on else "disabled")
        self.hint_label.configure(text=MSG_NOT_DRAFT if selected and not draft else "")
        self._update_unit_actions()

    def _selected_unit(self):
        selection = self.units_tree.selection()
        if not selection:
            return None
        return self._units.get(int(selection[0]))

    def _update_unit_actions(self):
        """Habilita solo la acción válida para el estado real de la unidad elegida."""
        unit = self._selected_unit()
        for attribute, _, _, _, _ in UNIT_ACTIONS.values():
            getattr(self, attribute).configure(state="disabled")
        if unit is None:
            self.unit_times_label.configure(text="")
            return
        self.unit_times_label.configure(
            text=f"Salida: {_format_date(unit.get('departed_at'))}  "
                 f"Llegada: {_format_date(unit.get('arrived_at'))}  "
                 f"Liberación: {_format_date(unit.get('released_at'))}"
        )
        action = UNIT_ACTIONS.get(unit["state_code"])
        # En un despacho cancelado solo se libera una participación que sí fue asignada
        cancelled_draft = (
            self.selected_state_code == CANCELLED_STATE and unit.get("assigned_at") is None
        )
        if (
            action and self.can_write and not cancelled_draft
            and self.selected_state_code in action[4]
        ):
            getattr(self, action[0]).configure(state="normal")

    def create(self):
        if self.selected_id is not None:
            return
        data = {
            "incident_id": self.incident_form_id(),
            "observations": self._observations_value(),
        }
        ok, dispatch = self._run(dispatch_controller.create_dispatch_draft, data, write=True)
        if not ok:
            return
        # El incidente nuevo debe verse en la lista aunque haya un filtro distinto
        if self.incident_filter_id() not in (None, dispatch["incident_id"]):
            self.incident_filter.set(ALL_INCIDENTS)
        self._show_dispatch(dispatch)
        self.load_incident_options()
        self.refresh_list(select_id=dispatch["dispatch_id"])
        self.refresh_available()
        self.show_message(f"Borrador N.º {dispatch['dispatch_id']} creado correctamente.")

    def save(self):
        if not self._is_draft():
            return
        data = {"observations": self._observations_value()}
        ok, dispatch = self._run(
            dispatch_controller.update_dispatch_draft, self.selected_id, data, write=True
        )
        if ok:
            self._show_dispatch(dispatch)
            self.refresh_list()
            self.show_message("Observaciones guardadas correctamente.")

    def add_unit(self):
        if not self._is_draft():
            return
        selection = self.available_tree.selection()
        if not selection:
            self.show_message(MSG_SELECT_AVAILABLE, error=True)
            return
        ok, _ = self._run(
            dispatch_controller.add_unit_to_draft, self.selected_id, int(selection[0]),
            write=True,
        )
        if ok:
            self._after_units_change("Unidad agregada al borrador.")

    def remove_unit(self):
        if not self._is_draft():
            return
        selection = self.units_tree.selection()
        if not selection:
            self.show_message(MSG_SELECT_DRAFT_UNIT, error=True)
            return
        code = self.units_tree.item(selection[0], "values")[0]
        message = (
            f"¿Quitar la unidad {code} del borrador? La unidad no se modifica."
        )
        if not self.confirm(message):
            return
        ok, _ = self._run(
            dispatch_controller.remove_unit_from_draft, self.selected_id, int(selection[0]),
            write=True,
        )
        if ok:
            self._after_units_change("Unidad quitada del borrador.")

    def _after_units_change(self, message):
        ok, dispatch = self._run(dispatch_controller.get_dispatch, self.selected_id)
        if ok:
            self._show_dispatch(dispatch, keep_text=True)
            self.refresh_list()
            self.refresh_available()
            self.show_message(message)

    def cancel_draft(self):
        if not self._is_draft():
            return
        number = self.selected_id
        prompt = (
            f"Motivo de la cancelación del borrador N.º {number}.\n"
            "El incidente y las unidades no se modifican."
        )
        reason = self.ask_reason(prompt)
        if reason is None or not reason.strip():
            self.show_message(MSG_REASON_REQUIRED, error=True)
            return
        message = (
            f"¿Cancelar el borrador N.º {number}? El incidente y las unidades no se "
            "modifican, pero el borrador no podrá reabrirse."
        )
        if not self.confirm(message):
            return
        ok, dispatch = self._run(
            dispatch_controller.cancel_dispatch_draft, number, reason, write=True
        )
        if ok:
            self._show_dispatch(dispatch)
            self.refresh_list()
            self.refresh_available()
            self.show_message(f"Borrador N.º {number} cancelado correctamente.")
            # Si el filtro ya no incluye el borrador, se vuelve al modo nuevo
            if not self.tree.exists(str(number)):
                self._reset_form()

    def activate(self):
        if not self._is_draft() or not self.units_tree.get_children():
            return
        number = self.selected_id
        message = (
            f"¿Activar el despacho N.º {number}? Las unidades pasarán a Despachada y, "
            "si el incidente está Reportado, pasará a Asignado. Esta acción no se "
            "puede deshacer desde esta pantalla."
        )
        if not self.confirm(message):
            return
        ok, dispatch = self._run(dispatch_controller.activate_dispatch, number, write=True)
        if ok:
            self._show_dispatch(dispatch)
            self.refresh_list()
            self.refresh_available()
            self.show_message(f"Despacho N.º {number} activado correctamente.")

    def run_unit_action(self, attribute):
        """Ejecuta la acción operativa de la unidad seleccionada tras confirmar."""
        unit = self._selected_unit()
        if self.selected_id is None or unit is None:
            self.show_message(MSG_SELECT_DRAFT_UNIT, error=True)
            return
        _, function_name, label, done, _ = next(
            a for a in UNIT_ACTIONS.values() if a[0] == attribute
        )
        code = unit["unit_code"]
        if not self.confirm(f"¿{label} para la unidad {code}?"):
            return
        function = getattr(dispatch_controller, function_name)
        ok, dispatch = self._run(function, self.selected_id, unit["unit_id"], write=True)
        if ok:
            self._show_dispatch(dispatch, keep_text=True)
            self.refresh_list()
            self.refresh_available()
            self.show_message(f"La unidad {code} quedó {done}.")

    def cancel_active(self):
        """Cancela un despacho ACTIVO con motivo obligatorio y confirmación."""
        if self.selected_id is None or self.selected_state_code != ACTIVE_STATE:
            return
        number = self.selected_id
        prompt = (
            f"Motivo de la cancelación del despacho N.º {number}.\n"
            "Las unidades se liberan y el incidente conserva su estado."
        )
        reason = self.ask_reason(prompt)
        if reason is None or reason == "":
            self.show_message("No se canceló el despacho: el motivo es obligatorio.", error=True)
            return
        message = (
            f"¿Cancelar el despacho N.º {number}? Las unidades se liberan: las Despachada "
            "vuelven a Disponible y las En ruta o En escena pasan a Regresando. El "
            "incidente conserva su estado y esta acción no se puede deshacer."
        )
        if not self.confirm(message):
            return
        ok, dispatch = self._run(
            dispatch_controller.cancel_active_dispatch, number, reason, write=True
        )
        if ok:
            self._show_dispatch(dispatch, keep_text=True)
            self.refresh_list()
            self.refresh_available()
            self.show_message(f"Despacho N.º {number} cancelado correctamente.")

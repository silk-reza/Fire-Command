import logging
from tkinter import messagebox, ttk

import customtkinter as ctk

from controllers import access_controller, unit_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from utils.permissions import PERMISSION_MANAGE_RESOURCES
from controllers.unit_controller import (
    DuplicateUnitCodeError,
    DuplicateUnitPlateError,
    UnitBusyError,
    UnitNotFoundError,
    UnitValidationError,
)

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para administrar unidades."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "La unidad ya no existe."
MSG_REACTIVATED = (
    "Unidad reactivada. Permanece Fuera de servicio hasta que se restablezca su disponibilidad."
)
ALL_STATIONS = "Todas las estaciones"
ALL_TYPES = "Todos los tipos"
ALL_STATES = "Todos los estados"
STATION_PLACEHOLDER = "Seleccione una estación"
TYPE_PLACEHOLDER = "Seleccione un tipo"
CAPACITY_HINT = "Entero mayor que 0; vacío = sin dato"
INITIAL_STATE_TEXT = "Estado inicial: Disponible"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"

COLUMNS = (
    ("code", "Código", 90, 90),
    ("plate", "Placa", 100, 100),
    ("name", "Nombre", 180, 180),
    ("station", "Estación", 240, 240),
    ("type", "Tipo", 150, 150),
    ("state", "Estado operativo", 140, 140),
    ("active", "Activo", 60, 60),
)


def _station_text(station):
    """Texto legible de una estación para los selectores."""
    text = f"{station['code']} — {station['name']}"
    return text if station["active"] else f"{text} (inactiva)"


def _type_text(unit_type):
    """Texto legible de un tipo de unidad para los selectores."""
    text = unit_type["name"]
    return text if unit_type["active"] else f"{text} (inactiva)"


class UnitView(ctk.CTkToplevel):
    """Módulo de unidades como ventana secundaria modal de la principal."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Unidades")
        self.resizable(False, False)
        self.selected_id = None
        self.selected_active = None
        self.selected_state_code = None
        self._refreshing = False
        # La escritura la decide el permiso; el controlador vuelve a validarlo
        self.can_write = access_controller.can(PERMISSION_MANAGE_RESOURCES)
        # Solo presentación: texto del selector -> id
        self._station_ids = {}
        self._station_texts = {}
        self._stations = []
        self._type_ids = {}
        self._type_texts = {}
        self._types = []
        self._state_ids = {}
        self._state_texts = {}
        # Estación y tipo actuales de la unidad en edición; pueden estar inactivos
        self.selected_station_id = None
        self.selected_type_id = None

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
        self._update_buttons()

    def _build_search(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(bar, text="Buscar").pack(side="left", padx=(0, 8))
        self.search_entry = ctk.CTkEntry(bar, width=190, placeholder_text="Código, placa o nombre")
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

        self.station_filter = combo("Estación", ALL_STATIONS, 210)
        self.type_filter = combo("Tipo", ALL_TYPES, 150)
        self.state_filter = combo("Estado operativo", ALL_STATES, 150)
        self.clear_search_button = ctk.CTkButton(
            bar, text="Quitar filtros", width=100, fg_color="gray40", hover_color="gray30",
            command=self.clear_search,
        )
        self.clear_search_button.pack(side="left", padx=6)

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Unit.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=26, borderwidth=0,
        )
        style.configure("Unit.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Unit.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=6)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=10, style="Unit.Treeview",
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
        form.pack(fill="x", padx=16, pady=6)
        self.form_title = ctk.CTkLabel(
            form, text="Nueva unidad", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.form_title.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        self.code_entry = self._field(form, "Código", 1, 0)
        self.name_entry = self._field(form, "Nombre (opcional)", 1, 2)
        self.plate_entry = self._field(form, "Placa (opcional)", 2, 0)
        self.capacity_entry = self._field(form, "Capacidad de personal (opcional)", 2, 2)
        ctk.CTkLabel(form, text=CAPACITY_HINT, text_color="gray60").grid(
            row=3, column=3, sticky="w", padx=(0, 12)
        )
        self.description_entry = self._field(form, "Descripción (opcional)", 3, 0)

        ctk.CTkLabel(form, text="Estación").grid(row=4, column=0, sticky="e", padx=(12, 6), pady=5)
        self.station_combo = ctk.CTkComboBox(
            form, values=[STATION_PLACEHOLDER], width=250, state="readonly"
        )
        self.station_combo.set(STATION_PLACEHOLDER)
        self.station_combo.grid(row=4, column=1, sticky="w", padx=(0, 12), pady=5)

        ctk.CTkLabel(form, text="Tipo").grid(row=4, column=2, sticky="e", padx=(12, 6), pady=5)
        self.type_combo = ctk.CTkComboBox(
            form, values=[TYPE_PLACEHOLDER], width=210, state="readonly"
        )
        self.type_combo.set(TYPE_PLACEHOLDER)
        self.type_combo.grid(row=4, column=3, sticky="w", padx=(0, 12), pady=5)

        # El estado operativo solo se muestra; cambia por acciones de negocio posteriores
        self.state_label = ctk.CTkLabel(form, text=INITIAL_STATE_TEXT)
        self.state_label.grid(row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(5, 10))

    def _field(self, form, label, row, column):
        ctk.CTkLabel(form, text=label).grid(row=row, column=column, sticky="e", padx=(12, 6), pady=5)
        entry = ctk.CTkEntry(form, width=210)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=5)
        return entry

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=900, justify="left")
        self.message_label.pack(fill="x", padx=18, pady=(4, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(8, 16))
        self.create_button = ctk.CTkButton(bar, text="Guardar nuevo", width=120, command=self.create)
        self.save_button = ctk.CTkButton(bar, text="Guardar cambios", width=120, command=self.save)
        self.activate_button = ctk.CTkButton(bar, text="Activar", width=90, command=self.activate)
        self.deactivate_button = ctk.CTkButton(
            bar, text="Desactivar", width=90, fg_color="#b45309", hover_color="#92400e",
            command=self.deactivate,
        )
        self.maintenance_button = ctk.CTkButton(
            bar, text="Enviar a mantenimiento", width=160, command=self.send_to_maintenance,
        )
        self.service_button = ctk.CTkButton(
            bar, text="Volver a servicio", width=130, command=self.return_to_service,
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
            self.create_button, self.save_button, self.activate_button,
            self.deactivate_button, self.maintenance_button, self.service_button,
            self.clear_button, self.back_button,
        ):
            button.pack(side="left", padx=4)

    def confirm(self, message):
        """Pide confirmación; se puede reemplazar en pruebas."""
        return messagebox.askyesno("Confirmar", message, parent=self)

    def notify(self, message):
        """Aviso informativo; se puede reemplazar en pruebas."""
        messagebox.showinfo("FireCommand", message, parent=self)

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
        except (
            UnitValidationError, DuplicateUnitCodeError, DuplicateUnitPlateError, UnitBusyError,
        ) as error:
            self.show_message(str(error), error=True)
        except UnitNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._reset_form()
            self.refresh_list()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en el módulo de unidades")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def load_options(self):
        """Relee estaciones, tipos y estados conservando las elecciones actuales."""
        ok_s, stations = self._run(unit_controller.list_stations_for_units)
        if not ok_s:
            return
        ok_t, types = self._run(unit_controller.list_unit_types)
        if not ok_t:
            return
        ok_e, states = self._run(unit_controller.list_unit_states)
        if not ok_e:
            return
        filter_ids = (self.station_filter_id(), self.type_filter_id(), self.state_filter_id())
        form_ids = (self.station_form_id(), self.type_form_id())
        self._stations = stations
        self._station_ids = {_station_text(s): s["station_id"] for s in stations}
        self._station_texts = {s["station_id"]: _station_text(s) for s in stations}
        self._types = types
        self._type_ids = {_type_text(t): t["type_id"] for t in types}
        self._type_texts = {t["type_id"]: _type_text(t) for t in types}
        self._state_ids = {s["name"]: s["state_id"] for s in states}
        self._state_texts = {s["state_id"]: s["name"] for s in states}
        # Los filtros conservan todo el catálogo para alcanzar unidades de elementos inactivos
        self.station_filter.configure(values=[ALL_STATIONS] + list(self._station_ids))
        self.station_filter.set(self._station_texts.get(filter_ids[0], ALL_STATIONS))
        self.type_filter.configure(values=[ALL_TYPES] + list(self._type_ids))
        self.type_filter.set(self._type_texts.get(filter_ids[1], ALL_TYPES))
        self.state_filter.configure(values=[ALL_STATES] + list(self._state_ids))
        self.state_filter.set(self._state_texts.get(filter_ids[2], ALL_STATES))
        self._apply_form_options(form_ids[0], form_ids[1])

    def _apply_form_options(self, station_id=None, type_id=None):
        """Reconstruye las opciones del formulario según el modo (alta o edición)."""
        stations = [
            s for s in self._stations if s["active"] or s["station_id"] == self.selected_station_id
        ]
        texts = [_station_text(s) for s in stations]
        self.station_combo.configure(values=texts)
        text = self._station_texts.get(station_id)
        self.station_combo.set(text if text in texts else STATION_PLACEHOLDER)

        types = [t for t in self._types if t["active"] or t["type_id"] == self.selected_type_id]
        type_texts = [_type_text(t) for t in types]
        self.type_combo.configure(values=type_texts)
        text = self._type_texts.get(type_id)
        self.type_combo.set(text if text in type_texts else TYPE_PLACEHOLDER)

    def station_filter_id(self):
        return self._station_ids.get(self.station_filter.get())

    def type_filter_id(self):
        return self._type_ids.get(self.type_filter.get())

    def state_filter_id(self):
        return self._state_ids.get(self.state_filter.get()) if self._state_ids else None

    def station_form_id(self):
        return self._station_ids.get(self.station_combo.get())

    def type_form_id(self):
        return self._type_ids.get(self.type_combo.get())

    def refresh_list(self, select_id=None):
        """Recarga la tabla con el texto de búsqueda y los filtros actuales."""
        ok, units = self._run(
            unit_controller.filter_units,
            self.station_filter_id(), self.type_filter_id(), self.state_filter_id(),
            self.search_entry.get(),
        )
        if not ok:
            return
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for unit in units:
                self.tree.insert("", "end", iid=str(unit["unit_id"]), values=(
                    unit["code"],
                    unit["plate"] or "",
                    unit["name"] or "",
                    f"{unit['station_code']} — {unit['station_name']}",
                    unit["type_name"],
                    unit["state_name"],
                    "Sí" if unit["active"] else "No",
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
        self.station_filter.set(ALL_STATIONS)
        self.type_filter.set(ALL_TYPES)
        self.state_filter.set(ALL_STATES)
        self.refresh_list()

    def _on_select(self, event=None):
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection:
            return
        ok, unit = self._run(unit_controller.get_unit, int(selection[0]))
        if ok:
            self._load_unit(unit)

    def _load_unit(self, unit):
        # Se relee el catálogo por si cambió con la ventana abierta
        self.selected_station_id = unit["station_id"]
        self.selected_type_id = unit["type_id"]
        self.load_options()
        self._apply_form_options(unit["station_id"], unit["type_id"])
        self._fill_form(unit)
        self.selected_id = unit["unit_id"]
        self.selected_active = unit["active"]
        self.selected_state_code = unit["state_code"]
        self.form_title.configure(text=f"Editando: {unit['code']}")
        self.state_label.configure(text=f"Estado operativo: {unit['state_name']}")
        self._update_buttons()

    def _entries(self):
        return (
            self.code_entry, self.name_entry, self.plate_entry,
            self.capacity_entry, self.description_entry,
        )

    def _fill_form(self, unit):
        capacity = unit["capacity"]
        values = (
            unit["code"], unit["name"] or "", unit["plate"] or "",
            "" if capacity is None else str(capacity), unit["description"] or "",
        )
        for entry, value in zip(self._entries(), values):
            entry.delete(0, "end")
            entry.insert(0, value)

    def _reset_form(self):
        for entry in self._entries():
            entry.delete(0, "end")
        self.selected_station_id = None
        self.selected_type_id = None
        self._apply_form_options(None, None)
        self.selected_id = None
        self.selected_active = None
        self.selected_state_code = None
        self._refreshing = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._refreshing = False
        self.form_title.configure(text="Nueva unidad")
        self.state_label.configure(text=INITIAL_STATE_TEXT)
        self._update_buttons()

    def clear_form(self):
        self._reset_form()
        self.message_label.configure(text="")

    def _update_buttons(self):
        selected = self.selected_id is not None
        self.create_button.configure(state="disabled" if selected else "normal")
        self.save_button.configure(state="normal" if selected else "disabled")
        self.activate_button.configure(
            state="normal" if selected and not self.selected_active else "disabled"
        )
        self.deactivate_button.configure(
            state="normal" if selected and self.selected_active else "disabled"
        )
        # Presentación: el controlador valida de nuevo estado, actividad y estación
        station_active = any(
            s["active"] for s in self._stations if s["station_id"] == self.selected_station_id
        )
        usable = self.can_write and selected and bool(self.selected_active)
        self.maintenance_button.configure(
            state="normal" if usable and self.selected_state_code == "DISPONIBLE" else "disabled"
        )
        self.service_button.configure(
            state="normal" if usable and station_active and self.selected_state_code in (
                "MANTENIMIENTO", "FUERA_DE_SERVICIO") else "disabled"
        )

    def _form_data(self):
        # Nunca se envía estado operativo; la capacidad viaja como texto
        return {
            "code": self.code_entry.get(),
            "name": self.name_entry.get(),
            "plate": self.plate_entry.get(),
            "capacity": self.capacity_entry.get(),
            "description": self.description_entry.get(),
            "station_id": self.station_form_id(),
            "type_id": self.type_form_id(),
        }

    def create(self):
        if self.selected_id is not None:
            return
        ok, unit = self._run(unit_controller.create_unit, self._form_data())
        if ok:
            # Sin selección: el formulario sigue en modo alta
            self._reset_form()
            self.refresh_list()
            self.show_message(f"Unidad {unit['code']} creada correctamente.")

    def save(self):
        if self.selected_id is None:
            return
        ok, unit = self._run(unit_controller.update_unit, self.selected_id, self._form_data())
        if ok:
            self._load_unit(unit)
            self.refresh_list()
            self.show_message(f"Unidad {unit['code']} actualizada correctamente.")

    def activate(self):
        self._change_state(unit_controller.activate_unit, MSG_REACTIVATED)

    def deactivate(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        message = (
            f"¿Desactivar la unidad {code}? No se borrará, pero quedará inactiva "
            "y en estado Fuera de servicio."
        )
        if not self.confirm(message):
            return
        self._change_state(unit_controller.deactivate_unit, None)

    def _change_state(self, function, success_text):
        if self.selected_id is None:
            return
        ok, unit = self._run(function, self.selected_id)
        if ok:
            self._load_unit(unit)
            self.refresh_list()
            self.show_message(
                success_text or f"Unidad {unit['code']} desactivada correctamente. "
                "Quedó Fuera de servicio."
            )

    def send_to_maintenance(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        if not self.confirm(f"¿Enviar la unidad {code} a mantenimiento?"):
            return
        self._operate(
            unit_controller.send_unit_to_maintenance,
            "Unidad {code} enviada a mantenimiento.",
        )

    def return_to_service(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        message = (
            f"Esta es la confirmación explícita de que la unidad {code} es apta para el "
            "servicio (aptitud). ¿Volver a servicio? La unidad pasará a Disponible."
        )
        if not self.confirm(message):
            return
        self._operate(
            unit_controller.return_unit_to_service,
            "Unidad {code} de vuelta en servicio. Quedó Disponible.",
        )

    def _operate(self, function, success_text):
        ok, unit = self._run(function, self.selected_id)
        if ok:
            self._load_unit(unit)
            self.refresh_list()
            self.show_message(success_text.format(code=unit["code"]))
            return
        # Tras un rechazo se relee la unidad por si su estado cambió
        if self.selected_id is not None:
            try:
                self._load_unit(unit_controller.get_unit(self.selected_id))
            except Exception:
                pass
            self.refresh_list()

import logging
from tkinter import messagebox, ttk

import customtkinter as ctk

from controllers import personnel_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.personnel_controller import (
    DuplicatePersonCodeError,
    InvalidStationError,
    PersonNotFoundError,
    PersonnelValidationError,
)

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para administrar personal."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "La persona ya no existe."
ALL_STATIONS = "Todas las estaciones"
STATION_PLACEHOLDER = "Seleccione una estación"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"

COLUMNS = (
    ("code", "Código", 100, 100),
    ("first_name", "Nombres", 140, 140),
    ("last_name", "Apellidos", 140, 140),
    ("position", "Cargo", 170, 170),
    ("station", "Estación", 240, 240),
    ("status", "Estado", 80, 80),
)


def _station_text(station):
    """Texto legible de una estación para los selectores."""
    text = f"{station['code']} — {station['name']}"
    return text if station["active"] else f"{text} (inactiva)"


class PersonnelView(ctk.CTkToplevel):
    """Módulo de personal como ventana secundaria modal de la principal."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Personal")
        self.resizable(False, False)
        self.selected_id = None
        self.selected_active = None
        self._refreshing = False
        # Solo presentación: texto del selector -> id de estación
        self._station_ids = {}
        self._station_texts = {}
        self._stations = []
        # Estación actual de la persona en edición; puede estar inactiva
        self.selected_station_id = None

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

        self.load_stations()
        self.refresh_list()
        self._update_buttons()

    def _build_search(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(bar, text="Buscar").pack(side="left", padx=(0, 8))
        self.search_entry = ctk.CTkEntry(bar, width=220, placeholder_text="Código, nombre o cargo")
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda event: self.search())
        self.search_button = ctk.CTkButton(bar, text="Buscar", width=80, command=self.search)
        self.search_button.pack(side="left", padx=6)
        ctk.CTkLabel(bar, text="Estación").pack(side="left", padx=(10, 6))
        self.station_filter = ctk.CTkComboBox(
            bar, values=[ALL_STATIONS], width=250, state="readonly",
            command=lambda value: self.refresh_list(),
        )
        self.station_filter.set(ALL_STATIONS)
        self.station_filter.pack(side="left")
        self.clear_search_button = ctk.CTkButton(
            bar, text="Quitar filtro", width=100, fg_color="gray40", hover_color="gray30",
            command=self.clear_search,
        )
        self.clear_search_button.pack(side="left", padx=6)

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Personnel.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=26, borderwidth=0,
        )
        style.configure("Personnel.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Personnel.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=6)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=10, style="Personnel.Treeview",
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
            form, text="Nueva persona", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.form_title.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        self.code_entry = self._field(form, "Código", 1, 0)
        self.position_entry = self._field(form, "Cargo", 1, 2)
        self.first_name_entry = self._field(form, "Nombres", 2, 0)
        self.last_name_entry = self._field(form, "Apellidos", 2, 2)
        self.phone_entry = self._field(form, "Teléfono (opcional)", 3, 0)

        ctk.CTkLabel(form, text="Estación").grid(row=3, column=2, sticky="e", padx=(12, 6), pady=5)
        self.station_combo = ctk.CTkComboBox(
            form, values=[STATION_PLACEHOLDER], width=250, state="readonly"
        )
        self.station_combo.set(STATION_PLACEHOLDER)
        self.station_combo.grid(row=3, column=3, sticky="w", padx=(0, 12), pady=(5, 10))

    def _field(self, form, label, row, column):
        ctk.CTkLabel(form, text=label).grid(row=row, column=column, sticky="e", padx=(12, 6), pady=5)
        entry = ctk.CTkEntry(form, width=210)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=5)
        return entry

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=800, justify="left")
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
            self.deactivate_button, self.clear_button, self.back_button,
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
        except (PersonnelValidationError, InvalidStationError, DuplicatePersonCodeError) as error:
            self.show_message(str(error), error=True)
        except PersonNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._reset_form()
            self.refresh_list()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en el módulo de personal")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def load_stations(self):
        """Carga las estaciones en ambos selectores conservando la elección actual."""
        ok, stations = self._run(personnel_controller.list_stations_for_personnel)
        if not ok:
            return
        current_filter = self.station_filter_id()
        current_form = self.station_form_id()
        self._stations = stations
        self._station_ids = {_station_text(s): s["station_id"] for s in stations}
        self._station_texts = {s["station_id"]: _station_text(s) for s in stations}
        # El filtro conserva todas las estaciones para alcanzar al personal de las inactivas
        self.station_filter.configure(values=[ALL_STATIONS] + list(self._station_ids))
        self.station_filter.set(self._station_texts.get(current_filter, ALL_STATIONS))
        self._apply_form_options(current_form)

    def _apply_form_options(self, selected_id=None):
        """Reconstruye las opciones del formulario según el modo (alta o edición)."""
        allowed = [s for s in self._stations if s["active"] or s["station_id"] == self.selected_station_id]
        texts = [_station_text(s) for s in allowed]
        self.station_combo.configure(values=texts)
        text = self._station_texts.get(selected_id)
        self.station_combo.set(text if text in texts else STATION_PLACEHOLDER)

    def station_filter_id(self):
        return self._station_ids.get(self.station_filter.get())

    def station_form_id(self):
        return self._station_ids.get(self.station_combo.get())

    def refresh_list(self, select_id=None):
        """Recarga la tabla con el texto de búsqueda y la estación actuales."""
        ok, people = self._run(
            personnel_controller.find_personnel, self.search_entry.get(), self.station_filter_id()
        )
        if not ok:
            return
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for person in people:
                self.tree.insert("", "end", iid=str(person["person_id"]), values=(
                    person["code"],
                    person["first_name"],
                    person["last_name"],
                    person["position"],
                    f"{person['station_code']} — {person['station_name']}",
                    "Activo" if person["active"] else "Inactivo",
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
        self.refresh_list()

    def _on_select(self, event=None):
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection:
            return
        ok, person = self._run(personnel_controller.get_person, int(selection[0]))
        if ok:
            self._load_person(person)

    def _load_person(self, person):
        # Se relee el estado real de las estaciones por si cambió con la ventana abierta
        self.selected_station_id = person["station_id"]
        self.load_stations()
        self._apply_form_options(person["station_id"])
        self._fill_form(person)
        self.selected_id = person["person_id"]
        self.selected_active = person["active"]
        self.form_title.configure(text=f"Editando: {person['code']}")
        self._update_buttons()

    def _entries(self):
        return (
            self.code_entry, self.first_name_entry, self.last_name_entry,
            self.position_entry, self.phone_entry,
        )

    def _fill_form(self, person):
        values = (
            person["code"], person["first_name"], person["last_name"],
            person["position"], person["phone"] or "",
        )
        for entry, value in zip(self._entries(), values):
            entry.delete(0, "end")
            entry.insert(0, value)

    def _reset_form(self):
        for entry in self._entries():
            entry.delete(0, "end")
        self.selected_station_id = None
        self._apply_form_options(None)
        self.selected_id = None
        self.selected_active = None
        self._refreshing = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._refreshing = False
        self.form_title.configure(text="Nueva persona")
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

    def _form_data(self):
        return {
            "code": self.code_entry.get(),
            "first_name": self.first_name_entry.get(),
            "last_name": self.last_name_entry.get(),
            "position": self.position_entry.get(),
            "phone": self.phone_entry.get(),
            # Sin selección se envía None y el controlador rechaza la falta de estación
            "station_id": self.station_form_id(),
        }

    def create(self):
        if self.selected_id is not None:
            return
        ok, person = self._run(personnel_controller.create_person, self._form_data())
        if ok:
            # Sin selección: el formulario sigue en modo alta
            self._reset_form()
            self.refresh_list()
            self.show_message(f"Persona {person['code']} creada correctamente.")

    def save(self):
        if self.selected_id is None:
            return
        ok, person = self._run(
            personnel_controller.update_person, self.selected_id, self._form_data()
        )
        if ok:
            self._load_person(person)
            self.refresh_list()
            self.show_message(f"Persona {person['code']} actualizada correctamente.")

    def activate(self):
        self._change_state(personnel_controller.activate_person, "activada")

    def deactivate(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        if not self.confirm(f"¿Desactivar a la persona {code}? No se borrará, solo quedará inactiva."):
            return
        self._change_state(personnel_controller.deactivate_person, "desactivada")

    def _change_state(self, function, verb):
        if self.selected_id is None:
            return
        ok, person = self._run(function, self.selected_id)
        if ok:
            self._load_person(person)
            self.refresh_list()
            self.show_message(f"Persona {person['code']} {verb} correctamente.")

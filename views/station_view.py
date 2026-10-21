import logging
from tkinter import messagebox, ttk

import customtkinter as ctk

from controllers import station_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.station_controller import (
    DuplicateStationCodeError,
    StationNotFoundError,
    StationValidationError,
)

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para administrar estaciones."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "La estación ya no existe."
COORDINATES_HINT = "Latitud y longitud: ambas o ninguna"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"

COLUMNS = (
    ("code", "Código", 110, 110),
    ("name", "Nombre", 230, 230),
    ("address", "Dirección", 300, 300),
    ("status", "Estado", 80, 80),
)


def _coordinate_text(value):
    """Muestra la coordenada como decimal simple; vacío si no existe."""
    if value is None:
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


class StationView(ctk.CTkToplevel):
    """Módulo de estaciones como ventana secundaria modal de la principal.

    Mientras está abierto bloquea la ventana principal (grab_set), por lo que
    otro módulo no puede abrirse a la vez: primero hay que usar Regresar.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Estaciones")
        self.resizable(False, False)
        self.selected_id = None
        self.selected_active = None
        self._refreshing = False

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

        self.refresh_list()
        self._update_buttons()

    def _build_search(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(bar, text="Buscar").pack(side="left", padx=(0, 8))
        self.search_entry = ctk.CTkEntry(bar, width=280, placeholder_text="Código, nombre o dirección")
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda event: self.search())
        self.search_button = ctk.CTkButton(bar, text="Buscar", width=90, command=self.search)
        self.search_button.pack(side="left", padx=6)
        self.clear_search_button = ctk.CTkButton(
            bar, text="Quitar filtro", width=100, fg_color="gray40", hover_color="gray30",
            command=self.clear_search,
        )
        self.clear_search_button.pack(side="left")

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Stations.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=26, borderwidth=0,
        )
        style.configure("Stations.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Stations.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=6)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=8, style="Stations.Treeview",
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
            form, text="Nueva estación", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.form_title.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        self.code_entry = self._field(form, "Código", 1, 0)
        self.name_entry = self._field(form, "Nombre", 1, 2)
        self.address_entry = self._field(form, "Dirección", 2, 0)
        self.reference_entry = self._field(form, "Referencia (opcional)", 2, 2)
        self.phone_entry = self._field(form, "Teléfono (opcional)", 3, 0)
        self.latitude_entry = self._field(form, "Latitud (opcional)", 4, 0)
        self.longitude_entry = self._field(form, "Longitud (opcional)", 4, 2)
        ctk.CTkLabel(form, text=COORDINATES_HINT, text_color="gray60").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8)
        )

    def _field(self, form, label, row, column):
        ctk.CTkLabel(form, text=label).grid(row=row, column=column, sticky="e", padx=(12, 6), pady=5)
        entry = ctk.CTkEntry(form, width=210)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=5)
        return entry

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=700, justify="left")
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
        except (StationValidationError, DuplicateStationCodeError) as error:
            self.show_message(str(error), error=True)
        except StationNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._reset_form()
            self.refresh_list()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en el módulo de estaciones")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def refresh_list(self, select_id=None):
        """Recarga la tabla con el texto de búsqueda actual."""
        ok, stations = self._run(station_controller.search_stations, self.search_entry.get())
        if not ok:
            return
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for station in stations:
                self.tree.insert("", "end", iid=str(station["station_id"]), values=(
                    station["code"],
                    station["name"],
                    station["address"],
                    "Activa" if station["active"] else "Inactiva",
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
        self.refresh_list()

    def _on_select(self, event=None):
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection:
            return
        ok, station = self._run(station_controller.get_station, int(selection[0]))
        if ok:
            self._load_station(station)

    def _load_station(self, station):
        self._fill_form(station)
        self.selected_id = station["station_id"]
        self.selected_active = station["active"]
        self.form_title.configure(text=f"Editando: {station['code']}")
        self._update_buttons()

    def _entries(self):
        return (
            self.code_entry, self.name_entry, self.address_entry, self.reference_entry,
            self.phone_entry, self.latitude_entry, self.longitude_entry,
        )

    def _fill_form(self, station):
        values = (
            station["code"], station["name"], station["address"],
            station["reference"] or "", station["phone"] or "",
            _coordinate_text(station["latitude"]), _coordinate_text(station["longitude"]),
        )
        for entry, value in zip(self._entries(), values):
            entry.delete(0, "end")
            entry.insert(0, value)

    def _reset_form(self):
        for entry in self._entries():
            entry.delete(0, "end")
        self.selected_id = None
        self.selected_active = None
        self._refreshing = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._refreshing = False
        self.form_title.configure(text="Nueva estación")
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
            "name": self.name_entry.get(),
            "address": self.address_entry.get(),
            "reference": self.reference_entry.get(),
            "phone": self.phone_entry.get(),
            "latitude": self.latitude_entry.get(),
            "longitude": self.longitude_entry.get(),
        }

    def create(self):
        if self.selected_id is not None:
            return
        ok, station = self._run(station_controller.create_station, self._form_data())
        if ok:
            # Sin selección: el formulario sigue en modo alta y la fila se puede elegir después
            self._reset_form()
            self.refresh_list()
            self.show_message(f"Estación {station['code']} creada correctamente.")

    def save(self):
        if self.selected_id is None:
            return
        ok, station = self._run(
            station_controller.update_station, self.selected_id, self._form_data()
        )
        if ok:
            self._load_station(station)
            self.refresh_list()
            self.show_message(f"Estación {station['code']} actualizada correctamente.")

    def activate(self):
        self._change_state(station_controller.activate_station, "activada")

    def deactivate(self):
        if self.selected_id is None:
            return
        code = self.code_entry.get()
        if not self.confirm(f"¿Desactivar la estación {code}? No se borrará, solo quedará inactiva."):
            return
        self._change_state(station_controller.deactivate_station, "desactivada")

    def _change_state(self, function, verb):
        if self.selected_id is None:
            return
        ok, station = self._run(function, self.selected_id)
        if ok:
            self._load_station(station)
            self.refresh_list()
            self.show_message(f"Estación {station['code']} {verb} correctamente.")

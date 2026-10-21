import logging
from tkinter import messagebox, ttk

import customtkinter as ctk

from controllers import user_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.user_controller import (
    DuplicateUserError,
    UserNotFoundError,
    UserValidationError,
)

from utils import session

logger = logging.getLogger(__name__)

MSG_SESSION_ENDED = (
    "Su rol fue modificado. Por seguridad la sesión se cerró; inicie sesión nuevamente."
)
MSG_DENIED = "No tiene permiso para administrar usuarios."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_NOT_FOUND = "El usuario ya no existe."
PASSWORD_HINT_NEW = "Obligatoria"
PASSWORD_HINT_EDIT = "Dejar vacío para conservar la actual"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"

COLUMNS = (
    ("username", "Usuario", 150),
    ("name", "Nombre", 220),
    ("role", "Rol", 150),
    ("status", "Estado", 80),
)


class UserView(ctk.CTkToplevel):
    """Módulo de usuarios como ventana secundaria de la principal.

    Es un Toplevel para no crear una segunda raíz ni un segundo mainloop:
    el bucle de MainView sigue siendo el único activo.
    """

    def __init__(self, parent, on_session_ended=None):
        super().__init__(parent)
        self.on_session_ended = on_session_ended
        self.title("FireCommand - Usuarios")
        self.resizable(False, False)
        self.selected_id = None
        self.selected_active = None
        self._role_ids = {}
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

        self._load_roles()
        self.refresh_list()
        self._update_buttons()

    def _build_search(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(bar, text="Buscar").pack(side="left", padx=(0, 8))
        self.search_entry = ctk.CTkEntry(bar, width=280, placeholder_text="Usuario o nombre")
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
            "Users.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=26, borderwidth=0,
        )
        style.configure("Users.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Users.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=6)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=9, style="Users.Treeview",
        )
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_form(self):
        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=16, pady=6)
        self.form_title = ctk.CTkLabel(
            form, text="Nuevo usuario", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.form_title.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        self.username_entry = self._field(form, "Usuario", 1, 0)
        self.role_combo = self._field_combo(form, "Rol", 1, 2)
        self.first_name_entry = self._field(form, "Nombres", 2, 0)
        self.last_name_entry = self._field(form, "Apellidos", 2, 2)
        self.email_entry = self._field(form, "Correo (opcional)", 3, 0)
        ctk.CTkLabel(form, text="Contraseña").grid(row=3, column=2, sticky="e", padx=(12, 6), pady=5)
        self.password_entry = ctk.CTkEntry(form, width=210, show="*")
        self.password_entry.grid(row=3, column=3, sticky="w", padx=(0, 12), pady=5)
        self.password_hint = ctk.CTkLabel(form, text=PASSWORD_HINT_NEW, text_color="gray60")
        self.password_hint.grid(row=4, column=3, sticky="w", padx=(0, 12), pady=(0, 8))

    def _field(self, form, label, row, column):
        ctk.CTkLabel(form, text=label).grid(row=row, column=column, sticky="e", padx=(12, 6), pady=5)
        entry = ctk.CTkEntry(form, width=210)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=5)
        return entry

    def _field_combo(self, form, label, row, column):
        ctk.CTkLabel(form, text=label).grid(row=row, column=column, sticky="e", padx=(12, 6), pady=5)
        combo = ctk.CTkComboBox(form, width=210, state="readonly", values=[""])
        combo.grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=5)
        return combo

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=640, justify="left")
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

    def _end_session_flow(self):
        """El controlador cerró la sesión: avisa, cierra el módulo y pide volver al login."""
        callback = self.on_session_ended
        # La sesión ya se cerró: aunque el aviso falle, hay que cerrar y volver al login
        try:
            self.notify(MSG_SESSION_ENDED)
        finally:
            self.close()
            if callback is not None:
                callback()

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
        except (UserValidationError, DuplicateUserError) as error:
            self.show_message(str(error), error=True)
        except UserNotFoundError:
            self.show_message(MSG_NOT_FOUND, error=True)
            self._reset_form()
            self.refresh_list()
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en el módulo de usuarios")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def _load_roles(self):
        ok, roles = self._run(user_controller.list_roles)
        roles = roles if ok else []
        self._role_ids = {role["role_name"]: role["role_id"] for role in roles}
        names = list(self._role_ids) or [""]
        self.role_combo.configure(values=names)
        self.role_combo.set(names[0] if self._role_ids else "")

    def refresh_list(self, select_id=None):
        """Recarga la tabla con el texto de búsqueda actual."""
        ok, users = self._run(user_controller.search_users, self.search_entry.get())
        if not ok:
            return
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for user in users:
                self.tree.insert("", "end", iid=str(user["user_id"]), values=(
                    user["username"],
                    f"{user['first_name']} {user['last_name']}",
                    user["role_name"],
                    "Activo" if user["active"] else "Inactivo",
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
        ok, user = self._run(user_controller.get_user, int(selection[0]))
        if ok:
            self._load_user(user)

    def _load_user(self, user):
        self._fill_form(user)
        self.selected_id = user["user_id"]
        self.selected_active = user["active"]
        self.form_title.configure(text=f"Editando: {user['username']}")
        self.password_hint.configure(text=PASSWORD_HINT_EDIT)
        self._update_buttons()

    def _fill_form(self, user):
        for entry, value in (
            (self.username_entry, user["username"]),
            (self.first_name_entry, user["first_name"]),
            (self.last_name_entry, user["last_name"]),
            (self.email_entry, user["email"] or ""),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.password_entry.delete(0, "end")
        self.role_combo.set(user["role_name"])

    def _reset_form(self):
        for entry in (
            self.username_entry, self.first_name_entry, self.last_name_entry,
            self.email_entry, self.password_entry,
        ):
            entry.delete(0, "end")
        if self._role_ids:
            self.role_combo.set(next(iter(self._role_ids)))
        self.selected_id = None
        self.selected_active = None
        self._refreshing = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._refreshing = False
        self.form_title.configure(text="Nuevo usuario")
        self.password_hint.configure(text=PASSWORD_HINT_NEW)
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
        role_name = self.role_combo.get()
        return {
            "username": self.username_entry.get(),
            "first_name": self.first_name_entry.get(),
            "last_name": self.last_name_entry.get(),
            "email": self.email_entry.get(),
            "role_id": self._role_ids.get(role_name),
            "password": self.password_entry.get(),
        }

    def create(self):
        if self.selected_id is not None:
            return
        ok, user = self._run(user_controller.create_user, self._form_data())
        if ok:
            # Sin selección: el formulario sigue en modo alta y la fila se puede elegir después
            self._reset_form()
            self.refresh_list()
            self.show_message(f"Usuario {user['username']} creado correctamente.")

    def save(self):
        if self.selected_id is None:
            return
        ok, user = self._run(user_controller.update_user, self.selected_id, self._form_data())
        if ok:
            # Si el controlador cerró la sesión no se vuelve a consultar nada
            if not session.is_active():
                self._end_session_flow()
                return
            self._load_user(user)
            self.refresh_list()
            self.show_message(f"Usuario {user['username']} actualizado correctamente.")

    def activate(self):
        self._change_state(user_controller.activate_user, "activado")

    def deactivate(self):
        if self.selected_id is None:
            return
        username = self.username_entry.get()
        if not self.confirm(f"¿Desactivar al usuario {username}? No podrá iniciar sesión."):
            return
        self._change_state(user_controller.deactivate_user, "desactivado")

    def _change_state(self, function, verb):
        if self.selected_id is None:
            return
        ok, user = self._run(function, self.selected_id)
        if ok:
            self._load_user(user)
            self.refresh_list()
            self.show_message(f"Usuario {user['username']} {verb} correctamente.")

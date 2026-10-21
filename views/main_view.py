import customtkinter as ctk

from controllers import access_controller
from utils import permissions as perm
from utils import session
from views.station_view import StationView
from views.user_view import UserView

READ_ONLY_TEXT = "Modo solo lectura"
PENDING_NOTE = "disponible según su rol (módulo pendiente)"

# Solo presentación: etiqueta de cada permiso; la autorización la decide el controlador
ACCESS_ITEMS = (
    (perm.PERMISSION_MANAGE_USERS, "Administrar usuarios"),
    (perm.PERMISSION_MANAGE_CATALOGS, "Administrar catálogos"),
    (perm.PERMISSION_OPERATE_INCIDENTS, "Incidentes"),
    (perm.PERMISSION_OPERATE_DISPATCHES, "Despachos"),
    (perm.PERMISSION_MANAGE_RESOURCES, "Recursos"),
    (perm.PERMISSION_VIEW_OPERATIONAL_DATA, "Datos operativos (consulta)"),
    (perm.PERMISSION_VIEW_REPORTS, "Reportes"),
    (perm.PERMISSION_VIEW_AUDIT, "Bitácora"),
)


class MainView(ctk.CTk):
    """Ventana principal mínima. `logged_out` indica si se pidió volver al login."""

    def __init__(self, user):
        super().__init__()
        self.title("FireCommand")
        self.resizable(False, False)
        self.logged_out = False
        self.user_view = None
        self.station_view = None

        frame = ctk.CTkFrame(self)
        frame.pack(padx=30, pady=30)

        self.user_label = ctk.CTkLabel(
            frame,
            text=f"Sesión iniciada: {user['first_name']} {user['last_name']} ({user['username']})",
        )
        self.user_label.pack(padx=30, pady=(25, 6))
        self.role_label = ctk.CTkLabel(frame, text=f"Rol: {user['role_code']}")
        self.role_label.pack(padx=30, pady=6)

        # Solo se muestran los accesos que el controlador autoriza para la sesión
        self.read_only_label = None
        if access_controller.is_read_only():
            self.read_only_label = ctk.CTkLabel(frame, text=READ_ONLY_TEXT, text_color="#d97706")
            self.read_only_label.pack(padx=30, pady=6)

        ctk.CTkLabel(frame, text="Accesos disponibles").pack(padx=30, pady=(15, 4))
        self.access_widgets = {}
        for permission, text in ACCESS_ITEMS:
            if not access_controller.can(permission):
                continue
            if permission == perm.PERMISSION_MANAGE_USERS:
                # Módulo real: botón que abre la administración de usuarios
                widget = ctk.CTkButton(frame, text="Usuarios", command=self.open_users)
                widget.pack(padx=30, pady=4, anchor="w")
            elif permission == perm.PERMISSION_MANAGE_CATALOGS:
                # Por ahora el único catálogo implementado es el de estaciones
                widget = ctk.CTkButton(frame, text="Estaciones", command=self.open_stations)
                widget.pack(padx=30, pady=4, anchor="w")
            else:
                widget = ctk.CTkLabel(frame, text=f"{text}: {PENDING_NOTE}")
                widget.pack(padx=30, pady=2, anchor="w")
            self.access_widgets[permission] = widget
        self.granted_permissions = frozenset(self.access_widgets)

        self.logout_button = ctk.CTkButton(frame, text="Cerrar sesión", command=self.logout)
        self.logout_button.pack(padx=30, pady=(15, 25))

        # La X cierra la sesión y sale de la aplicación (no vuelve al login)
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_reqwidth()) // 2
        y = (self.winfo_screenheight() - self.winfo_reqheight()) // 3
        self.geometry(f"+{x}+{y}")

    def open_users(self):
        """Abre el módulo de usuarios; si ya está abierto, lo trae al frente."""
        if self.user_view is not None and self.user_view.winfo_exists():
            self.user_view.lift()
            return self.user_view
        self.user_view = UserView(self, on_session_ended=self.logout)
        return self.user_view

    def open_stations(self):
        """Abre el módulo de estaciones; si ya está abierto, lo trae al frente.

        Los módulos son modales: mientras uno está abierto la ventana principal
        queda bloqueada, así que hay que usar Regresar antes de abrir otro.
        """
        if self.station_view is not None and self.station_view.winfo_exists():
            self.station_view.lift()
            return self.station_view
        self.station_view = StationView(self)
        return self.station_view

    def logout(self):
        """Limpia la sesión y pide volver al login."""
        session.end_session()
        self.logged_out = True
        self.quit()

    def close_app(self):
        session.end_session()
        self.logged_out = False
        self.quit()

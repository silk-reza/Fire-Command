import customtkinter as ctk

from utils import session


class MainView(ctk.CTk):
    """Ventana principal mínima. `logged_out` indica si se pidió volver al login."""

    def __init__(self, user):
        super().__init__()
        self.title("FireCommand")
        self.resizable(False, False)
        self.logged_out = False

        frame = ctk.CTkFrame(self)
        frame.pack(padx=30, pady=30)

        self.user_label = ctk.CTkLabel(
            frame,
            text=f"Sesión iniciada: {user['first_name']} {user['last_name']} ({user['username']})",
        )
        self.user_label.pack(padx=30, pady=(25, 6))
        self.role_label = ctk.CTkLabel(frame, text=f"Rol: {user['role_code']}")
        self.role_label.pack(padx=30, pady=6)
        self.logout_button = ctk.CTkButton(frame, text="Cerrar sesión", command=self.logout)
        self.logout_button.pack(padx=30, pady=(15, 25))

        # La X cierra la sesión y sale de la aplicación (no vuelve al login)
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_reqwidth()) // 2
        y = (self.winfo_screenheight() - self.winfo_reqheight()) // 3
        self.geometry(f"+{x}+{y}")

    def logout(self):
        """Limpia la sesión y pide volver al login."""
        session.end_session()
        self.logged_out = True
        self.quit()

    def close_app(self):
        session.end_session()
        self.logged_out = False
        self.quit()

import logging

import customtkinter as ctk

from controllers import auth_controller
from controllers.auth_controller import AccessDenied
from utils import session

logger = logging.getLogger(__name__)

MSG_EMPTY = "Ingrese usuario y contraseña."
MSG_DENIED = "Usuario o contraseña no válidos."
MSG_INTERNAL = "No fue posible iniciar sesión por un problema interno. Intente de nuevo."


class LoginView(ctk.CTk):
    """Ventana de login. Tras mainloop(), `result` es el usuario autenticado o None."""

    def __init__(self):
        super().__init__()
        self.title("FireCommand - Iniciar sesión")
        self.resizable(False, False)
        self.result = None

        frame = ctk.CTkFrame(self)
        frame.pack(padx=30, pady=30)

        ctk.CTkLabel(frame, text="Iniciar sesión", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, columnspan=2, pady=(20, 15), padx=20
        )
        ctk.CTkLabel(frame, text="Usuario").grid(row=1, column=0, sticky="e", padx=(20, 8), pady=6)
        self.username_entry = ctk.CTkEntry(frame, width=220)
        self.username_entry.grid(row=1, column=1, padx=(0, 20), pady=6)

        ctk.CTkLabel(frame, text="Contraseña").grid(row=2, column=0, sticky="e", padx=(20, 8), pady=6)
        self.password_entry = ctk.CTkEntry(frame, width=220, show="*")
        self.password_entry.grid(row=2, column=1, padx=(0, 20), pady=6)

        self.message_label = ctk.CTkLabel(frame, text="", text_color="#d9534f", wraplength=300)
        self.message_label.grid(row=3, column=0, columnspan=2, pady=(6, 0), padx=20)

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, pady=(10, 20))
        self.accept_button = ctk.CTkButton(buttons, text="Aceptar", width=110, command=self.attempt_login)
        self.accept_button.pack(side="left", padx=6)
        self.cancel_button = ctk.CTkButton(
            buttons, text="Cancelar", width=110, fg_color="gray40", hover_color="gray30",
            command=self.cancel,
        )
        self.cancel_button.pack(side="left", padx=6)

        self.bind("<Return>", lambda _event: self.attempt_login())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._center()
        self.username_entry.focus_set()

    def _center(self):
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_reqwidth()) // 2
        y = (self.winfo_screenheight() - self.winfo_reqheight()) // 3
        self.geometry(f"+{x}+{y}")

    @property
    def message(self):
        return self.message_label.cget("text")

    def attempt_login(self):
        """Valida los campos, autentica y registra el acceso vía controller y arranca la sesión."""
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if not username or not password:
            self.message_label.configure(text=MSG_EMPTY)
            return

        try:
            user = auth_controller.login(username, password)
        except AccessDenied:
            self._fail(MSG_DENIED)
            return
        except Exception:
            # Detalle solo al log; el usuario ve un mensaje genérico
            logger.exception("Fallo técnico durante el login")
            self._fail(MSG_INTERNAL)
            return

        session.start_session(user)
        self.result = user
        self.quit()

    def _fail(self, text):
        self.message_label.configure(text=text)
        self.password_entry.delete(0, "end")
        self.password_entry.focus_set()

    def cancel(self):
        """Cierra el login sin usuario; la aplicación termina."""
        self.result = None
        self.quit()

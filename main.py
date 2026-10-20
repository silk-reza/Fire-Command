import logging

from views.login_view import LoginView
from views.main_view import MainView


def run_login():
    """Muestra el login y devuelve el usuario autenticado o None."""
    view = LoginView()
    try:
        view.mainloop()
        return view.result
    finally:
        view.destroy()


def run_main(user):
    """Muestra la ventana principal; devuelve True si se cerró sesión."""
    view = MainView(user)
    try:
        view.mainloop()
        return view.logged_out
    finally:
        view.destroy()


def run_app():
    # Una sola ventana CTk viva a la vez
    while True:
        user = run_login()
        if user is None:
            return
        if not run_main(user):
            return


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_app()

import logging
from tkinter import filedialog, ttk

import customtkinter as ctk

from controllers import incident_controller, report_controller
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.report_controller import (
    ReportExportError,
    ReportNotFoundError,
    ReportServiceError,
    ReportValidationError,
)

logger = logging.getLogger(__name__)

MSG_DENIED = "No tiene permiso para consultar reportes."
MSG_INTERNAL = "Ocurrió un problema interno. Intente de nuevo."
MSG_SELECT_ROW = "Seleccione un incidente de la tabla."
ALL_TYPES = "Todos los tipos"
ALL_PRIORITIES = "Todas las prioridades"
ALL_STATES = "Todos los estados"
EMPTY_VALUE = "—"
NO_DATA = "Sin datos"
GENERAL_FILE_NAME = "FireCommand_Reporte_General.pdf"
COLOR_ERROR = "#d9534f"
COLOR_OK = "#2e9e5b"

COLUMNS = (
    ("code", "Código", 110, "w"),
    ("date", "Fecha", 110, "w"),
    ("type", "Tipo", 100, "w"),
    ("priority", "Prioridad", 80, "w"),
    ("state", "Estado", 90, "w"),
    ("address", "Dirección", 240, "w"),
    ("response", "Tiempo respuesta", 120, "e"),
    ("duration", "Duración", 100, "e"),
)


def _date(value):
    return value.strftime("%d/%m/%Y %H:%M") if value else EMPTY_VALUE


def _minutes(value):
    return EMPTY_VALUE if value is None else f"{value:.1f} min"


def _text(value):
    return EMPTY_VALUE if value is None or value == "" else str(value)


def _catalog_text(item):
    return item["name"] if item["active"] else f"{item['name']} (inactivo/a)"


class ReportView(ctk.CTkToplevel):
    """Reporte general de incidentes con detalle y exportación a PDF (solo lectura)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("FireCommand - Reportes")
        self.resizable(False, False)
        self.applied_filters = {}
        self.detail_window = None
        # Solo presentación: texto del selector -> id
        self._type_ids = {}
        self._priority_ids = {}
        self._state_ids = {}

        self._build_filters()
        self._build_table()
        self._build_metrics()
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
        self.search()

    def _build_filters(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(10, 4))

        def label(text, column):
            ctk.CTkLabel(bar, text=text).grid(row=0, column=column, sticky="e", padx=(8, 4))

        def combo(default, column):
            widget = ctk.CTkComboBox(bar, values=[default], width=150, state="readonly")
            widget.set(default)
            widget.grid(row=0, column=column, sticky="w")
            return widget

        label("Fecha desde", 0)
        self.date_from_entry = ctk.CTkEntry(bar, width=105, placeholder_text="AAAA-MM-DD")
        self.date_from_entry.grid(row=0, column=1, sticky="w")
        label("Fecha hasta", 2)
        self.date_to_entry = ctk.CTkEntry(bar, width=105, placeholder_text="AAAA-MM-DD")
        self.date_to_entry.grid(row=0, column=3, sticky="w")
        label("Tipo", 4)
        self.type_filter = combo(ALL_TYPES, 5)
        label("Prioridad", 6)
        self.priority_filter = combo(ALL_PRIORITIES, 7)
        label("Estado", 8)
        self.state_filter = combo(ALL_STATES, 9)
        self.search_button = ctk.CTkButton(bar, text="Buscar", width=80, command=self.search)
        self.search_button.grid(row=0, column=10, padx=(10, 4))
        self.clear_button = ctk.CTkButton(
            bar, text="Limpiar", width=80, fg_color="gray40", hover_color="gray30",
            command=self.clear_filters,
        )
        self.clear_button.grid(row=0, column=11)
        for entry in (self.date_from_entry, self.date_to_entry):
            entry.bind("<Return>", lambda event: self.search())

    def _build_table(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Report.Treeview", background="#2b2b2b", fieldbackground="#2b2b2b",
            foreground="white", rowheight=24, borderwidth=0,
        )
        style.configure("Report.Treeview.Heading", background="#3a3a3a", foreground="white")
        style.map("Report.Treeview", background=[("selected", "#1f6aa5")])

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=4)
        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in COLUMNS], show="headings",
            selectmode="browse", height=11, style="Report.Treeview",
        )
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, anchor=anchor)
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.tree.pack(side="left", fill="x", expand=True)
        self.scrollbar.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._update_buttons())
        self.tree.bind("<Double-1>", lambda event: self.show_detail())

    def _build_metrics(self):
        strip = ctk.CTkFrame(self)
        strip.pack(fill="x", padx=16, pady=4)
        self.metric_labels = {}
        for column, (key, title) in enumerate((
            ("count", "Incidentes"), ("response", "Tiempo promedio de respuesta"),
            ("units", "Unidades utilizadas"), ("stations", "Estaciones con mayor actividad"),
        )):
            cell = ctk.CTkFrame(strip, fg_color="transparent")
            cell.grid(row=0, column=column, padx=14, pady=4, sticky="w")
            ctk.CTkLabel(cell, text=title, text_color="gray70").pack(anchor="w")
            value = ctk.CTkLabel(
                cell, text=EMPTY_VALUE, font=ctk.CTkFont(size=15, weight="bold"),
                anchor="w", wraplength=330, justify="left",
            )
            value.pack(anchor="w")
            self.metric_labels[key] = value

    def _build_buttons(self):
        self.message_label = ctk.CTkLabel(self, text="", wraplength=1000, justify="left")
        self.message_label.pack(fill="x", padx=18)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(padx=16, pady=(4, 10))
        self.detail_button = ctk.CTkButton(
            bar, text="Ver detalle", width=110, command=self.show_detail
        )
        self.export_general_button = ctk.CTkButton(
            bar, text="Exportar PDF general", width=160, command=self.export_general
        )
        self.export_incident_button = ctk.CTkButton(
            bar, text="Exportar PDF del incidente", width=190, command=self.export_selected
        )
        self.back_button = ctk.CTkButton(
            bar, text="Regresar", width=100, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        for button in (
            self.detail_button, self.export_general_button, self.export_incident_button,
            self.back_button,
        ):
            button.pack(side="left", padx=4)
        self._update_buttons()

    def ask_save_path(self, default_name):
        """Pide la ruta del PDF; devuelve None/'' si se cancela. Se reemplaza en pruebas."""
        try:
            return filedialog.asksaveasfilename(
                parent=self, defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                initialfile=default_name,
            )
        finally:
            # El diálogo toma el grab y Tk no lo devuelve; se restablece el foco modal
            self._restore_modal()

    def _restore_modal(self):
        try:
            target = self.detail_window if self._detail_open() else self
            if target.winfo_exists():
                target.lift()
                target.grab_set()
                target.focus_set()
        except Exception:
            pass

    def _detail_open(self):
        return self.detail_window is not None and self.detail_window.winfo_exists()

    def close(self):
        if self._detail_open():
            self.detail_window.destroy()
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
            ReportValidationError, ReportNotFoundError, ReportExportError, ReportServiceError,
        ) as error:
            self.show_message(str(error), error=True)
        except (AuthorizationDenied, NotAuthenticated):
            self.show_message(MSG_DENIED, error=True)
        except Exception:
            logger.exception("Error en el módulo de reportes")
            self.show_message(MSG_INTERNAL, error=True)
        return False, None

    def load_options(self):
        """Carga los catálogos de los filtros con las mismas consultas de la consulta de incidentes."""
        ok_t, types = self._run(incident_controller.list_incident_types)
        ok_p, priorities = self._run(incident_controller.list_priorities)
        ok_s, states = self._run(incident_controller.list_incident_states)
        if not (ok_t and ok_p and ok_s):
            return
        self._type_ids = {_catalog_text(t): t["type_id"] for t in types}
        self._priority_ids = {_catalog_text(p): p["priority_id"] for p in priorities}
        self._state_ids = {s["name"]: s["state_id"] for s in states}
        self.type_filter.configure(values=[ALL_TYPES] + list(self._type_ids))
        self.priority_filter.configure(values=[ALL_PRIORITIES] + list(self._priority_ids))
        self.state_filter.configure(values=[ALL_STATES] + list(self._state_ids))

    def _current_filters(self):
        """Filtros del formulario; solo se incluyen los que tienen valor."""
        filters = {}
        date_from = self.date_from_entry.get().strip()
        date_to = self.date_to_entry.get().strip()
        if date_from:
            filters["date_from"] = date_from
        if date_to:
            filters["date_to"] = date_to
        for key, widget, options in (
            ("type_id", self.type_filter, self._type_ids),
            ("priority_id", self.priority_filter, self._priority_ids),
            ("state_id", self.state_filter, self._state_ids),
        ):
            value = options.get(widget.get())
            if value is not None:
                filters[key] = value
        return filters

    def search(self):
        """Una sola consulta alimenta la tabla y las métricas."""
        filters = self._current_filters()
        ok, data = self._run(report_controller.search_general_report, filters)
        if not ok:
            return
        self.applied_filters = filters
        self._render(data)
        self.show_message(f"{len(data['incidents'])} incidente(s) encontrados.")

    def clear_filters(self):
        self.date_from_entry.delete(0, "end")
        self.date_to_entry.delete(0, "end")
        self.type_filter.set(ALL_TYPES)
        self.priority_filter.set(ALL_PRIORITIES)
        self.state_filter.set(ALL_STATES)
        self.search()

    def _render(self, data):
        self.tree.delete(*self.tree.get_children())
        for item in data["incidents"]:
            self.tree.insert("", "end", iid=str(item["incident_id"]), values=(
                item["code"], _date(item["reported_at"]), item["type_name"],
                item["priority_name"], item["state_name"], item["address"] or "",
                _minutes(item["response_minutes"]), _minutes(item["duration_minutes"]),
            ))
        metrics = data["metrics"]
        stations = ", ".join(
            f"{s['station_code']} ({s['activity']})" for s in metrics["top_stations"]
        )
        self.metric_labels["count"].configure(text=str(metrics["incident_count"]))
        self.metric_labels["response"].configure(text=_minutes(metrics["avg_response_minutes"]))
        self.metric_labels["units"].configure(text=str(metrics["units_used"]))
        self.metric_labels["stations"].configure(text=stations or NO_DATA)
        self._update_buttons()

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            return None, None
        return int(selection[0]), self.tree.item(selection[0], "values")[0]

    def _update_buttons(self):
        selected = bool(self.tree.selection())
        state = "normal" if selected else "disabled"
        self.detail_button.configure(state=state)
        self.export_incident_button.configure(state=state)

    def export_general(self):
        path = self.ask_save_path(GENERAL_FILE_NAME)
        if not path:
            return
        ok, written = self._run(
            report_controller.export_general_pdf, self.applied_filters, path
        )
        if ok:
            self.show_message(f"PDF generado: {written}")

    def export_selected(self):
        incident_id, code = self._selected()
        if incident_id is None:
            self.show_message(MSG_SELECT_ROW, error=True)
            return
        self.export_incident(incident_id, code)

    def export_incident(self, incident_id, code):
        path = self.ask_save_path(f"FireCommand_{code}.pdf")
        if not path:
            return
        ok, written = self._run(report_controller.export_incident_pdf, incident_id, path)
        if ok:
            self.show_message(f"PDF generado: {written}")

    def show_detail(self):
        incident_id, code = self._selected()
        if incident_id is None:
            self.show_message(MSG_SELECT_ROW, error=True)
            return
        ok, report = self._run(report_controller.get_incident_report, incident_id)
        if not ok:
            return
        if self._detail_open():
            self.detail_window.destroy()
        self.detail_window = _DetailWindow(self, incident_id, code, report)
        return self.detail_window


def detail_text(report):
    """Texto legible del reporte de un incidente; nunca imprime None."""
    lines = [
        f"Código: {_text(report['code'])}",
        f"Fecha de reporte: {_date(report['reported_at'])}",
        f"Tipo: {_text(report['type_name'])}    Prioridad: {_text(report['priority_name'])}",
        f"Estado: {_text(report['state_name'])}",
        f"Dirección: {_text(report['address'])}",
        f"Referencia: {_text(report['reference'])}",
        f"Descripción: {_text(report['description'])}",
        f"Personas afectadas: {_text(report['affected_people'])}",
        f"Registrado por: {_text(report['registered_by_name'])}",
        f"Tiempo de respuesta: {_minutes(report['response_minutes'])}",
        f"Duración: {_minutes(report['duration_minutes'])}",
    ]
    if report.get("cancel_reason"):
        lines.append(f"Motivo de cancelación: {report['cancel_reason']}")
    lines += ["", "LÍNEA DE TIEMPO"]
    lines += [f"  {item['label']}: {_date(item['at'])}" for item in report["timeline"]] or [
        f"  {NO_DATA}"]
    lines += ["", "DESPACHOS"]
    for item in report["dispatches"]:
        lines.append(
            f"  N.º {item['dispatch_id']} — {_text(item['state_name'])} — "
            f"creado {_date(item['created_at'])}"
        )
        if item.get("cancel_reason"):
            lines.append(f"    Motivo de cancelación: {item['cancel_reason']}")
        if item.get("observations"):
            lines.append(f"    Observaciones: {item['observations']}")
    if not report["dispatches"]:
        lines.append(f"  {NO_DATA}")
    lines += ["", "UNIDADES Y PARTICIPACIONES"]
    for unit in report["units"]:
        name = f" ({unit['unit_name']})" if unit.get("unit_name") else ""
        lines.append(
            f"  {unit['unit_code']}{name} — {_text(unit['unit_type_name'])} — "
            f"origen {_text(unit['origin_station_code'])} — {_text(unit['participation_status'])}"
        )
        lines.append(
            f"    Asignada {_date(unit['assigned_at'])} · Salida {_date(unit['departed_at'])}"
            f" · Llegada {_date(unit['arrived_at'])} · Liberación {_date(unit['released_at'])}"
        )
        if unit.get("cancel_reason"):
            lines.append(f"    Motivo de cancelación: {unit['cancel_reason']}")
    if not report["units"]:
        lines.append(f"  {NO_DATA}")
    lines += ["", "TRIPULACIÓN"]
    for member in report["crew"]:
        lines.append(
            f"  {member['unit_code']} — {_text(member['full_name'])} ({_text(member['role'])})"
        )
    if not report["crew"]:
        lines.append(f"  {NO_DATA}")
    return "\n".join(lines)


class _DetailWindow(ctk.CTkToplevel):
    """Detalle de solo lectura de un incidente, con su propia exportación."""

    def __init__(self, owner, incident_id, code, report):
        super().__init__(owner)
        self.owner = owner
        self.incident_id = incident_id
        self.code = code
        self.title("Detalle del incidente")
        self.resizable(False, False)
        self.text = ctk.CTkTextbox(self, width=760, height=470, wrap="word")
        self.text.pack(padx=14, pady=(14, 6))
        self.text.insert("1.0", detail_text(report))
        self.text.configure(state="disabled")
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(pady=(0, 12))
        self.export_button = ctk.CTkButton(
            bar, text="Exportar PDF", width=120, command=self.export
        )
        self.export_button.pack(side="left", padx=4)
        self.close_button = ctk.CTkButton(
            bar, text="Cerrar", width=100, fg_color="gray40", hover_color="gray30",
            command=self.close,
        )
        self.close_button.pack(side="left", padx=4)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(owner)
        self.update_idletasks()
        try:
            self.grab_set()
        except Exception:
            pass

    def export(self):
        self.owner.export_incident(self.incident_id, self.code)

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        owner = self.owner
        self.destroy()
        owner.detail_window = None
        owner._restore_modal()

"""Generación de PDF de reportes con ReportLab (sin acceso a base de datos)."""
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

_DASH = "—"
_HEADER_BG = colors.HexColor("#7f1d1d")
_GRID = colors.HexColor("#9ca3af")
_PARTICIPATION_LABELS = {
    "ACTIVA": "Activa", "LIBERADA": "Liberada", "CANCELADA": "Cancelada",
    "HISTORICA": "Histórica",
}
# Relleno horizontal de cada celda y tamaños de fuente que se prueban en las tablas anchas
_PAD = 3
_FONT_SIZES = (7.5, 7, 6.5, 6)
_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle("FcTitle", parent=_styles["Title"], fontName=_FONT_BOLD,
                       fontSize=18, textColor=_HEADER_BG, spaceAfter=6)
HEADING = ParagraphStyle("FcHeading", parent=_styles["Heading2"], fontName=_FONT_BOLD,
                         fontSize=12, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("FcBody", parent=_styles["BodyText"], fontName=_FONT,
                      fontSize=9, leading=12)
PAIR_CELL = ParagraphStyle("FcPair", parent=BODY, fontSize=8.5, leading=11)

# Diseños de las tablas generadas en la última construcción (para verificar anchos)
LAST_LAYOUTS = []


def format_datetime(value):
    return value.strftime("%d/%m/%Y %H:%M") if value else _DASH


def format_datetime_stacked(value):
    """Fecha y hora en dos líneas para columnas angostas."""
    return value.strftime("%d/%m/%Y\n%H:%M") if value else _DASH


def format_minutes(value):
    """'8.4 min' o '2 h 5 min' (desde 60 minutos); guion si no hay dato."""
    if value is None:
        return _DASH
    if value >= 60:
        hours, minutes = divmod(round(value), 60)
        return f"{hours} h {minutes} min"
    return f"{value:.1f} min"


def _label(value):
    """Códigos con guion bajo se muestran con espacios para que puedan partirse."""
    return value.replace("_", " ") if isinstance(value, str) else value


def _soft_wrap(text, limit=24):
    """Último recurso: parte las palabras sin espacios muy largas."""
    lines = []
    for line in text.split("\n"):
        words = []
        for word in line.split(" "):
            while len(word) > limit:
                words.append(word[:limit])
                word = word[limit:]
            words.append(word)
        lines.append(" ".join(words))
    return "\n".join(lines)


def _text(value):
    return _DASH if value is None or value == "" else _soft_wrap(str(value))


def _markup(value):
    # Los textos de usuario se escapan para que <, > y & no rompan el XML de Paragraph
    return escape(_text(value)).replace("\n", "<br/>")


def _tokens(text):
    return text.replace("\n", " ").split()


def table_layout(header, rows, total_width):
    """Elige tamaño de fuente y anchos de columna para que ninguna palabra se parta.

    Cada columna recibe como mínimo el ancho de su palabra más larga (encabezado o
    valores); el resto se reparte según lo que cada columna necesitaría en una sola línea.
    """
    texts = [[_text(cell) for cell in row] for row in rows]
    columns = len(header)
    for size in _FONT_SIZES:
        minimum, ideal = [], []
        for index in range(columns):
            head_tokens = _tokens(header[index])
            minimum.append(max(
                [stringWidth(t, _FONT_BOLD, size) for t in head_tokens]
                + [stringWidth(t, _FONT, size) for row in texts for t in _tokens(row[index])]
                + [0]) + 2 * _PAD + 1)
            lines = [stringWidth(header[index], _FONT_BOLD, size)]
            lines += [stringWidth(line, _FONT, size)
                      for row in texts for line in row[index].split("\n")]
            ideal.append(max(lines) + 2 * _PAD + 1)
        if sum(minimum) <= total_width or size == _FONT_SIZES[-1]:
            break
    fits = sum(minimum) <= total_width
    if not fits:
        # No hay forma de respetar todas las palabras: se reduce proporcionalmente
        scale = total_width / sum(minimum)
        widths = [m * scale for m in minimum]
    else:
        widths = list(minimum)
        spare = total_width - sum(widths)
        wish = [max(i - m, 0) for i, m in zip(ideal, minimum)]
        wanted = sum(wish)
        if wanted > 0:
            grant = min(spare, wanted)
            widths = [w + grant * c / wanted for w, c in zip(widths, wish)]
            spare -= grant
        if spare > 0:
            widths = [w + spare * w / sum(widths) for w in widths]
    return {"widths": widths, "size": size, "minimum": minimum, "fits": fits,
            "header": list(header), "rows": texts}


def longest_token_fits(layout):
    """True si en cada columna la palabra más larga cabe en el ancho asignado."""
    size = layout["size"]
    for index, width in enumerate(layout["widths"]):
        tokens = [(t, _FONT_BOLD) for t in _tokens(layout["header"][index])]
        tokens += [(t, _FONT) for row in layout["rows"] for t in _tokens(row[index])]
        for token, font in tokens:
            if stringWidth(token, font, size) + 2 * _PAD > width + 0.01:
                return False
    return True


def _table(header, rows, total_width):
    layout = table_layout(header, rows, total_width)
    LAST_LAYOUTS.append(layout)
    size = layout["size"]
    cell = ParagraphStyle("FcCell", parent=BODY, fontSize=size, leading=size * 1.25)
    head = ParagraphStyle("FcCellHead", parent=cell, fontName=_FONT_BOLD, textColor=colors.white)
    data = [[Paragraph(escape(h), head) for h in header]]
    data += [[Paragraph(_markup(c), cell) for c in row] for row in rows]
    table = Table(data, colWidths=layout["widths"], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), _PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), _PAD),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
    ]))
    return table


def _pairs_table(pairs, width):
    data = [[Paragraph(f"<b>{escape(label)}</b>", PAIR_CELL), Paragraph(_markup(value), PAIR_CELL)]
            for label, value in pairs]
    table = Table(data, colWidths=[width * 0.22, width * 0.78])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
    ]))
    return table


def _footer(canvas, document):
    canvas.saveState()
    canvas.setFont(_FONT, 8)
    width = document.pagesize[0]
    canvas.drawString(15 * mm, 8 * mm, "FireCommand")
    canvas.drawRightString(width - 15 * mm, 8 * mm,
                           f"Página {document.page} — generado {format_datetime(datetime.now())}")
    canvas.restoreState()


def _document(path, pagesize, compress, title):
    return SimpleDocTemplate(
        path, pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=16 * mm, title=title, author="FireCommand",
        pageCompression=1 if compress else 0)


def build_incident_pdf(report, path, compress=True):
    """Escribe el PDF del reporte de un incidente (dict de report_model).

    Va en A4 apaisado: la tabla de participaciones tiene muchas columnas de fecha.
    """
    LAST_LAYOUTS.clear()
    document = _document(path, landscape(A4), compress, "Reporte de incidente")
    width = document.width
    story = [Paragraph("FireCommand — Reporte de incidente", TITLE)]
    coordinates = (f"{report['latitude']:.6f}, {report['longitude']:.6f}"
                   if report.get("latitude") is not None and report.get("longitude") is not None
                   else None)
    story.append(_pairs_table([
        ("Código", report["code"]),
        ("Fecha de reporte", format_datetime(report["reported_at"])),
        ("Tipo", _label(report["type_name"])),
        ("Prioridad", report["priority_name"]),
        ("Estado", report["state_name"]),
        ("Dirección", report["address"]),
        ("Referencia", report.get("reference")),
        ("Coordenadas", coordinates),
        ("Personas afectadas", report["affected_people"]),
        ("Registrado por", report.get("registered_by_name") or report.get("registered_by_username")),
        ("Tiempo de respuesta", format_minutes(report["response_minutes"])),
        ("Duración", format_minutes(report["duration_minutes"])),
    ], width))
    story.append(Paragraph("Descripción", HEADING))
    story.append(Paragraph(_markup(report["description"]), BODY))

    story.append(Paragraph("Timeline", HEADING))
    story.append(_table(["Hito", "Fecha"],
                        [[e["label"], format_datetime(e["at"])] for e in report["timeline"]],
                        width * 0.5))

    story.append(Paragraph("Despachos", HEADING))
    if report["dispatches"]:
        story.append(_table(
            ["Despacho", "Estado", "Creado", "Despachado", "Finalizado", "Cancelado",
             "Observaciones", "Motivo cancelación"],
            [[d["dispatch_id"], d["state_name"], format_datetime_stacked(d["created_at"]),
              format_datetime_stacked(d["dispatched_at"]),
              format_datetime_stacked(d["finished_at"]),
              format_datetime_stacked(d["cancelled_at"]), d["observations"],
              d["cancel_reason"]] for d in report["dispatches"]],
            width))
    else:
        story.append(Paragraph("Sin despachos.", BODY))

    story.append(Paragraph("Unidades / participaciones", HEADING))
    if report["units"]:
        story.append(_table(
            ["Despacho", "Unidad", "Tipo", "Estación origen", "Estado", "Asignación", "Salida",
             "Llegada", "Liberación", "Cancelación", "Motivo"],
            [[u["dispatch_id"], f"{u['unit_code']} {u['unit_name'] or ''}".strip(),
              _label(u["unit_type_name"]), u["origin_station_code"],
              _PARTICIPATION_LABELS.get(u["participation_status"], u["participation_status"]),
              format_datetime_stacked(u["assigned_at"]),
              format_datetime_stacked(u["departed_at"]),
              format_datetime_stacked(u["arrived_at"]),
              format_datetime_stacked(u["released_at"]),
              format_datetime_stacked(u["cancelled_at"]), u["cancel_reason"]]
             for u in report["units"]],
            width))
    else:
        story.append(Paragraph("Sin participaciones de unidades.", BODY))

    story.append(Paragraph("Tripulación", HEADING))
    if report["crew"]:
        story.append(_table(
            ["Despacho", "Unidad", "Código", "Nombre", "Cargo"],
            [[c["dispatch_id"], c["unit_code"], c["personnel_code"], c["full_name"], c["role"]]
             for c in report["crew"]],
            width * 0.8))
    else:
        story.append(Paragraph("Sin tripulación registrada.", BODY))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def describe_filters(applied):
    """Texto legible de los filtros aplicados; 'Sin filtros' si no hay ninguno."""
    parts = []
    if applied.get("date_from"):
        parts.append(f"Desde: {applied['date_from']}")
    if applied.get("date_to"):
        parts.append(f"Hasta: {applied['date_to']}")
    if applied.get("type_id") is not None:
        parts.append(f"Tipo: {applied.get('type_name') or applied['type_id']}")
    if applied.get("priority_id") is not None:
        parts.append(f"Prioridad: {applied.get('priority_name') or applied['priority_id']}")
    if applied.get("state_id") is not None or applied.get("state_code"):
        parts.append(f"Estado: {applied.get('state_name') or applied.get('state_code')}")
    return " | ".join(parts) if parts else "Sin filtros"


def build_general_pdf(general, path, compress=True):
    """Escribe el PDF del reporte general (dict de report_model.search_general_report)."""
    LAST_LAYOUTS.clear()
    document = _document(path, landscape(A4), compress, "Reporte general de incidentes")
    width = document.width
    metrics = general["metrics"]
    story = [Paragraph("FireCommand — Reporte general de incidentes", TITLE)]
    story.append(Paragraph("Filtros aplicados", HEADING))
    story.append(Paragraph(_markup(describe_filters(general["filters_applied"])), BODY))
    story.append(Paragraph("Métricas", HEADING))
    stations = "; ".join(
        f"{s['station_code']} ({s['activity']})" for s in metrics["top_stations"]) or _DASH
    story.append(_pairs_table([
        ("Cantidad de incidentes", metrics["incident_count"]),
        ("Tiempo promedio de respuesta", format_minutes(metrics["avg_response_minutes"])),
        ("Estaciones con mayor actividad", stations),
        ("Unidades utilizadas", metrics["units_used"]),
    ], width))
    story.append(Paragraph("Incidentes", HEADING))
    if general["incidents"]:
        story.append(_table(
            ["Código", "Fecha", "Tipo", "Prioridad", "Estado", "Dirección",
             "Tiempo de respuesta", "Duración"],
            [[i["code"], format_datetime_stacked(i["reported_at"]), _label(i["type_name"]),
              i["priority_name"], i["state_name"], i["address"],
              format_minutes(i["response_minutes"]), format_minutes(i["duration_minutes"])]
             for i in general["incidents"]],
            width))
    else:
        story.append(Paragraph("No hay incidentes para los filtros indicados.", BODY))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)

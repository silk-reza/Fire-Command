import os
import re
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta

from controllers import report_controller as rc
from controllers.auth_controller import authenticate
from tests.test_reports import ReportBase
from utils import report_pdf, session

DEMO_PASSWORD = "FireDemo2026!"
WHEN = datetime(2026, 5, 2, 14, 30)


def sample_incident(**overrides):
    report = {
        "incident_id": 1, "code": "INC-TEST-001", "reported_at": WHEN,
        "type_name": "INCENDIO", "priority_name": "ALTA", "priority_level": 3,
        "state_code": "CERRADO", "state_name": "Cerrado",
        "address": "12 Calle & Avenida <Zona 1> ñandú", "reference": None,
        "latitude": 14.6, "longitude": -90.5,
        "description": "Humo <b>negro</b> & llamas. Ubicación: señalización áéíóú ÑÁ",
        "affected_people": 3, "registered_by_username": "admin.demo",
        "registered_by_name": "Admin Demo",
        "timeline": [{"key": "reported", "label": "Reporte", "at": WHEN},
                     {"key": "closed", "label": "Cierre", "at": WHEN + timedelta(hours=2)}],
        "dispatches": [{"dispatch_id": 7, "state_code": "COMPLETADO", "state_name": "Completado",
                        "created_at": WHEN, "dispatched_at": WHEN, "finished_at": WHEN,
                        "cancelled_at": None, "cancel_reason": None,
                        "observations": None, "created_by_username": "admin.demo"}],
        "units": [{"dispatch_unit_id": 1, "dispatch_id": 7, "unit_id": 2, "unit_code": "B-01",
                   "unit_name": None, "unit_type_name": "AUTOBOMBA",
                   "origin_station_code": "EST-01", "origin_station_name": "Central",
                   "assigned_at": WHEN, "departed_at": WHEN, "arrived_at": WHEN,
                   "released_at": WHEN, "cancelled_at": None, "cancel_reason": None,
                   "participation_status": "LIBERADA"}],
        "crew": [{"crew_id": 1, "dispatch_unit_id": 1, "dispatch_id": 7, "unit_code": "B-01",
                  "personnel_code": "P-1", "first_name": "A", "last_name": "B",
                  "full_name": "Ana Pérez", "role": "Bombero"}],
        "response_minutes": 8.4, "duration_minutes": None,
    }
    report.update(overrides)
    return report


def sample_general(rows=2, filters=None):
    applied = {"date_from": None, "date_to": None, "type_id": None, "type_name": None,
               "priority_id": None, "priority_name": None, "state_id": None,
               "state_code": None, "state_name": None}
    applied.update(filters or {})
    return {
        "incidents": [{
            "incident_id": n, "code": f"INC-{n:04d}", "reported_at": WHEN, "type_name": "OTRO",
            "priority_name": "BAJA", "state_code": "REPORTADO", "state_name": "Reportado",
            "address": f"Dirección número {n} & <esquina>", "response_minutes": None,
            "duration_minutes": 125.0} for n in range(rows)],
        "metrics": {"incident_count": rows, "avg_response_minutes": None,
                    "top_stations": [{"station_id": 1, "station_code": "EST-01",
                                      "station_name": "Central", "activity": 4}],
                    "units_used": 3, "top_units": []},
        "filters_applied": applied,
    }


def page_count(content):
    return len(re.findall(rb"/Type /Page(?!s)", content))


class ReportPdfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def test_format_helpers(self):
        self.assertEqual(report_pdf.format_minutes(None), "—")
        self.assertEqual(report_pdf.format_minutes(8.4), "8.4 min")
        self.assertEqual(report_pdf.format_minutes(125.0), "2 h 5 min")
        self.assertEqual(report_pdf.format_datetime(WHEN), "02/05/2026 14:30")
        self.assertEqual(report_pdf.format_datetime(None), "—")

    def test_incident_pdf(self):
        path = self.path("incidente.pdf")
        report_pdf.build_incident_pdf(sample_incident(), path, compress=False)
        content = self.read(path)
        self.assertTrue(content.startswith(b"%PDF"))
        for expected in (b"INC-TEST-001", b"Reporte de incidente", b"Timeline", b"Despachos",
                         b"Tripulaci", b"B-01", b"8.4 min", b"Descripci"):
            self.assertIn(expected, content)
        self.assertNotIn(b"(None", content)
        # También se genera comprimido por defecto
        compressed = self.path("comprimido.pdf")
        report_pdf.build_incident_pdf(sample_incident(), compressed)
        self.assertTrue(self.read(compressed).startswith(b"%PDF"))

    def test_incident_pdf_empty_sections_and_dashes(self):
        path = self.path("vacio.pdf")
        report_pdf.build_incident_pdf(sample_incident(
            dispatches=[], units=[], crew=[], timeline=[], latitude=None, longitude=None,
            response_minutes=None, reference=None), path, compress=False)
        content = self.read(path)
        self.assertIn(b"Sin despachos", content)
        self.assertIn(b"Sin tripulaci", content)
        self.assertNotIn(b"(None", content)

    def test_general_pdf(self):
        path = self.path("general.pdf")
        report_pdf.build_general_pdf(
            sample_general(3, {"type_id": 1, "type_name": "INCENDIO", "date_from": "2026-05-01"}),
            path, compress=False)
        content = self.read(path)
        self.assertTrue(content.startswith(b"%PDF"))
        for expected in (b"Reporte general de incidentes", b"Filtros aplicados",
                         b"Tipo: INCENDIO", b"Desde: 2026-05-01", b"EST-01",
                         b"INC-0000", b"2 h 5 min"):
            self.assertIn(expected, content)
        self.assertNotIn(b"(None", content)
        empty = self.path("sin_filtros.pdf")
        report_pdf.build_general_pdf(sample_general(0), empty, compress=False)
        content = self.read(empty)
        self.assertIn(b"Sin filtros", content)
        self.assertIn(b"No hay incidentes", content)

    def test_general_pdf_paginates_with_repeated_header(self):
        path = self.path("largo.pdf")
        report_pdf.build_general_pdf(sample_general(120), path, compress=False)
        content = self.read(path)
        self.assertGreater(page_count(content), 1)
        self.assertGreater(content.count(b"(Prioridad) Tj"), 1)  # encabezado repetido
        self.assertIn(b"INC-0119", content)

    def test_columns_fit_their_longest_word(self):
        long_types = ["MATERIALES_PELIGROSOS", "ACCIDENTE_VEHICULAR", "EMERGENCIA_MEDICA"]
        general = sample_general(3)
        for incident, type_name in zip(general["incidents"], long_types):
            incident["type_name"] = type_name
            incident["state_name"] = "Controlado"
        report_pdf.build_general_pdf(general, self.path("g.pdf"))
        self.assertTrue(report_pdf.LAST_LAYOUTS)
        for layout in report_pdf.LAST_LAYOUTS:
            self.assertTrue(layout["fits"])
            self.assertTrue(report_pdf.longest_token_fits(layout))
            self.assertLessEqual(sum(layout["widths"]), 842 - 2 * 15 * 2.8346 + 0.5)
        report = sample_incident(
            units=[dict(sample_incident()["units"][0], unit_type_name="AMBULANCIA",
                        origin_station_code="EST-CENTRAL", dispatch_id=6158,
                        participation_status="LIBERADA", unit_name="Unidad de rescate")],
            dispatches=[dict(sample_incident()["dispatches"][0], dispatch_id=6158,
                             state_name="Completado")],
            type_name="MATERIALES_PELIGROSOS")
        report_pdf.build_incident_pdf(report, self.path("i.pdf"))
        self.assertGreaterEqual(len(report_pdf.LAST_LAYOUTS), 4)
        for layout in report_pdf.LAST_LAYOUTS:
            self.assertTrue(layout["fits"])
            self.assertTrue(report_pdf.longest_token_fits(layout))
            self.assertGreaterEqual(layout["size"], 6.5)
        # Cualquier palabra más ancha que su columna se detecta
        broken = dict(report_pdf.LAST_LAYOUTS[0], widths=[10.0] * len(
            report_pdf.LAST_LAYOUTS[0]["widths"]))
        self.assertFalse(report_pdf.longest_token_fits(broken))
        # Las fechas van en dos líneas y los códigos con guion bajo se pueden partir
        self.assertEqual(report_pdf.format_datetime_stacked(WHEN), "02/05/2026" + chr(10) + "14:30")
        self.assertEqual(report_pdf._label("MATERIALES_PELIGROSOS"), "MATERIALES PELIGROSOS")

    def test_special_and_long_text_do_not_crash(self):
        nasty = "&<>\"' áéíóúñÑ " + "X" * 600 + " " + "https://" + "a" * 300
        path = self.path("especial.pdf")
        report_pdf.build_incident_pdf(sample_incident(
            description=nasty, address=nasty, dispatches=[{
                "dispatch_id": 1, "state_name": "Activo", "created_at": WHEN,
                "dispatched_at": None, "finished_at": None, "cancelled_at": None,
                "cancel_reason": nasty, "observations": nasty}]), path)
        self.assertTrue(self.read(path).startswith(b"%PDF"))
        rows = sample_general(2)
        rows["incidents"][0]["address"] = nasty
        report_pdf.build_general_pdf(rows, self.path("general_especial.pdf"))
        self.assertTrue(self.read(self.path("general_especial.pdf")).startswith(b"%PDF"))


class ReportExportControllerTest(ReportBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_export_incident_and_general(self):
        incident_id = self.incident()
        target = os.path.join(self.tmp.name, "incidente.pdf")
        written = rc.export_incident_pdf(incident_id, target, compress=False)
        self.assertEqual(written, target)
        with open(target, "rb") as handle:
            content = handle.read()
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertIn(f"{self.prefix}-RP1".encode(), content)
        general = os.path.join(self.tmp.name, "general.pdf")
        rc.export_general_pdf({"type_id": self.seed_type}, general, compress=False)
        with open(general, "rb") as handle:
            content = handle.read()
        self.assertIn(b"Filtros aplicados", content)
        self.assertIn(b"Tipo:", content)

    def test_failed_build_keeps_existing_file(self):
        target = os.path.join(self.tmp.name, "previo.pdf")
        with open(target, "wb") as handle:
            handle.write(b"%PDF-previo")

        def partial_writer(data, path, compress=True):
            with open(path, "wb") as handle:
                handle.write(b"parcial")
            raise RuntimeError("fallo al construir")

        for failing in (mock.Mock(side_effect=RuntimeError("fallo")), partial_writer):
            with mock.patch.object(rc.report_pdf, "build_general_pdf", failing):
                with self.assertRaises(rc.ReportExportError):
                    rc.export_general_pdf(None, target)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"%PDF-previo")
            self.assertEqual(os.listdir(self.tmp.name), ["previo.pdf"])
        rc.export_general_pdf({"type_id": self.seed_type}, target)
        with open(target, "rb") as handle:
            content = handle.read()
        self.assertTrue(content.startswith(b"%PDF") and content != b"%PDF-previo")
        self.assertEqual(os.listdir(self.tmp.name), ["previo.pdf"])

    def test_path_rules_and_errors(self):
        incident_id = self.incident()
        # Sin extensión se agrega .pdf; otra extensión se rechaza
        written = rc.export_incident_pdf(incident_id, os.path.join(self.tmp.name, "sin_ext"))
        self.assertTrue(written.endswith("sin_ext.pdf") and os.path.exists(written))
        for bad in (None, "", "   ", 5):
            with self.assertRaises(rc.ReportExportError):
                rc.export_incident_pdf(incident_id, bad)
        with self.assertRaises(rc.ReportExportError):
            rc.export_incident_pdf(incident_id, os.path.join(self.tmp.name, "x.txt"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "x.txt")))
        missing_dir = os.path.join(self.tmp.name, "no_existe", "r.pdf")
        with self.assertRaises(rc.ReportExportError) as ctx:
            rc.export_general_pdf(None, missing_dir)
        self.assertIn("ruta", str(ctx.exception))
        self.assertNotIn("Traceback", str(ctx.exception))
        with self.assertRaises(rc.ReportNotFoundError):
            rc.export_incident_pdf(999999, os.path.join(self.tmp.name, "n.pdf"))
        with self.assertRaises(rc.ReportValidationError):
            rc.export_general_pdf({"date_from": "mal"}, os.path.join(self.tmp.name, "g.pdf"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "g.pdf")))


if __name__ == "__main__":
    unittest.main()

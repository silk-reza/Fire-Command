import unittest
from decimal import Decimal
from unittest import mock

import mysql.connector

from controllers import incident_controller as ic
from controllers.access_controller import AuthorizationDenied, NotAuthenticated
from controllers.auth_controller import authenticate
from models import incident_model
from tests._incident_support import IncidentTestBase
from utils import session

DEMO_PASSWORD = "FireDemo2026!"
STATE_KEYS = ("state", "state_id", "state_code", "status", "id_estado_incidente")
USER_KEYS = ("user_id", "id_usuario_registro", "registered_by", "registered_by_id",
             "created_by")
DATE_KEYS = ("fecha_cierre", "fecha_cancelacion", "fecha_reporte", "reported_at",
             "assigned_at", "attention_started_at", "controlled_at", "closed_at",
             "cancelled_at", "cancel_reason", "motivo_cancelacion")


def _login(username):
    session.start_session(authenticate(username, DEMO_PASSWORD))


class IncidentControllerTest(IncidentTestBase):
    def setUp(self):
        super().setUp()
        session.end_session()
        _login("admin.demo")

    def tearDown(self):
        session.end_session()
        super().tearDown()

    def _data(self, suffix="A", **overrides):
        data = {
            "code": f"{self.prefix}-{suffix}", "type_id": self.seed_type,
            "priority_id": self.seed_priority, "address": "Calle 1", "reference": "Ref",
            "latitude": "14.6", "longitude": "-90.5", "description": "Descripción",
            "affected_people": 2,
        }
        data.update(overrides)
        return data

    def _edit(self, **overrides):
        data = self._data(**overrides)
        data.pop("code")
        return data

    def _create(self, suffix="A", **overrides):
        return ic.create_incident(self._data(suffix, **overrides))

    def _ids(self, incidents):
        return [i["incident_id"] for i in incidents]

    def test_list_search_filter(self):
        incident = self._create(description="Zzincendio raro", address="Zzcalle")
        iid = incident["incident_id"]
        seed = [i for i in ic.list_incidents() if not i["code"].startswith(("TIN-", "TUI-"))]
        self.assertEqual(len(seed), 8)
        for term in (self.prefix, "  ZZINCENDIO ", "zzcalle"):
            self.assertEqual(self._ids(ic.search_incidents(term)), [iid], term)
        self.assertEqual(ic.search_incidents("inexistente-" + self.prefix), [])
        self.assertEqual(len(ic.search_incidents("  ")), len(ic.list_incidents()))
        self.assertEqual(len(ic.search_incidents(None)), len(ic.list_incidents()))
        self.assertEqual(self._ids(ic.filter_incidents(
            type_id=self.seed_type, priority_id=self.seed_priority, query=self.prefix)), [iid])
        self.assertEqual(ic.filter_incidents(type_id=self.temp_type(), query=self.prefix), [])
        states = {s["code"]: s["state_id"] for s in ic.list_incident_states()}
        self.assertEqual(self._ids(ic.filter_incidents(
            state_id=states["REPORTADO"], query=self.prefix)), [iid])
        self.assertEqual(ic.filter_incidents(state_id=states["CERRADO"], query=self.prefix), [])
        by_state = ic.filter_incidents(state_id=states["CERRADO"])
        self.assertTrue(by_state and all(i["state_code"] == "CERRADO" for i in by_state))
        for bad in (True, "1", 1.0):
            for kwargs in ({"type_id": bad}, {"priority_id": bad}, {"state_id": bad}):
                with self.assertRaises(ic.IncidentValidationError):
                    ic.filter_incidents(**kwargs)

    def test_wildcards(self):
        special = self._create("S", description="100%_x\\y")["incident_id"]
        plain = self._create("P", description="abc")["incident_id"]
        for term in ("%", "_", "\\"):
            found = self._ids(ic.search_incidents(term))
            self.assertIn(special, found, term)
            self.assertNotIn(plain, found, term)

    def test_lookups(self):
        types = ic.list_incident_types()
        self.assertTrue(all(set(t) == {"type_id", "name", "description", "active"}
                            for t in types))
        self.assertTrue(all({"priority_id", "name", "level", "active"} <= set(p)
                            for p in ic.list_priorities()))
        self.assertEqual(len(ic.list_incident_states()), 6)
        inactive = self.temp_type(active=False)
        found = [t for t in ic.list_incident_types() if t["type_id"] == inactive]
        self.assertFalse(found[0]["active"])

    def test_create_valid_sets_state_and_user(self):
        incident = self._create(code=f"  {self.prefix}-A  ", reference="  ",
                                affected_people=None, latitude=None, longitude="")
        self.assertEqual(incident["code"], f"{self.prefix}-A")
        self.assertEqual(incident["state_code"], "REPORTADO")
        self.assertEqual(incident["registered_by_username"], "admin.demo")
        self.assertEqual(incident["affected_people"], 0)
        self.assertIsNone(incident["reference"])
        self.assertIsNone(incident["latitude"])
        self.assertIsNotNone(incident["reported_at"])
        self.assertEqual(ic.get_incident(incident["incident_id"]), incident)
        audit = self.audit_rows(incident["incident_id"])
        self.assertEqual(audit, [(self.user_id("admin.demo"), "CREAR", "INCIDENTE",
                                  incident["incident_id"], f"Registro inicial de {self.prefix}-A.")])
        session.end_session()
        _login("operador.central")
        other = self._create("B")
        self.assertEqual(other["registered_by_username"], "operador.central")
        self.assertEqual(self.audit_rows(other["incident_id"])[0][0],
                         self.user_id("operador.central"))

    def test_forbidden_keys_on_create_and_update(self):
        iid = self._create()["incident_id"]
        before = self.row(iid)
        cases = [(STATE_KEYS, "El estado del incidente no se puede modificar desde este módulo."),
                 (USER_KEYS, "El usuario registrador se toma de la sesión y no se puede indicar."),
                 (DATE_KEYS, "Las fechas y el motivo de cancelación no se pueden modificar "
                             "desde este módulo.")]
        for keys, message in cases:
            for key in keys:
                with self.assertRaises(ic.IncidentValidationError, msg=key) as ctx:
                    self._create("X", **{key: 1})
                self.assertEqual(str(ctx.exception), message)
                with self.assertRaises(ic.IncidentValidationError, msg=key) as ctx:
                    ic.update_incident(iid, self._edit(**{key: "REPORTADO"}))
                self.assertEqual(str(ctx.exception), message)
        for key in ("fecha_algo", "otro", "codigo"):
            with self.assertRaises(ic.IncidentValidationError, msg=key):
                self._create("X", **{key: 1})
        # el código no se puede cambiar al editar
        with self.assertRaises(ic.IncidentValidationError) as ctx:
            ic.update_incident(iid, self._data(code="OTRO"))
        self.assertIn("código", str(ctx.exception))
        with self.assertRaises(ic.IncidentValidationError):
            ic.update_incident(iid, self._edit(extra="x"))
        self.assertEqual(self.row(iid), before)
        self.assertEqual(self.count_prefix(), 1)
        self.assertEqual(self.count_audit_prefix(), 1)

    def test_duplicate_code(self):
        self._create()
        with self.assertRaises(ic.DuplicateIncidentCodeError) as ctx:
            self._create()
        self.assertEqual(str(ctx.exception), "Ya existe un incidente con ese código.")
        self.assertEqual(self.count_prefix(), 1)
        self.assertEqual(self.count_audit_prefix(), 1)

    def test_invalid_type_and_priority(self):
        for bad in (999999, 0, -1, "abc", None, True):
            with self.assertRaises(ic.IncidentValidationError, msg=repr(bad)):
                self._create("T", type_id=bad)
            with self.assertRaises(ic.IncidentValidationError, msg=repr(bad)):
                self._create("P", priority_id=bad)
        with self.assertRaises(ic.InvalidTypeError):
            self._create("T", type_id=999999)
        with self.assertRaises(ic.InvalidPriorityError):
            self._create("P", priority_id=999999)
        self.assertEqual(self.count_prefix(), 0)
        self.assertEqual(self.count_prefix(), 0)

    def test_inactive_catalogs(self):
        inactive_type = self.temp_type(active=False)
        inactive_priority = self.temp_priority(active=False)
        with self.assertRaises(ic.InactiveTypeError):
            self._create("T", type_id=inactive_type)
        with self.assertRaises(ic.InactivePriorityError):
            self._create("P", priority_id=inactive_priority)
        self.assertEqual(self.count_prefix(), 0)
        type_id = self.temp_type()
        priority_id = self.temp_priority()
        iid = self._create(type_id=type_id, priority_id=priority_id)["incident_id"]
        with self.assertRaises(ic.InactiveTypeError):
            ic.update_incident(iid, self._edit(type_id=inactive_type, priority_id=priority_id))
        with self.assertRaises(ic.InactivePriorityError):
            ic.update_incident(iid, self._edit(type_id=type_id, priority_id=inactive_priority))
        self.deactivate_type(type_id)
        self.deactivate_priority(priority_id)
        updated = ic.update_incident(iid, self._edit(
            type_id=type_id, priority_id=priority_id, address="Nueva dirección"))
        self.assertEqual(updated["address"], "Nueva dirección")
        self.assertEqual(updated["type_id"], type_id)
        self.assertIn(iid, self._ids(ic.search_incidents(self.prefix)))

    def test_coordinates(self):
        good = [("90", "180"), ("-90", "-180"), ("0", "0"), ("14.123456", "-90.123456"),
                (14.5, -90.25), (Decimal("1.5"), Decimal("2.5")), (" 10 ", " 20 ")]
        for index, (lat, lon) in enumerate(good):
            incident = self._create(f"G{index}", latitude=lat, longitude=lon)
            self.assertEqual(incident["latitude"], float(Decimal(str(lat).strip())))
            self.assertEqual(incident["longitude"], float(Decimal(str(lon).strip())))
        self.assertIsNone(self._create("N1", latitude=None, longitude=None)["latitude"])
        self.assertIsNone(self._create("N2", latitude="", longitude="  ")["longitude"])
        count = self.count_prefix()
        bad = [("90.000001", "0"), ("-91", "0"), ("0", "180.1"), ("0", "-181"),
               ("14.6", None), (None, "-90.5"), ("14.6", ""), ("", "-90.5"),
               ("abc", "1"), ("1", "abc"), ("14,5", "1"), ("nan", "1"), ("inf", "1"),
               ("1", "-inf"), ("1e2", "1"), ("1", "1e1"), (True, 1), (1, False),
               ("14.1234567", "1"), ("1", "-90.1234567"), (float("nan"), 1.0),
               (float("inf"), 1.0), ([1], 1), ("1_0", "1")]
        for lat, lon in bad:
            with self.assertRaises(ic.IncidentValidationError, msg=repr((lat, lon))):
                self._create("B", latitude=lat, longitude=lon)
        self.assertEqual(self.count_prefix(), count)
        with self.assertRaises(ic.IncidentValidationError) as ctx_lat:
            self._create("B", latitude="91", longitude="0")
        with self.assertRaises(ic.IncidentValidationError) as ctx_lon:
            self._create("B", latitude="0", longitude="181")
        self.assertNotEqual(str(ctx_lat.exception), str(ctx_lon.exception))

    def test_affected_people(self):
        self.assertEqual(self._create("Z", affected_people=0)["affected_people"], 0)
        self.assertEqual(self._create("P", affected_people=15)["affected_people"], 15)
        self.assertEqual(self._create("S", affected_people=" 8 ")["affected_people"], 8)
        self.assertEqual(self._create("E", affected_people="")["affected_people"], 0)
        self.assertEqual(self._create("M", affected_people=65535)["affected_people"], 65535)
        count = self.count_prefix()
        for bad in (-1, "-3", "abc", 1.5, "1.5", True, 65536, "65536", 2.0, [1]):
            with self.assertRaises(ic.IncidentValidationError, msg=repr(bad)):
                self._create("B", affected_people=bad)
        self.assertEqual(self.count_prefix(), count)

    def test_required_and_length(self):
        bad = [{"code": ""}, {"code": "  "}, {"code": None}, {"code": "x" * 26},
               {"address": ""}, {"address": None}, {"address": "x" * 201},
               {"reference": "x" * 201}, {"description": ""}, {"description": "  "},
               {"description": None}, {"description": 5}, {"address": 5}]
        for overrides in bad:
            with self.assertRaises(ic.IncidentValidationError, msg=str(overrides)):
                self._create("R", **overrides)
        with self.assertRaises(ic.IncidentValidationError):
            ic.create_incident(None)
        with self.assertRaises(ic.IncidentValidationError):
            ic.update_incident(1, ["x"])
        self.assertEqual(self.count_prefix(), 0)
        self._create("L", code=self.prefix + "x" * (25 - len(self.prefix)),
                     address="a" * 200, reference="r" * 200)

    def test_update_persists_and_keeps_machine_fields(self):
        iid = self._create()["incident_id"]
        before = self.fixed_row(iid)
        new_type, new_priority = self.temp_type(), self.temp_priority()
        updated = ic.update_incident(iid, self._edit(
            type_id=new_type, priority_id=new_priority, address=" Otra ", reference=None,
            latitude="1.5", longitude="2.5", description="Nueva", affected_people="9"))
        self.assertEqual((updated["type_id"], updated["priority_id"], updated["address"],
                          updated["reference"], updated["latitude"], updated["longitude"],
                          updated["description"], updated["affected_people"]),
                         (new_type, new_priority, "Otra", None, 1.5, 2.5, "Nueva", 9))
        self.assertEqual(updated["state_code"], "REPORTADO")
        self.assertEqual(updated["registered_by_username"], "admin.demo")
        self.assertEqual(self.fixed_row(iid), before)
        audit = self.audit_rows(iid)
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[1][1:4], ("MODIFICAR", "INCIDENTE", iid))
        self.assertTrue(audit[1][4].startswith("Campos modificados: "))
        self.assertNotIn("Nueva", audit[1][4])
        session.end_session()
        _login("operador.central")
        ic.update_incident(iid, self._edit(
            type_id=new_type, priority_id=new_priority, address="Solo dirección",
            latitude="1.5", longitude="2.5", description="Nueva", affected_people=9))
        last = self.audit_rows(iid)[-1]
        self.assertEqual(last[0], self.user_id("operador.central"))
        self.assertEqual(last[4], "Campos modificados: direccion, referencia.")
        # los datos inválidos no cambian nada
        snapshot = self.row(iid)
        with self.assertRaises(ic.IncidentValidationError):
            ic.update_incident(iid, self._edit(address=""))
        with self.assertRaises(ic.IncidentValidationError):
            ic.update_incident(iid, self._edit(latitude="5", longitude=None))
        self.assertEqual(self.row(iid), snapshot)

    def test_update_is_full_replace(self):
        # Comportamiento actual: update reemplaza todo el formulario. Omitir
        # affected_people lo reinicia a 0 y omitir reference lo deja en NULL.
        iid = self._create(affected_people=5)["incident_id"]
        data = self._edit()
        del data["affected_people"]
        del data["reference"]
        result = ic.update_incident(iid, data)
        self.assertEqual(result["affected_people"], 0)
        self.assertIsNone(result["reference"])
        # los campos obligatorios omitidos se rechazan
        for key in ("type_id", "priority_id", "address", "description"):
            partial = self._edit()
            del partial[key]
            with self.assertRaises(ic.IncidentValidationError, msg=key):
                ic.update_incident(iid, partial)

    def test_update_noop(self):
        iid = self._create()["incident_id"]
        before = self.row(iid)
        result = ic.update_incident(iid, self._edit())
        self.assertEqual(result["incident_id"], iid)
        self.assertEqual(self.row(iid), before)
        self.assertEqual(len(self.audit_rows(iid)), 1)

    def test_update_by_state(self):
        for state in ("REPORTADO", "ASIGNADO", "EN_ATENCION", "CONTROLADO"):
            iid = self.insert_incident(suffix=state, state_code=state)
            before = self.fixed_row(iid)
            result = ic.update_incident(iid, self._edit(address=f"Dir {state}"))
            self.assertEqual(result["address"], f"Dir {state}")
            self.assertEqual(result["state_code"], state)
            self.assertEqual(self.fixed_row(iid), before)
        for state in ("CERRADO", "CANCELADO"):
            iid = self.insert_incident(suffix=state, state_code=state)
            before = self.row(iid)
            with self.assertRaises(ic.IncidentStateError, msg=state) as ctx:
                ic.update_incident(iid, self._edit(address="No debe"))
            self.assertEqual(str(ctx.exception),
                             "Un incidente cerrado o cancelado no admite modificaciones.")
            self.assertEqual(self.row(iid), before)
            self.assertEqual(self.audit_rows(iid), [])

    def test_update_not_found_and_bad_id(self):
        with self.assertRaises(ic.IncidentNotFoundError):
            ic.update_incident(0, self._edit())
        with self.assertRaises(ic.IncidentNotFoundError):
            ic.get_incident(0)
        with self.assertRaises(ic.IncidentNotFoundError):
            ic.cancel_incident(0, "Motivo")
        for bad in (True, "1", None, 1.0):
            with self.assertRaises(ic.IncidentValidationError):
                ic.update_incident(bad, self._edit())
            with self.assertRaises(ic.IncidentValidationError):
                ic.get_incident(bad)
            with self.assertRaises(ic.IncidentValidationError):
                ic.cancel_incident(bad, "Motivo")

    def test_cancel_reported(self):
        iid = self._create()["incident_id"]
        result = ic.cancel_incident(iid, "  Reporte duplicado  ")
        self.assertEqual(result["state_code"], "CANCELADO")
        self.assertEqual(result["cancel_reason"], "Reporte duplicado")
        self.assertGreaterEqual(result["cancelled_at"], result["reported_at"])
        audit = self.audit_rows(iid)
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[1], (self.user_id("admin.demo"), "CAMBIO_ESTADO", "INCIDENTE",
                                    iid, "REPORTADO → CANCELADO. Motivo: Reporte duplicado"))
        with self.assertRaises(ic.IncidentStateError) as ctx:
            ic.cancel_incident(iid, "Otra vez")
        self.assertEqual(str(ctx.exception), "El incidente ya está cancelado.")
        with self.assertRaises(ic.IncidentStateError):
            ic.update_incident(iid, self._edit(address="Tras cancelar"))
        self.assertEqual(len(self.audit_rows(iid)), 2)
        self.assertEqual(ic.get_incident(iid)["cancel_reason"], "Reporte duplicado")

    def test_cancel_resolves_created_dispatch(self):
        iid = self.insert_incident("CD")
        created = self.add_dispatch(iid)
        result = ic.cancel_incident(iid, "Duplicado")
        self.assertEqual(result["state_code"], "CANCELADO")
        self.assertEqual(self.dispatch_state(created), "CANCELADO")
        self.assertEqual(len(self.dispatch_audit(created)), 1)
        active = self.insert_incident("CA")
        draft = self.add_dispatch(active)
        self.add_dispatch(active, "ACTIVO")
        with self.assertRaises(ic.IncidentStateError):
            ic.cancel_incident(active, "Duplicado")
        self.assertEqual(self.state_of(active), "REPORTADO")
        self.assertEqual(self.dispatch_state(draft), "CREADO")
        rollback = self.insert_incident("CR")
        pending = self.add_dispatch(rollback)
        with mock.patch.object(incident_model, "_audit",
                               side_effect=mysql.connector.Error("falla")):
            with self.assertRaises(ic.IncidentServiceError):
                ic.cancel_incident(rollback, "Duplicado")
        self.assertEqual(self.state_of(rollback), "REPORTADO")
        self.assertEqual(self.dispatch_state(pending), "CREADO")
        self.assertEqual(self.dispatch_audit(pending), [])
        session.end_session()
        _login("consulta.sur")
        with self.assertRaises(AuthorizationDenied):
            ic.cancel_incident(rollback, "Duplicado")
        self.assertEqual(self.dispatch_state(pending), "CREADO")

    def test_cancel_invalid_reason(self):
        iid = self._create()["incident_id"]
        before = self.row(iid)
        for bad in (None, "", "   ", 5, b"x", ["x"], "x" * 251):
            with self.assertRaises(ic.IncidentValidationError, msg=repr(bad)):
                ic.cancel_incident(iid, bad)
        self.assertEqual(self.row(iid), before)
        self.assertEqual(len(self.audit_rows(iid)), 1)
        self.assertEqual(ic.cancel_incident(iid, "x" * 250)["state_code"], "CANCELADO")

    def test_cancel_other_states_rejected(self):
        for state in ("ASIGNADO", "EN_ATENCION", "CONTROLADO", "CERRADO", "CANCELADO"):
            iid = self.insert_incident(suffix=state, state_code=state)
            before = self.row(iid)
            with self.assertRaises(ic.IncidentStateError, msg=state):
                ic.cancel_incident(iid, "Motivo")
            self.assertEqual(self.row(iid), before)
            self.assertEqual(self.audit_rows(iid), [])

    def test_atomic_rollback(self):
        iid = self._create()["incident_id"]
        before = self.row(iid)
        boom = mysql.connector.Error("falla simulada")
        with mock.patch.object(incident_model, "_audit", side_effect=boom):
            with self.assertRaises(ic.IncidentServiceError) as ctx:
                self._create("N")
            self.assertNotIn("SELECT", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, mysql.connector.Error)
            with self.assertRaises(ic.IncidentServiceError):
                ic.update_incident(iid, self._edit(address="Cambio"))
            with self.assertRaises(ic.IncidentServiceError):
                ic.cancel_incident(iid, "Motivo")
        self.assertEqual(self.count_prefix(), 1)
        self.assertEqual(self.row(iid), before)
        self.assertEqual(self.state_of(iid), "REPORTADO")
        self.assertEqual(len(self.audit_rows(iid)), 1)
        self.assertEqual(self.count_audit_prefix(), 1)

    def test_technical_error_on_reads(self):
        with mock.patch.object(incident_model, "search_incidents",
                               side_effect=mysql.connector.Error("falla")):
            with self.assertRaises(ic.IncidentServiceError):
                ic.list_incidents()

    def test_session_user_is_used_not_payload(self):
        session.end_session()
        _login("operador.central")
        incident = self._create()
        self.assertEqual(incident["registered_by_id"], self.user_id("operador.central"))
        with self.assertRaises(ic.IncidentValidationError):
            self._create("B", user_id=self.user_id("admin.demo"))
        self.assertEqual(self.count_prefix(), 1)

    def test_authorization_by_role(self):
        for username in ("admin.demo", "operador.central"):
            session.end_session()
            _login(username)
            created = self._create(username.split(".")[0])
            iid = created["incident_id"]
            self.assertEqual(ic.get_incident(iid)["incident_id"], iid)
            ic.update_incident(iid, self._edit(address=f"Dir {username}"))
            self.assertEqual(ic.cancel_incident(iid, "Prueba")["state_code"], "CANCELADO")
        target = self._create("T")["incident_id"]
        before = self.row(target)
        total = self.count_prefix()
        audit_total = self.count_audit_prefix()
        session.end_session()
        _login("consulta.sur")
        # lecturas permitidas
        self.assertIsInstance(ic.list_incidents(), list)
        self.assertIsInstance(ic.search_incidents(self.prefix), list)
        self.assertIsInstance(ic.filter_incidents(type_id=self.seed_type), list)
        self.assertEqual(ic.get_incident(target)["incident_id"], target)
        self.assertTrue(ic.list_incident_types())
        self.assertTrue(ic.list_priorities())
        self.assertTrue(ic.list_incident_states())
        # escrituras denegadas
        with self.assertRaises(AuthorizationDenied):
            self._create("D")
        with self.assertRaises(AuthorizationDenied):
            ic.update_incident(target, self._edit(address="Hackeo"))
        with self.assertRaises(AuthorizationDenied):
            ic.cancel_incident(target, "Hackeo")
        with self.assertRaises(AuthorizationDenied):
            ic.create_incident({"state": "CERRADO"})
        self.assertEqual(self.row(target), before)
        self.assertEqual(self.count_prefix(), total)
        self.assertEqual(self.count_audit_prefix(), audit_total)

    def test_no_session(self):
        target = self._create()["incident_id"]
        before = self.row(target)
        session.end_session()
        calls = [
            lambda: ic.list_incidents(), lambda: ic.search_incidents("x"),
            lambda: ic.filter_incidents(), lambda: ic.get_incident(target),
            lambda: ic.list_incident_types(), lambda: ic.list_priorities(),
            lambda: ic.list_incident_states(), lambda: self._create("N"),
            lambda: ic.update_incident(target, self._edit(address="x")),
            lambda: ic.cancel_incident(target, "x"),
        ]
        for call in calls:
            with self.assertRaises(NotAuthenticated):
                call()
        self.assertEqual(self.row(target), before)
        self.assertEqual(self.count_prefix(), 1)

    def test_no_delete_or_set_state_api(self):
        for name in ("delete_incident", "set_incident_state", "change_state"):
            self.assertFalse(hasattr(ic, name), name)


if __name__ == "__main__":
    unittest.main()

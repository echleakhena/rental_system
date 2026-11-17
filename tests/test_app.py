import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from rpms import create_app
from rpms.models import MODELS, db
from rpms.seed import create_admin
from rpms.services import activate, issue_invoice, pay_invoice


class RpmTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SECRET_KEY": "test",
            }
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.u = create_admin("test@example.com", "test-password")
        p = MODELS["properties"](property_code="P", name="Test")
        db.session.add(p)
        db.session.flush()
        f = MODELS["floors"](property_id=p.id, floor_number=1)
        db.session.add(f)
        db.session.flush()
        self.room = MODELS["rooms"](floor_id=f.id, room_number="101", status="Vacant")
        db.session.add(self.room)
        t = MODELS["tenants"](tenant_code="T", full_name="Test")
        db.session.add(t)
        db.session.flush()
        self.t = t
        self.lease = self.lease_for("L")
        db.session.commit()
        self.client = self.app.test_client()

    def lease_for(self, no):
        lease = MODELS["rental_contracts"](
            contract_no=no,
            room_id=self.room.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent=Decimal("150"),
            deposit_required=150,
            status="Draft",
            payment_due_day=5,
        )
        db.session.add(lease)
        db.session.flush()
        db.session.add(
            MODELS["contract_tenants"](
                contract_id=lease.id, tenant_id=self.t.id, is_primary=True
            )
        )
        db.session.flush()
        return lease

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def auth(self):
        with self.client.session_transaction() as s:
            s["uid"] = self.u.id
            s["csrf"] = "test"

    def test_contract_uses_reservation_room_and_customer(self):
        self.auth()
        booking = MODELS["reservations"](
            room_id=self.room.id, tenant_id=self.t.id, status="Confirmed"
        )
        db.session.add(booking)
        db.session.commit()
        html = self.client.get("/records/rental_contracts/new").get_data(as_text=True)
        self.assertNotIn('name="room_id"', html)
        payload = dict(
            _csrf="test",
            reservation_id=str(booking.id),
            room_id="99999",
            start_date="2026-01-01",
            end_date="2026-12-31",
            monthly_rent="150",
            deposit_required="150",
            billing_day="1",
            payment_due_day="5",
            status="Draft",
        )
        response = self.client.post("/records/rental_contracts/new", data=payload)
        self.assertEqual(response.status_code, 302)
        contract = db.session.scalar(
            select(MODELS["rental_contracts"]).where(
                MODELS["rental_contracts"].reservation_id == booking.id
            )
        )
        self.assertEqual(contract.room_id, self.room.id)
        link = db.session.scalar(
            select(MODELS["contract_tenants"]).where(
                MODELS["contract_tenants"].contract_id == contract.id
            )
        )
        self.assertEqual(link.tenant_id, self.t.id)
        self.assertTrue(link.is_primary)
        payload["reservation_id"] = ""
        self.assertEqual(
            self.client.post("/records/rental_contracts/new", data=payload).status_code,
            200,
        )

    def test_auth_csrf_and_roles(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.post("/login", data={}).status_code, 400)
        self.auth()
        role = MODELS["roles"](name="Cashier", is_active=True)
        db.session.add(role)
        db.session.flush()
        self.u.role_id = role.id
        db.session.commit()
        self.assertEqual(self.client.get("/records/rooms/new").status_code, 403)

    def test_administration_crud_and_json_settings(self):
        self.auth()
        for table, payload in (
            ("roles", dict(name="Custom role", is_active="true")),
            (
                "permissions",
                dict(code="custom.read", name="Read custom", module="Custom"),
            ),
            (
                "system_settings",
                dict(setting_key="custom", setting_value='{"enabled": true}'),
            ),
        ):
            with self.subTest(table=table):
                html = self.client.get(f"/records/{table}").get_data(as_text=True)
                self.assertIn(f"/records/{table}/new", html)
                response = self.client.post(
                    f"/records/{table}/new", data={"_csrf": "test", **payload}
                )
                self.assertEqual(response.status_code, 302)
                row = db.session.scalar(
                    select(MODELS[table]).order_by(MODELS[table].id.desc())
                )
                record_id = row.id
                if table == "system_settings":
                    self.assertEqual(row.setting_value, {"enabled": True})
                response = self.client.get(f"/records/{table}/{record_id}/edit")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    self.client.post(
                        f"/records/{table}/{record_id}/edit",
                        data={"_csrf": "test", **payload},
                    ).status_code,
                    302,
                )
                self.assertEqual(
                    self.client.post(
                        f"/records/{table}/{record_id}/delete", data={"_csrf": "test"}
                    ).status_code,
                    302,
                )
                self.assertIsNone(db.session.get(MODELS[table], record_id))

    def test_user_password_and_admin_access(self):
        from werkzeug.security import check_password_hash

        self.auth()
        payload = dict(
            _csrf="test",
            role_id=str(self.u.role_id),
            name="New user",
            email="NEW@example.com",
            password="new-password",
            is_active="true",
        )
        self.assertEqual(
            self.client.post("/records/users/new", data=payload).status_code, 302
        )
        account = db.session.scalar(
            select(MODELS["users"]).where(MODELS["users"].email == "new@example.com")
        )
        self.assertTrue(check_password_hash(account.password_hash, "new-password"))
        payload["password"] = ""
        self.assertEqual(
            self.client.post(
                f"/records/users/{account.id}/edit", data=payload
            ).status_code,
            302,
        )
        self.assertTrue(check_password_hash(account.password_hash, "new-password"))
        manager = MODELS["roles"](name="Manager", is_active=True)
        db.session.add(manager)
        db.session.flush()
        self.u.role_id = manager.id
        db.session.commit()
        for table in (
            "users",
            "roles",
            "permissions",
            "role_permissions",
            "system_settings",
        ):
            self.assertEqual(self.client.get(f"/records/{table}/new").status_code, 403)
            self.assertEqual(
                self.client.post(
                    f"/records/{table}/new", data={"_csrf": "test"}
                ).status_code,
                403,
            )
        self.assertEqual(self.client.get("/records/audit_logs/new").status_code, 403)

    def test_role_permission_crud(self):
        self.auth()
        permission = MODELS["permissions"](code="test.read", name="Read", module="Test")
        db.session.add(permission)
        db.session.commit()
        payload = dict(
            _csrf="test", role_id=str(self.u.role_id), permission_id=str(permission.id)
        )
        self.assertEqual(
            self.client.post("/records/role_permissions/new", data=payload).status_code,
            302,
        )
        link = db.session.scalar(select(MODELS["role_permissions"]))
        self.assertEqual(
            self.client.post(
                f"/records/role_permissions/{link.id}/edit", data=payload
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/records/role_permissions/{link.id}/delete", data={"_csrf": "test"}
            ).status_code,
            302,
        )

    def test_draft_renewal_and_move_out_actions(self):
        self.auth()
        for table, values in (
            (
                "contract_renewals",
                dict(
                    new_end_date="2027-12-31", new_rent="175", renewal_date="2026-11-10"
                ),
            ),
            ("move_outs", dict(notice_date="2026-11-10", move_out_date="2026-12-31")),
        ):
            payload = dict(
                _csrf="test", contract_id=str(self.lease.id), status="Draft", **values
            )
            self.assertEqual(
                self.client.post(f"/records/{table}/new", data=payload).status_code, 302
            )
            row = db.session.scalar(select(MODELS[table]))
            if table == "contract_renewals":
                self.assertEqual(row.previous_end_date, self.lease.end_date)
                self.assertEqual(row.previous_rent, self.lease.monthly_rent)
            self.assertEqual(
                self.client.post(
                    f"/records/{table}/{row.id}/edit", data=payload
                ).status_code,
                302,
            )
            row.status = "Completed"
            db.session.commit()
            response = self.client.post(
                f"/records/{table}/{row.id}/delete", data={"_csrf": "test"}
            )
            self.assertEqual(response.status_code, 302)
            self.assertIsNotNone(db.session.get(MODELS[table], row.id))
            row.status = "Draft"
            db.session.commit()
            record_id = row.id
            self.client.post(
                f"/records/{table}/{record_id}/delete", data={"_csrf": "test"}
            )
            self.assertIsNone(db.session.get(MODELS[table], record_id))

    def test_lease_overlap(self):
        activate(self.lease, self.u.id)
        db.session.flush()
        other = self.lease_for("L2")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            activate(other, self.u.id)

    def test_monthly_invoice_and_partial_payment(self):
        activate(self.lease, self.u.id)
        db.session.flush()
        inv = issue_invoice(self.lease, "2026-11", self.u.id)
        db.session.flush()
        with self.assertRaisesRegex(ValueError, "already"):
            issue_invoice(self.lease, "2026-11", self.u.id)
        pay_invoice(inv.id, "100", "Cash", "TEST", self.u.id)
        db.session.flush()
        self.assertEqual(inv.balance_due, Decimal("50.00"))
        self.assertEqual(inv.status, "Partial")
        with self.assertRaises(ValueError):
            pay_invoice(inv.id, "51", "Cash", "", self.u.id)
        with self.assertRaises(ValueError):
            pay_invoice(inv.id, "NaN", "Cash", "", self.u.id)
        pay_invoice(inv.id, "50", "Cash", "FINAL", self.u.id)
        db.session.commit()
        self.assertEqual(inv.status, "Paid")
        self.assertEqual(
            db.session.scalar(
                select(db.func.sum(MODELS["payment_allocations"].allocated_amount))
            ),
            Decimal("150"),
        )

    def test_all_pages_and_forms_render(self):
        self.auth()
        for path in ["/", "/simulator", "/reports", "/mis-dss"] + [
            "/records/" + t for t in MODELS
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        from rpms.record_config import EDITABLE

        for t in EDITABLE:
            with self.subTest(form=t):
                self.assertEqual(
                    self.client.get("/records/" + t + "/new").status_code, 200
                )

    def test_utility_billing(self):
        activate(self.lease, self.u.id)
        db.session.flush()
        meter = MODELS["utility_meters"](room_id=self.room.id, meter_type="Electricity")
        db.session.add(meter)
        db.session.flush()
        reading = MODELS["meter_readings"](
            meter_id=meter.id,
            contract_id=self.lease.id,
            reading_date=date(2026, 11, 1),
            previous_reading=1200,
            current_reading=1350,
            usage_quantity=150,
            unit_rate=Decimal(".25"),
            total_amount=Decimal("37.50"),
            status="Approved",
        )
        db.session.add(reading)
        db.session.flush()
        inv = issue_invoice(self.lease, "2026-11", self.u.id)
        db.session.flush()
        self.assertEqual(inv.total_amount, Decimal("187.50"))
        self.assertEqual(reading.status, "Billed")

    def test_dashboard_customer_payment_summary(self):
        from rpms.reporting import dashboard_reports

        activate(self.lease, self.u.id)
        db.session.flush()
        inv = issue_invoice(self.lease, "2026-11", self.u.id)
        db.session.flush()

        def report():
            return dashboard_reports([self.room], [inv])

        self.assertEqual(report()["customers"][0]["status"], "Unpaid")
        pay_invoice(inv.id, "100", "Cash", "", self.u.id)
        db.session.flush()
        data = report()
        self.assertEqual(data["customers"][0]["status"], "Partially paid")
        self.assertEqual(data["charts"][1]["total"], Decimal("150"))
        self.assertEqual(data["customers"][0]["due"], Decimal("50"))
        pay_invoice(inv.id, "50", "Cash", "", self.u.id)
        db.session.flush()
        self.assertEqual(report()["customers"][0]["status"], "Paid")
        # The same customer with a second invoice still counts only once.
        second = issue_invoice(self.lease, "2026-12", self.u.id)
        db.session.flush()
        data = dashboard_reports([self.room], [inv, second])
        self.assertEqual(len(data["customers"]), 1)
        self.assertEqual(data["customers"][0]["status"], "Partially paid")
        inv.status = "Voided"
        second.status = "Draft"
        self.assertEqual(dashboard_reports([], [inv, second])["customers"], [])

    def test_management_recommendations(self):
        from datetime import timedelta
        from types import SimpleNamespace as Row

        from rpms.reporting import management_actions

        today = date(2026, 11, 3)
        invoices = [
            Row(
                status="Partial",
                balance_due=Decimal("50"),
                due_date=today - timedelta(days=1),
            ),
            Row(
                status="Voided",
                balance_due=Decimal("999"),
                due_date=today - timedelta(days=1),
            ),
        ]
        rooms = [
            Row(status="Vacant", monthly_rent=Decimal("150")),
            Row(status="Occupied", monthly_rent=Decimal("300")),
        ]
        requests = [
            Row(status="Pending", priority="Urgent"),
            Row(status="Completed", priority="Urgent"),
        ]
        contracts = [
            Row(status="Active", end_date=today + timedelta(days=30)),
            Row(status="Active", end_date=today + timedelta(days=31)),
        ]
        data = management_actions(rooms, invoices, requests, contracts, today)
        self.assertEqual(data["overdue_count"], 1)
        self.assertEqual(data["vacancy_opportunity"], Decimal("150"))
        actions = {a["title"]: a for a in data["actions"]}
        self.assertIn(
            "1 active lease(s)", actions["Plan upcoming lease endings"]["evidence"]
        )
        self.assertIn(
            "1 open request(s)", actions["Review maintenance queue"]["evidence"]
        )
        self.assertEqual(data["actions"][0]["priority"], "High")
        self.assertIn("$50.00", actions["Follow up overdue invoices"]["evidence"])
        empty = management_actions([], [], [], [], today)
        self.assertTrue(all(a["priority"] == "Setup" for a in empty["actions"]))

    def test_excel_export(self):
        from io import BytesIO

        from openpyxl import load_workbook

        self.assertEqual(self.client.get("/reports/export.xlsx").status_code, 302)
        self.auth()
        self.t.full_name = "=1+1"
        activate(self.lease, self.u.id)
        db.session.flush()
        inv = issue_invoice(self.lease, "2026-11", self.u.id)
        db.session.flush()
        pay_invoice(inv.id, "100", "Cash", "", self.u.id)
        db.session.commit()
        response = self.client.get("/reports/export.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        workbook = load_workbook(BytesIO(response.data))
        sheet = workbook["Customer payments"]
        self.assertEqual(sheet["A2"].value, "=1+1")
        self.assertEqual(sheet["A2"].data_type, "s")
        self.assertEqual(sheet["B2"].value, 100)
        self.assertEqual(sheet["C2"].value, 50)
        self.assertEqual(sheet["D2"].value, "Partially paid")
        self.assertEqual(sheet["B3"].value, 100)
        self.assertEqual(sheet.freeze_panes, "A2")
        workbook.close()
        response.close()

    def test_room_photo_upload_and_view(self):
        from io import BytesIO
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from PIL import Image

        def photo():
            output = BytesIO()
            Image.new("RGB", (40, 30), "green").save(output, "PNG")
            output.seek(0)
            return output

        path = f"/rooms/{self.room.id}"
        self.assertEqual(self.client.get(path).status_code, 302)
        self.auth()
        original_path = self.app.instance_path
        with TemporaryDirectory() as directory:
            self.app.instance_path = directory
            try:
                self.assertEqual(self.client.get(path).status_code, 200)
                result = self.client.post(
                    path,
                    data={
                        "_csrf": "test",
                        **{f"photo_{i}": (photo(), f"room{i}.png") for i in range(4)},
                    },
                )
                self.assertEqual(result.status_code, 302)
                db.session.refresh(self.room)
                self.assertEqual(len([p for p in self.room.photos if p]), 4)
                image = self.client.get(path + "/photos/0")
                self.assertEqual(image.status_code, 200)
                self.assertEqual(image.mimetype, "image/jpeg")
                image.close()
                with self.client.get(path + "/photos/3") as toilet:
                    self.assertEqual(toilet.status_code, 200)
                before = list(self.room.photos)
                self.client.post(
                    path,
                    data={
                        "_csrf": "test",
                        "photo_0": (photo(), "valid.png"),
                        "photo_1": (BytesIO(b"not an image"), "fake.jpg"),
                    },
                )
                db.session.refresh(self.room)
                self.assertEqual(self.room.photos, before)
                self.client.post(
                    path,
                    data={
                        "_csrf": "test",
                        "photo_0": (photo(), "replacement.png"),
                        "remove_1": "yes",
                    },
                )
                db.session.refresh(self.room)
                self.assertNotEqual(self.room.photos[0], before[0])
                self.assertIsNone(self.room.photos[1])
                self.assertEqual(self.client.get(path + "/photos/1").status_code, 404)
                self.assertEqual(
                    len(list((Path(directory) / "room_photos").glob("*.jpg"))), 3
                )
                role = MODELS["roles"](name="Viewer", is_active=True)
                db.session.add(role)
                db.session.flush()
                self.u.role_id = role.id
                db.session.commit()
                self.assertEqual(self.client.get(path).status_code, 200)
                self.assertEqual(
                    self.client.post(
                        path, data={"_csrf": "test", "remove_0": "yes"}
                    ).status_code,
                    403,
                )
                with self.client.session_transaction() as session:
                    session.clear()
                self.assertEqual(self.client.get(path + "/photos/0").status_code, 302)
            finally:
                self.app.instance_path = original_path

    def test_record_actions_and_delete_protection(self):
        self.auth()
        self.assertEqual(
            self.client.get(f"/records/tenants/{self.t.id}").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/records/rooms/{self.room.id}/edit").status_code, 200
        )
        # Referenced tenant survives both the confirmation page and POST.
        path = f"/records/tenants/{self.t.id}/delete"
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertIsNotNone(db.session.get(MODELS["tenants"], self.t.id))
        self.client.post(path, data={"_csrf": "test"})
        self.assertIsNotNone(db.session.get(MODELS["tenants"], self.t.id))
        tenant = MODELS["tenants"](
            full_name="Disposable", tenant_code="DELETE-ME", status="Active"
        )
        db.session.add(tenant)
        db.session.commit()
        tid = tenant.id
        path = f"/records/tenants/{tid}/delete"
        self.assertEqual(self.client.post(path, data={}).status_code, 400)
        self.assertEqual(
            self.client.post(path, data={"_csrf": "test"}).status_code, 302
        )
        self.assertIsNone(db.session.get(MODELS["tenants"], tid))
        activate(self.lease, self.u.id)
        db.session.flush()
        inv = issue_invoice(self.lease, "2026-11", self.u.id)
        db.session.commit()
        self.client.post(f"/records/invoices/{inv.id}/delete", data={"_csrf": "test"})
        self.assertIsNotNone(db.session.get(MODELS["invoices"], inv.id))
        self.assertEqual(
            self.client.get(f"/records/invoices/{inv.id}/edit").status_code, 403
        )
        self.client.post(
            f"/records/rental_contracts/{self.lease.id}/delete", data={"_csrf": "test"}
        )
        self.assertIsNotNone(db.session.get(MODELS["rental_contracts"], self.lease.id))

    def test_leasing_record_summaries(self):
        from rpms.reporting import record_summary

        db.session.add(
            MODELS["tenants"](full_name="Inactive person", status="Inactive")
        )
        db.session.add(
            MODELS["reservations"](
                status="Confirmed", room_id=self.room.id, tenant_id=self.t.id
            )
        )
        db.session.flush()

        def counts(table):
            return {card["label"]: card["count"] for card in record_summary(table)}

        self.assertEqual(counts("tenants")["Total tenants"], 2)
        self.assertEqual(counts("tenants")["Inactive"], 1)
        self.assertEqual(counts("reservations")["Confirmed"], 1)
        self.assertEqual(counts("reservations")["Pending"], 0)
        self.assertEqual(counts("rental_contracts")["Draft"], 1)
        self.assertEqual(counts("contract_tenants")["Primary tenants"], 1)
        self.assertEqual(counts("contract_tenants")["Unique tenants"], 1)
        self.assertIsNone(record_summary("payments"))
        self.auth()
        for table in [
            "tenants",
            "reservations",
            "rental_contracts",
            "contract_tenants",
        ]:
            response = self.client.get("/records/" + table + "?q=NO-MATCH")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"room-summary-card", response.data)
            self.assertIn(b"totals are independent of search results", response.data)

    def test_empty_dashboard_charts(self):
        from rpms.reporting import dashboard_reports

        data = dashboard_reports([], [])
        for chart in data["charts"]:
            self.assertEqual(chart["total"], 0)
            self.assertEqual(chart["gradient"], "#e8ede9")

    def test_automatic_references_hidden_and_preserved(self):
        from rpms.identifiers import AUTO_REFERENCES
        from rpms.record_config import EDITABLE

        self.auth()
        for table, (field, prefix) in AUTO_REFERENCES.items():
            if table in EDITABLE:
                html = self.client.get("/records/" + table + "/new").get_data(
                    as_text=True
                )
                self.assertNotIn('name="' + field + '"', html)
                self.assertNotIn('name="id"', html)
        # No code required; submitted codes cannot override server generation.
        for name in ["First", "Second"]:
            response = self.client.post(
                "/records/properties/new",
                data={
                    "_csrf": "test",
                    "name": name,
                    "address": "Street",
                    "status": "Active",
                    "property_code": "FORGED",
                },
            )
            self.assertEqual(response.status_code, 302)
        rows = db.session.scalars(
            select(MODELS["properties"]).where(
                MODELS["properties"].name.in_(["First", "Second"])
            )
        ).all()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0].property_code, rows[1].property_code)
        for row in rows:
            self.assertTrue(row.property_code.startswith("PROP-"))
            self.assertLessEqual(len(row.property_code), 30)
        original = rows[0].property_code
        html = self.client.get(f"/records/properties/{rows[0].id}/edit").get_data(
            as_text=True
        )
        self.assertNotIn('name="property_code"', html)
        response = self.client.post(
            f"/records/properties/{rows[0].id}/edit",
            data={
                "_csrf": "test",
                "name": "Updated",
                "address": "Street",
                "status": "Active",
                "property_code": "CHANGED",
            },
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(rows[0])
        self.assertEqual(rows[0].property_code, original)

    def test_tenant_form_persists(self):
        self.auth()
        response = self.client.post(
            "/records/tenants/new",
            data={
                "_csrf": "test",
                "tenant_code": "T2",
                "full_name": "New Tenant",
                "phone": "012345678",
                "gender": "Female",
                "password": "customer-password",
                "status": "Active",
            },
        )
        self.assertEqual(response.status_code, 302)
        tenant = db.session.scalar(
            select(MODELS["tenants"]).where(MODELS["tenants"].full_name == "New Tenant")
        )
        self.assertIsNotNone(tenant)
        self.assertTrue(tenant.tenant_code.startswith("TEN-"))
        self.assertNotEqual(tenant.tenant_code, "T2")


class DatabaseConfigTests(unittest.TestCase):
    def test_private_postgres_settings_and_env_override(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from rpms.database_config import database_uri

        with (
            TemporaryDirectory() as directory,
            patch.dict("os.environ", {}, clear=True),
        ):
            self.assertEqual(database_uri(directory), "sqlite:///rpms.db")
            Path(directory, "database.json").write_text(
                json.dumps(
                    {
                        "database": "rental_system",
                        "username": "postgres",
                        "password": "special@:/pass",
                        "host": "127.0.0.1",
                        "port": 5432,
                    }
                )
            )
            url = database_uri(directory)
            self.assertEqual(url.database, "rental_system")
            self.assertEqual(url.password, "special@:/pass")
            self.assertEqual(url.drivername, "postgresql+psycopg")
            Path(directory, "database.json").write_text(
                json.dumps({"driver": "sqlite", "database": "rpms.db"})
            )
            sqlite_url = database_uri(directory)
            self.assertEqual(sqlite_url.drivername, "sqlite")
            self.assertEqual(sqlite_url.database, str(Path(directory) / "rpms.db"))
            with patch.dict("os.environ", {"DATABASE_URL": "sqlite:///:memory:"}):
                self.assertEqual(database_uri(directory), "sqlite:///:memory:")


if __name__ == "__main__":
    unittest.main()

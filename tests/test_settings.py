"""Rate snapshots and server-side enforcement of configured staff permissions."""

import unittest
from datetime import date, timedelta
from decimal import Decimal

import test_customer as customer_fixtures

from rpms.models import MODELS as M
from rpms.models import db


class SettingsTests(unittest.TestCase):
    setUp = customer_fixtures.CustomerTests.setUp
    tearDown = customer_fixtures.CustomerTests.tearDown
    csrf = customer_fixtures.CustomerTests.csrf

    def staff_for(self, name):
        role = M["roles"](name=name, is_active=True)
        db.session.add(role)
        db.session.flush()
        person = M["users"](
            name=name,
            email=name.lower().replace(" ", "") + "@example.com",
            role_id=role.id,
            is_active=True,
            password_hash="unused",
        )
        db.session.add(person)
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(uid=person.id, csrf="test")
        return role, client

    def grant(self, role, codes):
        return self.staff.post(
            "/settings",
            data={
                "_csrf": "test",
                "action": "role_permissions",
                "role_id": role.id,
                "permissions": codes,
            },
        )

    def test_rate_changes_preserve_existing_readings(self):
        meter = M["utility_meters"](
            room_id=self.room.id,
            meter_type="Water",
            meter_number="W01",
            unit="m³",
            default_rate=1,
            status="Active",
        )
        db.session.add(meter)
        db.session.flush()
        reading = M["meter_readings"](
            meter_id=meter.id,
            reading_date=date.today() - timedelta(days=1),
            previous_reading=0,
            current_reading=10,
            usage_quantity=10,
            unit_rate=1,
            total_amount=10,
            status="Approved",
        )
        db.session.add(reading)
        db.session.commit()
        # The generic meter editor is protected once readings exist; rates have a dedicated workflow.
        self.assertEqual(self.staff.get("/settings").status_code, 200)
        self.assertEqual(
            self.staff.post(
                "/settings",
                data=dict(
                    _csrf="test",
                    action="utility_rate",
                    meter_id=meter.id,
                    rate="0.2500",
                ),
            ).status_code,
            302,
        )
        db.session.refresh(meter)
        db.session.refresh(reading)
        self.assertEqual(meter.default_rate, Decimal("0.2500"))
        self.assertEqual(reading.unit_rate, 1)
        self.assertEqual(reading.total_amount, 10)
        for bad in ["NaN", "Infinity", "-1", "100000000", "0.00001"]:
            self.staff.post(
                "/settings",
                data=dict(
                    _csrf="test", action="utility_rate", meter_id=meter.id, rate=bad
                ),
            )
            db.session.refresh(meter)
            self.assertEqual(meter.default_rate, Decimal("0.2500"))

    def test_grant_revoke_and_enforce_module_permissions(self):
        role, client = self.staff_for("Utility team")
        self.assertEqual(client.get("/settings").status_code, 403)
        self.assertEqual(self.grant(role, ["utilities.manage"]).status_code, 302)
        self.assertEqual(client.get("/settings").status_code, 200)
        self.assertEqual(client.get("/records/utility_meters/new").status_code, 200)
        self.assertEqual(client.get("/records/rooms/new").status_code, 403)
        self.assertEqual(client.get("/records/users/new").status_code, 403)
        self.assertEqual(
            client.post("/contracts/1/invoice", data={"_csrf": "test"}).status_code, 403
        )
        self.assertEqual(
            client.post(
                "/customer-management",
                data=dict(_csrf="test", action="confirm_payment", record_id=1),
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/customer-management",
                data=dict(_csrf="test", action="confirm_booking", record_id=1),
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/settings",
                data=dict(
                    _csrf="test",
                    action="role_permissions",
                    role_id=role.id,
                    permissions=["billing.manage"],
                ),
            ).status_code,
            403,
        )
        self.assertEqual(self.grant(role, []).status_code, 302)
        self.assertEqual(client.get("/settings").status_code, 403)
        self.assertEqual(client.get("/records/utility_meters/new").status_code, 403)

    def test_manager_can_set_rates_but_cannot_change_staff_access(self):
        role, client = self.staff_for("Manager")
        html = client.get("/settings").get_data(as_text=True)
        self.assertIn("Room utility prices", html)
        self.assertNotIn("Role permissions", html)
        self.assertEqual(
            client.post(
                "/settings",
                data=dict(_csrf="test", action="role_permissions", role_id=role.id),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.staff.post(
                "/settings",
                data=dict(
                    _csrf="test", action="role_permissions", role_id=self.admin.role_id
                ),
            ).status_code,
            302,
        )
        self.assertEqual(db.session.query(M["role_permissions"]).count(), 0)
        self.assertEqual(self.staff.post("/settings", data={}).status_code, 400)
        self.assertEqual(self.app.test_client().get("/settings").status_code, 302)

    def test_billing_only_staff_can_view_private_payment_evidence(self):
        from test_customer import photo
        from werkzeug.security import generate_password_hash

        from rpms.customer import store_image
        from rpms.customer_models import CustomerAccount, PaymentProof

        role, client = self.staff_for("Cashier")
        self.grant(role, ["billing.manage"])
        tenant = M["tenants"](full_name="Resident", status="Active")
        db.session.add(tenant)
        db.session.flush()
        a = CustomerAccount(
            tenant_id=tenant.id,
            phone="+85512345678",
            password_hash=generate_password_hash("password123"),
        )
        db.session.add(a)
        db.session.flush()
        invoice = M["invoices"](
            total_amount=50, balance_due=50, paid_amount=0, status="Issued"
        )
        db.session.add(invoice)
        db.session.flush()
        from werkzeug.datastructures import FileStorage

        stream, name = photo()
        filename = store_image(FileStorage(stream=stream, filename=name))
        proof = PaymentProof(
            customer_id=a.id,
            invoice_id=invoice.id,
            amount=50,
            reference="REF1",
            photo_path=filename,
            status="Pending",
        )
        db.session.add(proof)
        db.session.commit()
        with client.get(f"/customer/evidence/payment/{proof.id}") as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/records/rooms/new").status_code, 403)
        self.assertEqual(client.get("/customer-management").status_code, 200)

    def test_settings_submenus_have_separate_pages(self):
        utilities = self.staff.get("/settings/utilities")
        permissions = self.staff.get("/settings/permissions")
        self.assertEqual(utilities.status_code, 200)
        self.assertEqual(permissions.status_code, 200)
        self.assertIn(b'id="utility-prices"', utilities.data)
        self.assertNotIn(b'id="role-permissions"', utilities.data)
        self.assertIn(b'id="role-permissions"', permissions.data)
        self.assertNotIn(b'id="utility-prices"', permissions.data)
        role, client = self.staff_for("Utilities only")
        self.grant(role, ["utilities.manage"])
        self.assertEqual(client.get("/settings/utilities").status_code, 200)
        self.assertEqual(client.get("/settings/permissions").status_code, 403)
        self.assertEqual(
            self.staff.post(
                "/settings/utilities",
                data=dict(_csrf="test", action="role_permissions", role_id=role.id),
            ).status_code,
            400,
        )
        response = self.staff.post(
            "/settings/permissions",
            data=dict(
                _csrf="test",
                action="role_permissions",
                role_id=role.id,
                permissions=["utilities.manage"],
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/settings/permissions"))

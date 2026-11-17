"""Integrated readings, invoice issue and customer-visible saved calculations."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import test_customer as fixtures
from sqlalchemy import select

from rpms.customer_models import CustomerAccount
from rpms.models import MODELS as M
from rpms.models import db


class BillingWorkspaceTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    csrf = fixtures.CustomerTests.csrf
    register = fixtures.CustomerTests.register
    active_lease = fixtures.CustomerTests.active_lease

    def prepare(self):
        self.register()
        account = db.session.scalar(select(CustomerAccount))
        self.lease = self.active_lease(account.tenant_id)
        self.water = M["utility_meters"](
            room_id=self.room.id,
            meter_type="Water",
            meter_number="W01",
            default_rate=Decimal(".5"),
            unit="m³",
            status="Active",
        )
        self.electric = M["utility_meters"](
            room_id=self.room.id,
            meter_type="Electricity",
            meter_number="E01",
            default_rate=Decimal(".25"),
            unit="kWh",
            status="Active",
        )
        db.session.add_all([self.water, self.electric])
        db.session.commit()
        self.period = date.today().strftime("%Y-%m")
        self.path = (
            f"/records/invoices?contract_id={self.lease.id}&period={self.period}"
        )

    def reading(self, meter, previous, current, **extra):
        data = dict(
            _csrf="test",
            contract_id=self.lease.id,
            period=self.period,
            meter_id=meter.id,
            previous_reading=previous,
            current_reading=current,
            reading_date=date.today().isoformat(),
        )
        data.update(extra)
        return self.staff.post("/billing/readings", data=data)

    def issue(self, client=None):
        return (client or self.staff).post(
            "/billing/issue",
            data=dict(_csrf="test", contract_id=self.lease.id, period=self.period),
        )

    def test_combined_calculation_issue_and_customer_meter_breakdown(self):
        self.prepare()
        for path in [self.path, "/records/meter_readings"]:
            self.assertEqual(self.staff.get(path).status_code, 200)
        self.assertEqual(
            self.reading(
                self.water, "100", "150", unit_rate="0", total_amount="0"
            ).status_code,
            302,
        )
        self.assertEqual(self.reading(self.electric, "1000", "1100").status_code, 302)
        html = self.staff.get(self.path).get_data(as_text=True)
        self.assertIn("<span>Estimated total</span><strong>$200.00</strong>", html)
        response = self.issue()
        self.assertEqual(response.status_code, 302)
        invoice = db.session.scalar(select(M["invoices"]))
        self.assertTrue(response.location.endswith(f"/invoices/{invoice.id}"))
        self.assertEqual(invoice.total_amount, Decimal("200"))
        self.assertEqual(invoice.balance_due, Decimal("200"))
        readings = db.session.scalars(select(M["meter_readings"])).all()
        self.assertTrue(all(r.status == "Billed" for r in readings))
        self.assertEqual(sum(r.total_amount for r in readings), Decimal("50"))
        admin_html = self.staff.get(f"/invoices/{invoice.id}").get_data(as_text=True)
        self.assertIn("<th>Previous</th>", admin_html)
        self.assertIn("<th>Current</th>", admin_html)
        customer_html = self.client.get(f"/customer/invoices/{invoice.id}").get_data(
            as_text=True
        )
        self.assertIn("100.000 → 150.000", customer_html)
        self.assertIn("1000.000 → 1100.000", customer_html)
        self.assertIn("200.00", customer_html)
        self.water.default_rate = 99
        self.lease.monthly_rent = 999
        db.session.commit()
        html = self.staff.get(self.path).get_data(as_text=True)
        self.assertIn("<span>Monthly rent</span><strong>$150.00</strong>", html)
        self.assertIn("<span>Invoice total</span><strong>$200.00</strong>", html)
        self.issue()
        self.assertEqual(db.session.query(M["invoices"]).count(), 1)
        self.reading(self.water, "150", "200")
        self.assertEqual(db.session.query(M["meter_readings"]).count(), 2)

    def test_customer_draft_requires_review_before_invoice_issue(self):
        self.prepare()
        self.client.post(
            f"/customer/rentals/{self.lease.id}/readings",
            data=dict(
                _csrf="test",
                meter_id=self.water.id,
                reading_date=date.today().isoformat(),
                current_reading=10,
                photo=fixtures.photo(),
            ),
            content_type="multipart/form-data",
        )
        reading = db.session.scalar(select(M["meter_readings"]))
        self.assertEqual(reading.status, "Draft")
        self.issue()
        self.assertEqual(db.session.query(M["invoices"]).count(), 0)
        self.assertIn(b"Needs approval", self.staff.get(self.path).data)
        self.staff.post(
            f"/billing/readings/{reading.id}/approve", data={"_csrf": "test"}
        )
        db.session.refresh(reading)
        self.assertEqual(reading.status, "Approved")
        self.reading(self.electric, "1000", "1000")
        self.issue()
        self.assertEqual(
            db.session.scalar(select(M["invoices"])).total_amount, Decimal("155")
        )

    def test_invalid_values_wrong_room_and_duplicate_date_are_rejected(self):
        self.prepare()
        for previous, current in [
            ("NaN", "20"),
            ("-1", "20"),
            ("100", "99"),
            ("0", "Infinity"),
        ]:
            self.assertEqual(
                self.reading(self.water, previous, current).status_code, 302
            )
            self.assertEqual(db.session.query(M["meter_readings"]).count(), 0)
        response = self.reading(self.water, "0", "10", period="not-a-month")
        self.assertEqual(self.staff.get(response.location).status_code, 200)
        other_room = M["rooms"](
            floor_id=self.room.floor_id, room_number="OTHER", status="Vacant"
        )
        db.session.add(other_room)
        db.session.flush()
        other_meter = M["utility_meters"](
            room_id=other_room.id, meter_type="Water", default_rate=1, status="Active"
        )
        db.session.add(other_meter)
        db.session.commit()
        self.reading(other_meter, "0", "10")
        self.assertEqual(db.session.query(M["meter_readings"]).count(), 0)
        self.reading(self.water, "0", "10")
        self.reading(self.water, "10", "20")
        self.assertEqual(db.session.query(M["meter_readings"]).count(), 1)

    def test_utility_permission_does_not_allow_issuing_invoices(self):
        self.prepare()
        role = M["roles"](name="Meter team", is_active=True)
        db.session.add(role)
        db.session.flush()
        person = M["users"](
            name="Meter staff",
            email="meters@example.com",
            role_id=role.id,
            is_active=True,
        )
        db.session.add(person)
        db.session.commit()
        self.staff.post(
            "/settings/permissions",
            data=dict(
                _csrf="test",
                action="role_permissions",
                role_id=role.id,
                permissions=["utilities.manage"],
            ),
        )
        client = self.app.test_client()
        with client.session_transaction() as session:
            session.update(uid=person.id, csrf="test")
        self.assertEqual(client.get(self.path).status_code, 200)
        self.assertEqual(self.issue(client).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/billing/issue",
                data=dict(_csrf="test", contract_id=self.lease.id, period=self.period),
            ).status_code,
            302,
        )
        self.assertEqual(db.session.query(M["invoices"]).count(), 0)

    def test_missing_active_meter_readings_block_issue(self):
        self.prepare()
        self.issue()
        self.assertEqual(db.session.query(M["invoices"]).count(), 0)
        self.reading(self.water, "100", "150")
        self.issue()
        self.assertEqual(db.session.query(M["invoices"]).count(), 0)
        self.reading(self.electric, "1000", "1100")
        self.issue()
        self.assertEqual(
            db.session.scalar(select(M["invoices"])).total_amount, Decimal("200")
        )

    def test_workspace_uses_latest_reading_for_each_meter(self):
        self.prepare()
        self.reading(self.water, "100", "150")
        self.reading(self.electric, "1000", "1100")
        with patch(
            "rpms.billing_workspace.render_template", return_value="ok"
        ) as render:
            self.assertEqual(self.staff.get(self.path).status_code, 200)
        latest = render.call_args.kwargs["latest"]
        self.assertEqual(latest[self.water.id].current_reading, Decimal("150"))
        self.assertEqual(latest[self.electric.id].current_reading, Decimal("1100"))

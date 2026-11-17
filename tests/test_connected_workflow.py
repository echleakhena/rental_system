"""Staff/customer handoffs must agree on progress, charges and payment status."""

import unittest
from datetime import date, timedelta
from decimal import Decimal

import test_customer as fixtures
from sqlalchemy import select

from rpms.customer_models import CustomerAccount, PaymentProof
from rpms.models import MODELS as M
from rpms.models import db
from rpms.services import activate, issue_invoice


class ConnectedWorkflowTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    csrf = fixtures.CustomerTests.csrf
    register = fixtures.CustomerTests.register
    active_lease = fixtures.CustomerTests.active_lease
    post_staff = fixtures.CustomerTests.post_staff

    def prepare(self):
        self.register()
        self.account = db.session.scalar(select(CustomerAccount))
        self.lease = self.active_lease(self.account.tenant_id)
        self.period = date.today().strftime("%Y-%m")

    def test_booking_handoff_stays_visible_until_activation(self):
        self.register()
        self.client.post(
            f"/customer/rooms/{self.room.id}/book",
            data=dict(_csrf="test", move_in=date.today().isoformat()),
        )
        booking = db.session.scalar(select(M["reservations"]))
        self.assertIn(
            b"Your booking is being reviewed", self.client.get("/customer/rentals").data
        )
        self.post_staff("confirm_booking", booking.id)
        self.assertIn(
            b"Your booking is confirmed", self.client.get("/customer/rentals").data
        )
        staff_page = self.staff.get("/customer-management").get_data(as_text=True)
        self.assertIn(f"reservation_id={booking.id}", staff_page)
        self.assertIn(b"Prepare rentals", self.staff.get("/").data)
        form = self.staff.get(
            f"/records/rental_contracts/new?reservation_id={booking.id}"
        )
        self.assertEqual(form.status_code, 200)
        self.assertIn(f'value="{booking.id}" selected', form.get_data(as_text=True))
        payload = dict(
            _csrf="test",
            reservation_id=booking.id,
            start_date=date.today().isoformat(),
            end_date=(date.today() + timedelta(days=365)).isoformat(),
            monthly_rent=150,
            deposit_required=150,
            billing_day=1,
            payment_due_day=5,
            status="Draft",
        )
        self.staff.post("/records/rental_contracts/new", data=payload)
        lease = db.session.scalar(select(M["rental_contracts"]))
        self.assertIn(
            b"Review &amp; activate rental", self.staff.get("/customer-management").data
        )
        self.assertIn(
            b"agreement is being prepared", self.client.get("/customer/rentals").data
        )
        self.staff.post("/records/rental_contracts/new", data=payload)
        self.assertEqual(db.session.query(M["rental_contracts"]).count(), 1)
        self.staff.post(f"/contracts/{lease.id}/activate", data={"_csrf": "test"})
        self.assertIn(b"CURRENT HOME", self.client.get("/customer/rentals").data)
        self.assertNotIn(
            b"Review &amp; activate rental", self.staff.get("/customer-management").data
        )

    def test_every_invoice_entry_point_requires_readings(self):
        self.prepare()
        db.session.add(
            M["utility_meters"](
                room_id=self.room.id,
                meter_type="Water",
                status="Active",
                default_rate=1,
            )
        )
        db.session.commit()
        for path in ["/billing/issue", f"/contracts/{self.lease.id}/invoice"]:
            self.staff.post(
                path,
                data=dict(_csrf="test", contract_id=self.lease.id, period=self.period),
            )
            self.assertEqual(db.session.query(M["invoices"]).count(), 0)

    def test_generic_readings_cannot_change_an_issued_month(self):
        self.prepare()
        invoice = issue_invoice(self.lease, self.period, self.admin.id)
        meter = M["utility_meters"](
            room_id=self.room.id, meter_type="Water", status="Active", default_rate=1
        )
        db.session.add(meter)
        db.session.commit()
        self.staff.post(
            "/records/meter_readings/new",
            data=dict(
                _csrf="test",
                contract_id=self.lease.id,
                meter_id=meter.id,
                reading_date=date.today().isoformat(),
                previous_reading=0,
                current_reading=100,
                unit_rate=1,
                status="Approved",
                reading_type="Regular",
            ),
        )
        self.assertEqual(db.session.query(M["meter_readings"]).count(), 0)
        db.session.refresh(invoice)
        self.assertEqual(invoice.total_amount, Decimal("150"))

    def test_payment_review_and_partial_balance_agree_on_both_sides(self):
        self.prepare()
        invoice = issue_invoice(self.lease, self.period, self.admin.id)
        db.session.commit()
        self.post_staff("qr", photo=fixtures.photo())
        self.client.post(
            f"/customer/invoices/{invoice.id}",
            data=dict(
                _csrf="test",
                amount=50,
                reference="CONNECTED-TRANSFER",
                photo=fixtures.photo(),
            ),
            content_type="multipart/form-data",
        )
        proof = db.session.scalar(select(PaymentProof))
        self.assertIn(b"Payment in review", self.client.get("/customer/rentals").data)
        self.assertIn(
            b"Payment proof received",
            self.client.get(f"/customer/invoices/{invoice.id}").data,
        )
        self.assertIn(
            b"Review customer payment", self.staff.get(f"/invoices/{invoice.id}").data
        )
        self.assertIn(
            b"CONNECTED-TRANSFER", self.staff.get("/customer-management").data
        )
        self.post_staff("confirm_payment", proof.id)
        db.session.refresh(invoice)
        self.assertEqual(invoice.balance_due, Decimal("100"))
        self.assertEqual(invoice.status, "Partial")
        self.assertNotIn(
            b"Payment in review", self.client.get("/customer/rentals").data
        )
        for client, path in [
            (self.client, f"/customer/invoices/{invoice.id}"),
            (self.staff, f"/invoices/{invoice.id}"),
        ]:
            self.assertIn(b"100.00", client.get(path).data)
        self.post_staff("confirm_payment", proof.id)
        self.assertEqual(db.session.query(M["payments"]).count(), 1)

    def test_upcoming_rental_is_not_presented_as_a_missing_home(self):
        self.prepare()
        self.lease.start_date = date.today() + timedelta(days=5)
        db.session.commit()
        page = self.client.get("/customer/rentals").get_data(as_text=True)
        self.assertIn("UPCOMING RENTAL", page)
        self.assertNotIn("Your home is waiting", page)
        self.assertNotIn("CURRENT HOME", page)

    def test_primary_customer_required_before_activation(self):
        self.register()
        account = db.session.scalar(select(CustomerAccount))
        lease = self.active_lease(account.tenant_id)
        lease.status = "Draft"
        link = db.session.scalar(select(M["contract_tenants"]))
        link.is_primary = False
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "primary tenant"):
            activate(lease, self.admin.id)

    def test_both_staff_review_screens_recalculate_customer_charges(self):
        self.prepare()
        meter = M["utility_meters"](
            room_id=self.room.id, meter_type="Water", status="Active", default_rate=2
        )
        db.session.add(meter)
        db.session.flush()
        reading = M["meter_readings"](
            meter_id=meter.id,
            contract_id=self.lease.id,
            reading_date=date.today(),
            previous_reading=100,
            current_reading=110,
            unit_rate=2,
            usage_quantity=999,
            total_amount=999,
            reading_type="Customer",
            status="Draft",
        )
        db.session.add(reading)
        db.session.commit()
        self.assertNotIn(
            b"Submit a new reading", self.client.get("/customer/rentals").data
        )
        for path, data in [
            (
                "/customer-management",
                dict(_csrf="test", action="approve_reading", record_id=reading.id),
            ),
            (f"/billing/readings/{reading.id}/approve", dict(_csrf="test")),
        ]:
            reading.status = "Draft"
            reading.total_amount = 999
            db.session.commit()
            self.staff.post(path, data=data)
            db.session.refresh(reading)
            self.assertEqual(reading.status, "Approved")
            self.assertEqual(reading.usage_quantity, Decimal("10"))
            self.assertEqual(reading.total_amount, Decimal("20"))
        invoice = issue_invoice(self.lease, self.period, self.admin.id)
        db.session.commit()
        self.assertEqual(invoice.total_amount, Decimal("170"))
        self.assertIn(
            b"This month's bill has been issued",
            self.client.get("/customer/rentals").data,
        )

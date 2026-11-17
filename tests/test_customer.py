"""Customer isolation and the booking → utility → invoice → payment workflow."""

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from sqlalchemy import select

from rpms import create_app
from rpms.customer_models import CustomerAccount, PaymentProof
from rpms.models import MODELS as M
from rpms.models import db
from rpms.seed import create_admin
from rpms.services import activate, issue_invoice


def photo():
    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, "PNG")
    stream.seek(0)
    return stream, "meter.png"


class CustomerTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            }
        )
        self.temp = tempfile.TemporaryDirectory()
        self.app.instance_path = self.temp.name
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.admin = create_admin("admin@example.com", "test-password")
        prop = M["properties"](name="Garden Residence", status="Active")
        db.session.add(prop)
        db.session.flush()
        floor = M["floors"](property_id=prop.id, floor_number=1)
        db.session.add(floor)
        db.session.flush()
        self.room = M["rooms"](
            floor_id=floor.id, room_number="101", monthly_rent=150, status="Vacant"
        )
        db.session.add(self.room)
        db.session.commit()
        self.client = self.app.test_client()
        self.staff = self.app.test_client()
        with self.staff.session_transaction() as s:
            s.update(uid=self.admin.id, csrf="test")
        self.csrf(self.client)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.temp.cleanup()

    def csrf(self, client):
        with client.session_transaction() as s:
            s["csrf"] = "test"

    def register(self, client=None, phone="012 345 678", name="Sok Dara"):
        client = client or self.client
        self.csrf(client)
        response = client.post(
            "/customer/register",
            data=dict(_csrf="test", name=name, phone=phone, password="password123"),
        )
        self.csrf(client)
        return response

    def active_lease(self, tenant_id):
        today = date.today()
        lease = M["rental_contracts"](
            room_id=self.room.id,
            start_date=today - timedelta(days=60),
            end_date=today + timedelta(days=365),
            status="Draft",
            monthly_rent=150,
            deposit_required=150,
            payment_due_day=5,
        )
        db.session.add(lease)
        db.session.flush()
        db.session.add(
            M["contract_tenants"](
                contract_id=lease.id, tenant_id=tenant_id, is_primary=True
            )
        )
        db.session.flush()
        activate(lease, self.admin.id)
        db.session.commit()
        return lease

    def post_staff(self, action, id=0, **data):
        return self.staff.post(
            "/customer-management",
            data=dict(_csrf="test", action=action, record_id=id, **data),
        )

    def test_individual_room_lookup_preserves_public_visibility(self):
        path = f"/customer/rooms/{self.room.id}"
        with patch(
            "rpms.customer.public_rooms", side_effect=AssertionError("full scan")
        ):
            self.assertEqual(self.client.get(path).status_code, 200)
            self.room.status = "Occupied"
            db.session.commit()
            self.assertEqual(self.client.get(path).status_code, 404)
            self.room.status = "Vacant"
            floor = db.session.get(M["floors"], self.room.floor_id)
            prop = db.session.get(M["properties"], floor.property_id)
            prop.status = "Inactive"
            db.session.commit()
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_registration_phone_login_and_customer_staff_isolation(self):
        self.assertEqual(self.register().status_code, 302)
        account = db.session.scalar(select(CustomerAccount))
        self.assertEqual(account.phone, "+85512345678")
        self.assertNotEqual(account.password_hash, "password123")
        self.assertEqual(self.client.get("/records/tenants").status_code, 302)
        self.assertEqual(self.client.get("/customer-management").status_code, 302)
        self.assertEqual(
            self.client.post("/customer/logout", data={"_csrf": "test"}).status_code,
            302,
        )
        self.csrf(self.client)
        self.assertEqual(
            self.client.post(
                "/customer/login",
                data=dict(_csrf="test", phone="+85512345678", password="password123"),
            ).status_code,
            302,
        )
        self.assertEqual(self.client.get("/customer/profile").status_code, 200)
        self.assertEqual(
            self.client.post("/customer/register", data={}).status_code, 400
        )
        other = self.app.test_client()
        self.assertEqual(self.register(other, phone="+85512345678").status_code, 200)
        self.assertEqual(db.session.query(CustomerAccount).count(), 1)

    def test_pages_and_booking_confirmation_to_lease(self):
        self.register()
        self.staff.post(
            f"/rooms/{self.room.id}",
            data={"_csrf": "test", "photo_3": photo()},
            content_type="multipart/form-data",
        )
        with self.client.get(f"/customer/rooms/{self.room.id}/photo/3") as response:
            self.assertEqual(response.status_code, 200)
        html = self.client.get(f"/customer/rooms/{self.room.id}").get_data(as_text=True)
        self.assertEqual(html.count('<figure class="detail-photo'), 4)
        self.assertIn("<figcaption>Toilet", html)
        for path in [
            "/customer",
            "/customer/rooms",
            f"/customer/rooms/{self.room.id}",
            "/customer/rentals",
            "/customer/profile",
            "/customer-management",
        ]:
            client = self.staff if path == "/customer-management" else self.client
            self.assertEqual(client.get(path).status_code, 200, path)
        self.client.post(
            f"/customer/rooms/{self.room.id}/book",
            data=dict(_csrf="test", move_in=date.today().isoformat()),
        )
        booking = db.session.scalar(select(M["reservations"]))
        self.assertEqual(booking.status, "Pending")
        self.assertEqual(self.staff.get("/customer-management").status_code, 200)
        self.client.post(
            f"/customer/rooms/{self.room.id}/book",
            data=dict(_csrf="test", move_in=date.today().isoformat()),
        )
        self.assertEqual(db.session.query(M["reservations"]).count(), 1)
        self.post_staff("confirm_booking", booking.id)
        db.session.refresh(booking)
        db.session.refresh(self.room)
        self.assertEqual(booking.status, "Confirmed")
        self.assertEqual(self.room.status, "Reserved")
        self.assertNotIn(b"Room 101", self.client.get("/customer/rooms").data)
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
        self.assertEqual(
            self.staff.post("/records/rental_contracts/new", data=payload).status_code,
            302,
        )
        lease = db.session.scalar(select(M["rental_contracts"]))
        self.staff.post(f"/contracts/{lease.id}/activate", data={"_csrf": "test"})
        db.session.refresh(lease)
        self.assertEqual(lease.status, "Active")
        self.assertIn(b"CURRENT HOME", self.client.get("/customer/rentals").data)

    def test_utility_photo_billing_and_confirmed_payment(self):
        self.register()
        account = db.session.scalar(select(CustomerAccount))
        lease = self.active_lease(account.tenant_id)
        meter = M["utility_meters"](
            room_id=self.room.id,
            meter_type="Water",
            meter_number="W01",
            default_rate=2,
            status="Active",
            unit="m³",
        )
        db.session.add(meter)
        db.session.commit()
        data = dict(
            _csrf="test",
            meter_id=meter.id,
            reading_date=date.today().isoformat(),
            current_reading="10",
            photo=photo(),
        )
        self.client.post(
            f"/customer/rentals/{lease.id}/readings",
            data=data,
            content_type="multipart/form-data",
        )
        reading = db.session.scalar(select(M["meter_readings"]))
        self.assertIsNotNone(reading)
        self.assertEqual(reading.status, "Draft")
        self.assertTrue(
            (Path(self.temp.name) / "customer_uploads" / reading.photo_path).exists()
        )
        self.assertEqual(self.staff.get("/customer-management").status_code, 200)
        with self.client.get(f"/customer/evidence/reading/{reading.id}") as response:
            self.assertEqual(response.status_code, 200)
        with self.assertRaisesRegex(ValueError, "Review draft utility"):
            issue_invoice(lease, date.today().strftime("%Y-%m"), self.admin.id)
        self.post_staff("approve_reading", reading.id)
        inv = issue_invoice(lease, date.today().strftime("%Y-%m"), self.admin.id)
        db.session.commit()
        self.assertEqual(inv.total_amount, Decimal("170"))
        response = self.client.post(
            f"/customer/rentals/{lease.id}/readings",
            data=dict(
                _csrf="test",
                meter_id=meter.id,
                reading_date=date.today().isoformat(),
                current_reading="11",
                photo=photo(),
            ),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.query(M["meter_readings"]).count(), 1)
        self.assertIn(b"170.00", self.client.get(f"/customer/invoices/{inv.id}").data)
        self.post_staff("qr", photo=photo())
        with self.client.get("/customer/payment-qr") as response:
            self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/customer/invoices/{inv.id}",
            data=dict(_csrf="test", amount=170, reference="BANK-123", photo=photo()),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        proof = db.session.scalar(select(PaymentProof))
        self.assertIsNotNone(proof)
        self.assertEqual(proof.status, "Pending")
        db.session.refresh(inv)
        self.assertEqual(inv.balance_due, 170)
        self.assertEqual(self.staff.get("/customer-management").status_code, 200)
        self.post_staff("confirm_payment", proof.id)
        db.session.refresh(inv)
        db.session.refresh(proof)
        self.assertEqual(inv.status, "Paid")
        self.assertEqual(inv.balance_due, 0)
        self.assertEqual(proof.status, "Confirmed")
        self.assertIsNotNone(proof.payment_id)
        self.post_staff("confirm_payment", proof.id)
        self.assertEqual(db.session.query(M["payments"]).count(), 1)
        self.assertEqual(self.client.get("/customer/rentals").status_code, 200)

    def test_customer_cannot_access_other_resident_records(self):
        self.register()
        first = db.session.scalar(select(CustomerAccount))
        lease = self.active_lease(first.tenant_id)
        inv = issue_invoice(lease, date.today().strftime("%Y-%m"), self.admin.id)
        db.session.commit()
        other = self.app.test_client()
        self.register(other, phone="098765432", name="Another resident")
        self.assertEqual(other.get(f"/customer/invoices/{inv.id}").status_code, 404)
        self.assertEqual(
            other.post(
                f"/customer/rentals/{lease.id}/readings", data={"_csrf": "test"}
            ).status_code,
            404,
        )
        self.assertNotIn(b"CURRENT HOME", other.get("/customer/rentals").data)
        self.assertEqual(other.get("/customer/evidence/reading/999").status_code, 404)
        self.assertEqual(
            self.app.test_client().get("/customer/payment-qr").status_code, 302
        )

    def test_no_phone_based_takeover_and_staff_link_existing_resident(self):
        old = M["tenants"](
            full_name="Existing resident", phone="012345678", status="Active"
        )
        db.session.add(old)
        db.session.commit()
        self.active_lease(old.id)
        self.register()
        a = db.session.scalar(select(CustomerAccount))
        self.assertNotEqual(a.tenant_id, old.id)
        self.assertNotIn(b"CURRENT HOME", self.client.get("/customer/rentals").data)
        self.post_staff("link_tenant", a.id, tenant_id=old.id)
        db.session.refresh(a)
        self.assertEqual(a.tenant_id, old.id)
        self.assertIn(b"CURRENT HOME", self.client.get("/customer/rentals").data)

    def test_invalid_reading_and_fake_photo_do_not_create_records(self):
        self.register()
        a = db.session.scalar(select(CustomerAccount))
        lease = self.active_lease(a.tenant_id)
        meter = M["utility_meters"](
            room_id=self.room.id,
            meter_type="Electricity",
            default_rate=1,
            status="Active",
        )
        db.session.add(meter)
        db.session.commit()
        for reading, upload in [
            ("NaN", photo()),
            ("-5", photo()),
            ("12", (BytesIO(b"not an image"), "bad.png")),
        ]:
            response = self.client.post(
                f"/customer/rentals/{lease.id}/readings",
                data=dict(
                    _csrf="test",
                    meter_id=meter.id,
                    reading_date=date.today().isoformat(),
                    current_reading=reading,
                    photo=upload,
                ),
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.query(M["meter_readings"]).count(), 0)
        self.assertEqual(
            list((Path(self.temp.name) / "customer_uploads").glob("*")), []
        )

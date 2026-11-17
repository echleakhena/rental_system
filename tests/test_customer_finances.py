"""Customer monthly totals, utility uploads and confirmed receipts stay in sync."""

import unittest
from datetime import date, timedelta
from decimal import Decimal

import test_customer as fixtures
from sqlalchemy import select

from rpms.customer_models import CustomerAccount, PaymentProof
from rpms.models import MODELS as M
from rpms.models import db
from rpms.services import issue_invoice, pay_invoice


class CustomerFinanceTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    csrf = fixtures.CustomerTests.csrf
    register = fixtures.CustomerTests.register
    active_lease = fixtures.CustomerTests.active_lease
    post_staff = fixtures.CustomerTests.post_staff

    def prepare(self):
        self.register()
        account = db.session.scalar(select(CustomerAccount))
        self.lease = self.active_lease(account.tenant_id)
        self.period = date.today().strftime("%Y-%m")

    def test_upload_both_utilities_monthly_total_and_all_payment_methods(self):
        self.prepare()
        for kind, rate, value in [("Water", "2", "10"), ("Electricity", ".25", "100")]:
            meter = M["utility_meters"](
                room_id=self.room.id,
                meter_type=kind,
                default_rate=Decimal(rate),
                status="Active",
            )
            db.session.add(meter)
            db.session.commit()
            page = self.client.get("/customer/rentals").get_data(as_text=True)
            self.assertIn(f"Upload {kind.lower()} meter photo", page)
            self.client.post(
                f"/customer/rentals/{self.lease.id}/readings",
                data=dict(
                    _csrf="test",
                    meter_id=meter.id,
                    current_reading=value,
                    reading_date=date.today().isoformat(),
                    photo=fixtures.photo(),
                ),
                content_type="multipart/form-data",
            )
            reading = db.session.scalar(
                select(M["meter_readings"]).where(
                    M["meter_readings"].meter_id == meter.id
                )
            )
            self.assertEqual(reading.status, "Draft")
            self.post_staff("approve_reading", reading.id)
        self.staff.post(
            "/billing/issue",
            data=dict(_csrf="test", contract_id=self.lease.id, period=self.period),
        )
        invoice = db.session.scalar(select(M["invoices"]))
        self.assertEqual(invoice.total_amount, Decimal("195"))
        pay_invoice(invoice.id, 50, "Cash", "CASH-RECEIPT-50", self.admin.id)
        db.session.commit()
        self.post_staff("qr", photo=fixtures.photo())
        self.client.post(
            f"/customer/invoices/{invoice.id}",
            data=dict(
                _csrf="test",
                amount=45,
                reference="QR-RECEIPT-45",
                photo=fixtures.photo(),
            ),
            content_type="multipart/form-data",
        )
        proof = db.session.scalar(select(PaymentProof))
        page = self.client.get("/customer/rentals").get_data(as_text=True)
        self.assertIn("CASH-RECEIPT-50", page)
        self.assertNotIn("QR-RECEIPT-45", page)
        self.post_staff("confirm_payment", proof.id)
        page = self.client.get("/customer/rentals").get_data(as_text=True)
        for text in [
            "$150.00",
            "$20.00",
            "$25.00",
            "$195.00",
            "$95.00",
            "$100.00",
            "CASH-RECEIPT-50",
            "QR-RECEIPT-45",
        ]:
            self.assertIn(text, page)
        detail = self.client.get(f"/customer/invoices/{invoice.id}").get_data(
            as_text=True
        )
        for reference in ["CASH-RECEIPT-50", "QR-RECEIPT-45"]:
            self.assertIn(reference, detail)
        self.assertIn("Cash", detail)
        self.assertIn("KHQR", detail)

    def test_month_filter_and_unissued_month_do_not_show_zero_due_as_final_bill(self):
        self.prepare()
        invoice = issue_invoice(self.lease, self.period, self.admin.id)
        db.session.commit()
        other_month = (date.today().replace(day=1) + timedelta(days=32)).strftime(
            "%Y-%m"
        )
        page = self.client.get(f"/customer/rentals?period={other_month}").get_data(
            as_text=True
        )
        self.assertIn("Monthly invoice not issued yet", page)
        self.assertIn("Your final total includes utility charges", page)
        self.assertIn("Outstanding across all months", page)
        self.assertEqual(
            self.client.get("/customer/rentals?period=invalid").status_code, 400
        )
        page = self.client.get(f"/customer/rentals?period={self.period}").get_data(
            as_text=True
        )
        self.assertIn(f"View invoice · {invoice.invoice_no}", page)

    def test_another_residents_receipts_are_not_visible(self):
        self.prepare()
        invoice = issue_invoice(self.lease, self.period, self.admin.id)
        pay_invoice(invoice.id, 10, "Transfer", "MY-PRIVATE-RECEIPT", self.admin.id)
        db.session.commit()
        other = self.app.test_client()
        self.register(other, phone="098765432", name="Other resident")
        page = other.get("/customer/rentals").get_data(as_text=True)
        self.assertNotIn("MY-PRIVATE-RECEIPT", page)
        self.assertIn("No confirmed payments yet", page)
        self.assertEqual(other.get(f"/customer/invoices/{invoice.id}").status_code, 404)

"""Rental detail totals and actions keep the lease workflow intact."""

import unittest
from datetime import date, timedelta

import test_customer as fixtures
from sqlalchemy import select

from rpms.models import MODELS as M
from rpms.models import db
from rpms.services import pay_invoice


class LeaseDetailTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    csrf = fixtures.CustomerTests.csrf

    def draft(self):
        resident = M["tenants"](
            full_name="Lease Resident", phone="012345678", status="Active"
        )
        db.session.add(resident)
        db.session.flush()
        lease = M["rental_contracts"](
            room_id=self.room.id,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=60),
            monthly_rent=150,
            deposit_required=150,
            billing_day=1,
            payment_due_day=5,
            status="Draft",
        )
        db.session.add(lease)
        db.session.flush()
        db.session.add(
            M["contract_tenants"](
                contract_id=lease.id, tenant_id=resident.id, is_primary=True
            )
        )
        db.session.commit()
        return lease

    def test_activation_billing_and_totals_on_the_detail_page(self):
        lease = self.draft()
        path = f"/records/rental_contracts/{lease.id}"
        html = self.staff.get(path).get_data(as_text=True)
        self.assertIn("Lease Resident", html)
        self.assertIn("Activate rental", html)
        self.assertIn("No invoices yet", html)
        response = self.staff.post(
            f"/contracts/{lease.id}/activate",
            data=dict(_csrf="test", return_detail="yes"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(path))
        db.session.refresh(lease)
        self.assertEqual(lease.status, "Active")
        self.assertIn(b"Prepare invoice", self.staff.get(path).data)
        response = self.staff.post(
            f"/contracts/{lease.id}/invoice",
            data=dict(
                _csrf="test", return_detail="yes", period=date.today().strftime("%Y-%m")
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(path))
        invoice = db.session.scalar(
            select(M["invoices"]).where(M["invoices"].contract_id == lease.id)
        )
        pay_invoice(invoice.id, 40, "Cash", "", self.admin.id)
        db.session.commit()
        html = self.staff.get(path).get_data(as_text=True)
        self.assertIn("<span>Payments collected</span><strong>$40.00</strong>", html)
        self.assertIn("<span>Outstanding balance</span><strong>$110.00</strong>", html)
        self.assertIn(invoice.invoice_no, html)
        invoice.status = "Voided"
        db.session.commit()
        html = self.staff.get(path).get_data(as_text=True)
        self.assertIn("<span>Outstanding balance</span><strong>$0.00</strong>", html)
        self.assertIn("Voided", html)

    def test_read_only_and_missing_optional_details(self):
        lease = self.draft()
        role = M["roles"](name="Viewer", is_active=True)
        db.session.add(role)
        db.session.flush()
        viewer = M["users"](
            name="Viewer", email="viewer@example.com", role_id=role.id, is_active=True
        )
        db.session.add(viewer)
        db.session.commit()
        with self.client.session_transaction() as session:
            session.update(uid=viewer.id, csrf="test")
        html = self.client.get(f"/records/rental_contracts/{lease.id}").get_data(
            as_text=True
        )
        self.assertNotIn("Activate rental", html)
        self.assertNotIn("Edit rental", html)
        self.assertEqual(
            self.client.post(
                f"/contracts/{lease.id}/activate",
                data=dict(_csrf="test", return_detail="yes"),
            ).status_code,
            403,
        )
        empty = M["rental_contracts"](status="Draft")
        db.session.add(empty)
        db.session.commit()
        response = self.staff.get(f"/records/rental_contracts/{empty.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No customer linked yet", response.data)

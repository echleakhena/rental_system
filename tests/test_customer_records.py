"""The four-field customer form creates and maintains safe portal credentials."""

import re
import unittest

import test_customer as fixtures
from sqlalchemy import select
from werkzeug.security import check_password_hash

from rpms.customer_models import CustomerAccount
from rpms.models import MODELS as M
from rpms.models import db


class CustomerRecordTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    csrf = fixtures.CustomerTests.csrf

    def create_customer(self, **changes):
        payload = dict(
            _csrf="test",
            full_name="New Resident",
            gender="Female",
            phone="012345678",
            password="resident-password",
        )
        payload.update(changes)
        return self.staff.post("/records/tenants/new", data=payload)

    def test_only_four_fields_and_real_customer_login(self):
        html = self.staff.get("/records/tenants/new").get_data(as_text=True)
        names = set(
            re.findall(r'<(?:input|select|textarea)[^>]*\bname="([^"]+)"', html)
        )
        self.assertEqual(names, {"_csrf", "full_name", "gender", "phone", "password"})
        self.assertEqual(self.create_customer().status_code, 302)
        tenant = db.session.scalar(
            select(M["tenants"]).where(M["tenants"].full_name == "New Resident")
        )
        account = db.session.scalar(
            select(CustomerAccount).where(CustomerAccount.tenant_id == tenant.id)
        )
        self.assertEqual(tenant.gender, "Female")
        self.assertEqual(tenant.phone, "+85512345678")
        self.assertEqual(tenant.status, "Active")
        self.assertTrue(check_password_hash(account.password_hash, "resident-password"))
        response = self.client.post(
            "/customer/login",
            data=dict(_csrf="test", phone="012345678", password="resident-password"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/customer/profile").status_code, 200)
        listing = self.staff.get("/records/tenants").get_data(as_text=True)
        self.assertIn("<th>Name</th>", listing)
        self.assertIn("<th>Password</th>", listing)
        self.assertNotIn("<th>Email</th>", listing)
        self.assertNotIn("<th>Date Of Birth</th>", listing)
        for path in [
            "/records/tenants",
            f"/records/tenants/{tenant.id}",
            f"/records/tenants/{tenant.id}/edit",
        ]:
            text = self.staff.get(path).get_data(as_text=True)
            self.assertNotIn(account.password_hash, text)
            self.assertNotIn("resident-password", text)

    def test_edit_keeps_password_and_syncs_phone_without_erasing_legacy_data(self):
        self.create_customer()
        tenant = db.session.scalar(
            select(M["tenants"]).where(M["tenants"].full_name == "New Resident")
        )
        account = db.session.scalar(
            select(CustomerAccount).where(CustomerAccount.tenant_id == tenant.id)
        )
        original_hash = account.password_hash
        tenant.email = "kept@example.com"
        tenant.address = "Keep this address"
        db.session.commit()
        payload = dict(
            _csrf="test",
            full_name="Updated Resident",
            gender="Male",
            phone="098765432",
            password="",
            email="ignored@example.com",
            status="Inactive",
        )
        response = self.staff.post(f"/records/tenants/{tenant.id}/edit", data=payload)
        self.assertEqual(response.status_code, 302)
        db.session.refresh(tenant)
        db.session.refresh(account)
        self.assertEqual(account.password_hash, original_hash)
        self.assertEqual(account.phone, "+85598765432")
        self.assertEqual(tenant.phone, account.phone)
        self.assertEqual(tenant.email, "kept@example.com")
        self.assertEqual(tenant.address, "Keep this address")
        self.assertEqual(tenant.status, "Active")
        payload["password"] = "replacement-password"
        self.staff.post(f"/records/tenants/{tenant.id}/edit", data=payload)
        db.session.refresh(account)
        self.assertTrue(
            check_password_hash(account.password_hash, "replacement-password")
        )
        response = self.client.post(
            "/customer/login",
            data=dict(_csrf="test", phone="098765432", password="replacement-password"),
        )
        self.assertEqual(response.status_code, 302)

    def test_bad_details_and_duplicate_login_phone_are_rejected(self):
        for values in [
            dict(password=""),
            dict(password="short"),
            dict(phone="123"),
            dict(gender="Invalid"),
        ]:
            self.assertEqual(self.create_customer(**values).status_code, 200)
            self.assertEqual(db.session.query(CustomerAccount).count(), 0)
        self.assertEqual(self.create_customer().status_code, 302)
        self.assertEqual(
            self.create_customer(
                full_name="Duplicate", phone="+85512345678"
            ).status_code,
            200,
        )
        self.assertEqual(db.session.query(CustomerAccount).count(), 1)
        self.assertIsNone(
            db.session.scalar(
                select(M["tenants"]).where(M["tenants"].full_name == "Duplicate")
            )
        )

    def test_legacy_customer_can_receive_a_password_without_new_tenant(self):
        tenant = M["tenants"](
            full_name="Existing Resident",
            phone="012345678",
            status="Active",
            address="Existing information",
        )
        db.session.add(tenant)
        db.session.commit()
        response = self.staff.post(
            f"/records/tenants/{tenant.id}/edit",
            data=dict(
                _csrf="test",
                full_name=tenant.full_name,
                gender="Other",
                phone=tenant.phone,
                password="new-login-password",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.query(M["tenants"]).count(), 1)
        a = db.session.scalar(select(CustomerAccount))
        self.assertEqual(a.tenant_id, tenant.id)
        self.assertTrue(check_password_hash(a.password_hash, "new-login-password"))

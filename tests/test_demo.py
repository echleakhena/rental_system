"""Serverless demo isolation, sample accounts and disposable uploads."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from rpms import create_app
from rpms.customer_models import CustomerAccount
from rpms.models import MODELS, db


class DemoTests(unittest.TestCase):
    def test_demo_ignores_local_database_and_supports_both_logins(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    "os.environ",
                    {
                        "RPMS_DEMO": "1",
                        "RPMS_ENV": "production",
                        "SECRET_KEY": "test-secret",
                        "RPMS_DEMO_PASSWORD": "test-demo-password",
                        "DATABASE_URL": "sqlite:////must-not-open.db",
                    },
                ),
                patch("rpms.tempfile.mkdtemp", return_value=directory),
                patch(
                    "rpms.database_uri", side_effect=AssertionError("local settings")
                ),
            ):
                app = create_app()
            self.assertEqual(app.instance_path, directory)
            self.assertTrue(app.config["DEMO_MODE"])
            with app.app_context():
                self.assertEqual(db.session.query(MODELS["rooms"]).count(), 12)
                self.assertEqual(db.session.query(CustomerAccount).count(), 1)
                room = db.session.scalar(
                    select(MODELS["rooms"]).where(MODELS["rooms"].status == "Vacant")
                )
                photo_path = f"/customer/rooms/{room.id}/photo/0"
                self.assertTrue(
                    (Path(directory) / "room_photos" / room.photos[0]).is_file()
                )
            try:
                for path, credentials, destination in (
                    (
                        "/login",
                        {"email": "admin@rpms.local", "password": "test-demo-password"},
                        "/",
                    ),
                    (
                        "/customer/login",
                        {"phone": "012345678", "password": "test-demo-password"},
                        "/customer/rentals",
                    ),
                ):
                    client = app.test_client()
                    client.get(path, base_url="https://localhost")
                    with client.session_transaction() as session:
                        token = session["csrf"]
                    response = client.post(
                        path,
                        data={"_csrf": token, **credentials},
                        base_url="https://localhost",
                    )
                    self.assertEqual(response.status_code, 302)
                    page = client.get(destination, base_url="https://localhost")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(b"Demo system", page.data)
                with app.test_client().get(photo_path) as photo:
                    self.assertEqual(photo.status_code, 200)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_demo_requires_password_before_creating_temporary_files(self):
        with (
            patch.dict("os.environ", {"RPMS_DEMO": "1", "RPMS_DEMO_PASSWORD": ""}),
            patch("rpms.tempfile.mkdtemp") as temporary,
        ):
            with self.assertRaisesRegex(ValueError, "RPMS_DEMO_PASSWORD"):
                create_app()
            temporary.assert_not_called()

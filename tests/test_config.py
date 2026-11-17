"""Configuration isolation and deployment defaults."""

import unittest
from unittest.mock import patch

from rpms import create_app


class ConfigurationTests(unittest.TestCase):
    def test_explicit_configuration_skips_private_local_settings(self):
        with (
            patch.dict("os.environ", {"RPMS_ENV": "development"}),
            patch("rpms.database_uri", side_effect=AssertionError("local database")),
            patch("rpms.Path.read_text", side_effect=AssertionError("local secret")),
        ):
            app = create_app(
                {"SECRET_KEY": "test", "SQLALCHEMY_DATABASE_URI": "sqlite://"}
            )
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])

    def test_production_requires_explicit_secret(self):
        with patch.dict("os.environ", {"RPMS_ENV": "production", "SECRET_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "Set SECRET_KEY"):
                create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})

    def test_production_cookie_and_template_defaults(self):
        with patch.dict("os.environ", {"RPMS_ENV": "production"}):
            app = create_app(
                {"SECRET_KEY": "test", "SQLALCHEMY_DATABASE_URI": "sqlite://"}
            )
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertFalse(app.config["TEMPLATES_AUTO_RELOAD"])

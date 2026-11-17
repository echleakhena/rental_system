"""Verify transfer preserves UTC timestamps and linked records."""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select

from rpms import create_app
from rpms.models import MODELS, db
from scripts.configure_sqlite import transfer


class SqliteTransferTests(unittest.TestCase):
    def test_all_tables_and_timezone_normalization(self):
        app = create_app(
            {"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test"}
        )
        source = create_engine("sqlite:///:memory:")
        destination = create_engine("sqlite:///:memory:")
        try:
            with app.app_context():
                db.metadata.create_all(source)
                db.metadata.create_all(destination)
                stamp = datetime(2026, 11, 17, 12, tzinfo=timezone(timedelta(hours=7)))
                # SQLite strips offsets, so exercise normalization explicitly as well.
                from scripts.configure_sqlite import normalized

                self.assertEqual(normalized(stamp), datetime(2026, 11, 17, 5))
                with source.begin() as connection:
                    connection.execute(
                        MODELS["roles"].__table__.insert(),
                        {"id": 12, "name": "Viewer", "created_at": normalized(stamp)},
                    )
                    connection.execute(
                        MODELS["users"].__table__.insert(),
                        {"id": 21, "name": "Test", "role_id": 12},
                    )
                counts = transfer(source, destination)
                self.assertEqual(len(counts), 28)
                self.assertEqual(sum(counts.values()), 2)
                with destination.connect() as connection:
                    self.assertEqual(
                        connection.scalar(select(MODELS["users"].__table__.c.role_id)),
                        12,
                    )
                    self.assertEqual(
                        connection.scalar(
                            select(MODELS["roles"].__table__.c.created_at)
                        ),
                        datetime(2026, 11, 17, 5),
                    )
                db.engine.dispose()
        finally:
            source.dispose()
            destination.dispose()

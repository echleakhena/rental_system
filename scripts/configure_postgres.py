"""Configure PostgreSQL and optionally copy the current SQLite data, without deleting it.

Run from the project root: .venv/bin/python scripts/configure_postgres.py
"""

import getpass
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import URL

from rpms import create_app
from rpms.models import db


def main():
    settings = {
        "database": "rental_system",
        "host": input("PostgreSQL host [127.0.0.1]: ").strip() or "127.0.0.1",
        "port": int(input("PostgreSQL port [5432]: ").strip() or "5432"),
        "username": input("PostgreSQL username [postgres]: ").strip() or "postgres",
        "password": getpass.getpass("PostgreSQL password (hidden): "),
    }
    url = URL.create(
        "postgresql+psycopg",
        username=settings["username"],
        password=settings["password"] or None,
        host=settings["host"],
        port=settings["port"],
        database=settings["database"],
    )
    instance = ROOT / "instance"
    source_path = instance / "rpms.db"
    copy_data = (
        source_path.exists()
        and input("Copy current SQLite records? [Y/n]: ").strip().lower() != "n"
    )
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    source = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        names = set(inspect(engine).get_table_names())
        # Existing application records must never be overwritten or merged implicitly.
        with engine.connect() as conn:
            for name in names.intersection(db.metadata.tables):
                if (
                    conn.scalar(select(db.metadata.tables[name].c.id).limit(1))
                    is not None
                ):
                    raise RuntimeError(
                        "Target already contains application records. No data was changed; use an empty database for this migration."
                    )
        print("PostgreSQL connection verified.")
        if copy_data:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = instance / f"rpms-before-postgres-{stamp}.db"
            with (
                sqlite3.connect(source_path) as origin,
                sqlite3.connect(backup) as destination,
            ):
                origin.backup(destination)
            backup.chmod(0o600)
            print(f"SQLite backup: instance/{backup.name}")
            source = create_engine(URL.create("sqlite", database=str(source_path)))
        app = create_app({"SQLALCHEMY_DATABASE_URI": url})
        result = app.test_cli_runner().invoke(
            args=["db", "upgrade", "--directory", str(ROOT / "migrations")]
        )
        if result.exit_code:
            raise RuntimeError(
                "Schema migration failed. Connection settings were not switched."
            )
        counts = {}
        if copy_data:
            available = set(inspect(source).get_table_names())
            with source.connect() as origin, engine.begin() as destination:
                for table in db.metadata.sorted_tables:
                    if table.name not in available:
                        continue
                    rows = origin.execute(select(table)).mappings().all()
                    if rows:
                        destination.execute(table.insert(), [dict(row) for row in rows])
                    counts[table.name] = len(rows)
                # Explicit SQLite IDs need PostgreSQL sequences advanced after copying.
                quote = engine.dialect.identifier_preparer.quote
                for table in db.metadata.sorted_tables:
                    sequence = destination.scalar(
                        text("SELECT pg_get_serial_sequence(:name, :column)"),
                        {"name": table.name, "column": "id"},
                    )
                    if sequence:
                        maximum = destination.scalar(
                            text(f"SELECT MAX(id) FROM {quote(table.name)}")
                        )
                        destination.execute(
                            text(
                                "SELECT setval(CAST(:sequence AS regclass), :value, :used)"
                            ),
                            {
                                "sequence": sequence,
                                "value": maximum or 1,
                                "used": maximum is not None,
                            },
                        )
                for name, expected in counts.items():
                    actual = destination.scalar(
                        text(f"SELECT COUNT(*) FROM {quote(name)}")
                    )
                    if actual != expected:
                        raise RuntimeError(
                            "Row count verification failed; transfer rolled back."
                        )
            print(
                f"Transferred and verified {sum(counts.values())} records across {len(counts)} tables."
            )
        path = instance / "database.json"
        temporary = instance / "database.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(settings, file, indent=2)
        temporary.chmod(0o600)
        temporary.replace(path)
        print("Application configured for PostgreSQL database rental_system.")
        if os.environ.get("DATABASE_URL"):
            print(
                "DATABASE_URL overrides local settings. Update or unset it before restarting."
            )
        print(
            "Room photos and the original SQLite database remain in instance/. Restart app.py."
        )
    finally:
        engine.dispose()
        if source is not None:
            source.dispose()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
    except RuntimeError as error:
        print(str(error))
        sys.exit(1)
    except Exception as error:
        # Avoid echoing connection strings, passwords, or row values in exceptions.
        print(
            f"PostgreSQL setup failed ({type(error).__name__}). Check connection details and database permissions."
        )
        sys.exit(1)

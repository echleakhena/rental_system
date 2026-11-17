"""Copy the configured database to SQLite, verify it, then switch local settings.

The source is read only. Existing SQLite files and settings are backed up.
Run: .venv/bin/python scripts/configure_sqlite.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import URL, make_url

from rpms import create_app
from rpms.database_config import database_uri
from rpms.models import db


def normalized(value):
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def transfer(source, destination):
    """Copy and verify every application row in one destination transaction."""
    with source.connect() as origin:
        if source.dialect.name == "postgresql":
            origin = origin.execution_options(isolation_level="REPEATABLE READ")
            origin.execute(text("SET TRANSACTION READ ONLY"))
        available = set(inspect(origin).get_table_names())
        missing = set(db.metadata.tables) - available
        if missing:
            raise RuntimeError(
                "Source schema is out of date. Apply migrations before transferring."
            )
        counts = {}
        with destination.begin() as target:
            for table in db.metadata.sorted_tables:
                rows = [
                    dict(row)
                    for row in origin.execute(
                        select(table).order_by(table.c.id)
                    ).mappings()
                ]
                if rows:
                    target.execute(
                        table.insert(),
                        [{k: normalized(v) for k, v in row.items()} for row in rows],
                    )
                copied = [
                    dict(row)
                    for row in target.execute(
                        select(table).order_by(table.c.id)
                    ).mappings()
                ]
                if len(rows) != len(copied) or any(
                    {k: normalized(v) for k, v in a.items()}
                    != {k: normalized(v) for k, v in b.items()}
                    for a, b in zip(rows, copied)
                ):
                    raise RuntimeError(
                        f"Transfer verification failed for {table.name}."
                    )
                counts[table.name] = len(rows)
            if target.execute(text("PRAGMA foreign_key_check")).first():
                raise RuntimeError("Foreign-key verification failed.")
            if target.scalar(text("PRAGMA integrity_check")) != "ok":
                raise RuntimeError("SQLite integrity verification failed.")
        return counts


def main():
    instance = ROOT / "instance"
    instance.mkdir(exist_ok=True)
    source_url = make_url(database_uri(instance))
    target_path = instance / "rpms.db"
    if source_url.get_backend_name() == "sqlite":
        print("SQLite is already configured. No records were changed.")
        return
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "Unset DATABASE_URL first; it overrides the saved SQLite settings."
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    staging = instance / f"rpms-transfer-{stamp}.db"
    target_url = URL.create("sqlite", database=str(staging))
    app = create_app({"SQLALCHEMY_DATABASE_URI": target_url})
    source = create_engine(source_url, connect_args={"connect_timeout": 5})
    destination = create_engine(target_url)
    try:
        result = app.test_cli_runner().invoke(
            args=["db", "upgrade", "--directory", str(ROOT / "migrations")]
        )
        if result.exit_code:
            raise RuntimeError(
                "SQLite schema setup failed. Configuration was not changed."
            )
        counts = transfer(source, destination)
        destination.dispose()
        with app.app_context():
            db.engine.dispose()
        staging.chmod(0o600)
        if target_path.exists():
            backup = instance / f"rpms-before-sqlite-{stamp}.db"
            with sqlite3.connect(target_path) as old, sqlite3.connect(backup) as saved:
                old.backup(saved)
            backup.chmod(0o600)
        settings_path = instance / "database.json"
        if settings_path.exists():
            copy2(settings_path, instance / f"database-before-sqlite-{stamp}.json")
        # Both files are prepared before changing the active local configuration.
        temporary = instance / "database.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump({"driver": "sqlite", "database": "rpms.db"}, file, indent=2)
        temporary.chmod(0o600)
        staging.replace(target_path)
        temporary.replace(settings_path)
        print(
            f"Copied and verified {sum(counts.values())} records across {len(counts)} tables."
        )
        print(
            "SQLite active: instance/rpms.db. Original PostgreSQL records and uploaded images are preserved."
        )
    finally:
        source.dispose()
        destination.dispose()
        with app.app_context():
            db.engine.dispose()
        staging.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not expose database credentials or tenant data in connection errors.
        print(
            str(exc)
            if isinstance(exc, RuntimeError)
            else f"SQLite setup failed ({type(exc).__name__}). Configuration was not switched."
        )
        sys.exit(1)

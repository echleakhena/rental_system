"""Load private local database settings; environment configuration takes priority."""

import json
import os
from pathlib import Path

from sqlalchemy.engine import URL


def database_uri(instance_path):
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    path = Path(instance_path) / "database.json"
    if not path.exists():
        return "sqlite:///rpms.db"
    settings = json.loads(path.read_text())
    if settings.get("driver") == "sqlite":
        return URL.create(
            "sqlite",
            database=str(Path(instance_path) / settings.get("database", "rpms.db")),
        )
    return URL.create(
        "postgresql+psycopg",
        username=settings["username"],
        password=settings.get("password") or None,
        host=settings.get("host", "127.0.0.1"),
        port=int(settings.get("port", 5432)),
        database=settings.get("database", "rental_system"),
    )

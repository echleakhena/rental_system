"""Flask application factory and database initialization."""

import os
import secrets
from pathlib import Path

from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .database_config import database_uri
from .models import db


@event.listens_for(Engine, "connect")
def sqlite_integrity(connection, _):
    if connection.__class__.__module__.startswith("sqlite3"):
        connection.execute("PRAGMA foreign_keys=ON")


def create_app(config=None):
    """Create an app, optionally overriding settings for isolated tests."""
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(exist_ok=True)
    production = os.environ.get("RPMS_ENV", "development") == "production"
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TEMPLATES_AUTO_RELOAD=not production,
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=22 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = database_uri(app.instance_path)
    if not app.config.get("SECRET_KEY"):
        if production:
            raise ValueError("Set SECRET_KEY before starting RPMS in production.")
        keyfile = Path(app.instance_path) / "secret.key"
        try:
            descriptor = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(secrets.token_hex(32))
        app.config["SECRET_KEY"] = keyfile.read_text().strip()
        if not app.config["SECRET_KEY"]:
            raise ValueError("The local secret.key file is empty.")
    from . import customer_models  # noqa: F401

    db.init_app(app)
    Migrate(app, db)
    from .routes import bp

    app.register_blueprint(bp)
    from .customer import customer_bp

    app.register_blueprint(customer_bp)
    from .seed import register_commands

    register_commands(app)
    return app

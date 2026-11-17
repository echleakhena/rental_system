"""Alembic migration setup for Flask-SQLAlchemy 3."""

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")
migrate = current_app.extensions["migrate"]
database = migrate.db
metadata = database.metadata
config.set_main_option(
    "sqlalchemy.url",
    database.engine.url.render_as_string(hide_password=False).replace("%", "%%"),
)


def skip_empty_revision(migration_context, revision, directives):
    """Avoid creating a migration file when the schema has not changed."""
    if getattr(config.cmd_opts, "autogenerate", False) and directives:
        if directives[0].upgrade_ops.is_empty():
            directives.clear()
            logger.info("No schema changes detected.")


def run_migrations_offline():
    """Generate SQL without opening a database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Apply migrations using the application's configured database."""
    options = dict(migrate.configure_args)
    if options.get("process_revision_directives") is None:
        options["process_revision_directives"] = skip_empty_revision
    with database.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, **options)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

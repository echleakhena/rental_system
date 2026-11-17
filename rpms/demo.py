"""Disposable sample data for serverless demonstrations only."""

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .customer_models import CustomerAccount
from .models import MODELS, db


def initialize_demo(app, password):
    """Seed a fresh temporary instance without reading the local installation."""
    runner = app.test_cli_runner()
    for command in (["seed-demo", "--password", password], ["seed-room-photos"]):
        result = runner.invoke(args=command)
        if result.exit_code:
            raise RuntimeError(
                "Could not initialize sample demo data."
            ) from result.exception
    with app.app_context():
        tenant = db.session.scalar(
            select(MODELS["tenants"]).where(MODELS["tenants"].tenant_code == "TEN001")
        )
        db.session.add(
            CustomerAccount(
                tenant_id=tenant.id,
                phone="+85512345678",
                password_hash=generate_password_hash(password),
            )
        )
        db.session.commit()

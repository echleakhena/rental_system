from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import click
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .models import MODELS, db
from .photos import process_photo
from .services import activate, audit, issue_invoice, pay_invoice


def create_admin(email, password):
    role = db.session.scalar(
        select(MODELS["roles"]).where(MODELS["roles"].name == "Admin")
    )
    if not role:
        role = MODELS["roles"](name="Admin", is_active=True)
        db.session.add(role)
        db.session.flush()
    if db.session.scalar(select(MODELS["users"]).where(MODELS["users"].email == email)):
        raise click.ClickException("This email already exists.")
    u = MODELS["users"](
        name="Administrator",
        email=email,
        role_id=role.id,
        password_hash=generate_password_hash(password),
        is_active=True,
    )
    db.session.add(u)
    db.session.commit()
    return u


def register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        click.echo("Created 26 schema tables. Run create-admin or seed-demo next.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.password_option()
    def admin(email, password):
        if len(password) < 10:
            raise click.ClickException("Use at least 10 characters.")
        create_admin(email.strip().lower(), password)
        click.echo("Administrator created.")

    @app.cli.command("seed-demo")
    @click.password_option(
        help="Choose the demo administrator password (at least 10 characters)."
    )
    def seed(password):
        if len(password) < 10:
            raise click.ClickException("Use at least 10 characters.")
        db.create_all()
        if db.session.scalar(select(MODELS["properties"].id)) or db.session.scalar(
            select(MODELS["users"].id)
        ):
            raise click.ClickException("Demo seeding requires an empty database.")
        u = create_admin("admin@rpms.local", password)
        prop = MODELS["properties"](
            property_code="PROP001",
            name="Riverside Residences",
            property_type="Apartment",
            city="Phnom Penh",
            address="Riverside, Phnom Penh",
            status="Active",
            manager_id=u.id,
        )
        db.session.add(prop)
        db.session.flush()
        floor = MODELS["floors"](
            property_id=prop.id, floor_number=1, name="First floor"
        )
        db.session.add(floor)
        db.session.flush()
        rooms = []
        for i in range(1, 13):
            r = MODELS["rooms"](
                floor_id=floor.id,
                room_number=f"A1{i:02}",
                room_type="Studio",
                monthly_rent=150 + i * 10,
                default_deposit=200,
                size_sqm=28,
                status="Maintenance" if i == 12 else "Vacant",
            )
            db.session.add(r)
            rooms.append(r)
        tenant = MODELS["tenants"](
            tenant_code="TEN001",
            full_name="Sok Dara",
            phone="012 345 678",
            email="dara@example.com",
            status="Active",
        )
        db.session.add(tenant)
        db.session.flush()
        now = date.today()
        start = now.replace(day=1)
        end = date(now.year + 1, now.month, 1)
        lease = MODELS["rental_contracts"](
            contract_no="LEASE001",
            room_id=rooms[0].id,
            start_date=start,
            end_date=end,
            monthly_rent=Decimal("150"),
            deposit_required=200,
            billing_day=1,
            payment_due_day=5,
            status="Draft",
            created_by=u.id,
        )
        db.session.add(lease)
        db.session.flush()
        db.session.add(
            MODELS["contract_tenants"](
                contract_id=lease.id,
                tenant_id=tenant.id,
                is_primary=True,
                joined_at=start,
            )
        )
        db.session.flush()
        activate(lease, u.id)
        for kind, rate, previous, current in [
            ("Electricity", "0.25", "1200", "1350"),
            ("Water", "0.50", "80", "90"),
        ]:
            meter = MODELS["utility_meters"](
                room_id=rooms[0].id,
                meter_type=kind,
                meter_number=kind[:1] + "001",
                unit="kWh" if kind == "Electricity" else "m3",
                default_rate=Decimal(rate),
                status="Active",
            )
            db.session.add(meter)
            db.session.flush()
            usage = Decimal(current) - Decimal(previous)
            db.session.add(
                MODELS["meter_readings"](
                    meter_id=meter.id,
                    contract_id=lease.id,
                    reading_date=now,
                    previous_reading=Decimal(previous),
                    current_reading=Decimal(current),
                    usage_quantity=usage,
                    unit_rate=Decimal(rate),
                    total_amount=usage * Decimal(rate),
                    reading_type="Regular",
                    recorded_by=u.id,
                    status="Approved",
                )
            )
        db.session.flush()
        inv = issue_invoice(lease, now.strftime("%Y-%m"), u.id)
        db.session.flush()
        pay_invoice(inv.id, "100", "Cash", "DEMO", u.id)
        db.session.add(
            MODELS["maintenance_requests"](
                request_no="MNT001",
                room_id=rooms[-1].id,
                title="Replace kitchen tap",
                description="Slow leak at the kitchen sink.",
                priority="Medium",
                status="Pending",
            )
        )
        db.session.commit()
        click.echo("Demo ready. Sign in as admin@rpms.local with your chosen password.")

    @app.cli.command("seed-room-photos")
    def seed_room_photos():
        """Fill empty room photo slots with bundled sample interiors."""

        source = Path(__file__).parent / "demo_photos"
        samples = []
        for name in ("interior", "living", "kitchen"):
            with (source / (name + ".jpg")).open("rb") as file:
                samples.append(process_photo(file))
        folder = Path(app.instance_path) / "room_photos"
        folder.mkdir(exist_ok=True)
        created = []
        count = 0
        rooms_updated = 0
        try:
            for index, room in enumerate(
                db.session.scalars(
                    select(MODELS["rooms"]).order_by(MODELS["rooms"].id)
                ).all()
            ):
                photos = (list(room.photos or []) + [None] * 4)[:4]
                changed = False
                for slot in range(3):
                    if photos[slot]:
                        continue
                    filename = "demo-" + uuid4().hex + ".jpg"
                    created.append(filename)
                    (folder / filename).write_bytes(samples[(index + slot) % 3])
                    photos[slot] = filename
                    count += 1
                    changed = True
                if changed:
                    room.photos = photos
                    rooms_updated += 1
                    audit(None, "rooms", room, "Update")
            db.session.commit()
        except Exception:
            db.session.rollback()
            for name in created:
                (folder / name).unlink(missing_ok=True)
            raise
        click.echo(
            f"Added {count} demo photos to {rooms_updated} rooms. Existing photos preserved."
        )

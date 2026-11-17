"""Records page routes."""

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from ..customer_models import CustomerAccount
from ..identifiers import AUTO_REFERENCES
from ..models import MODELS, SCHEMA, db
from ..photos import ROOM_PHOTO_CATEGORIES, remove_room_photo_files, update_room_photos
from ..policies import change_policy
from ..record_config import CUSTOMER_FIELDS, EDITABLE, HIDDEN, REQUIRED, STATUSES
from ..reporting import record_summary
from ..services import audit, money, validate_reading_period
from ..web import bp, can_manage, label, login_required, user


@bp.route("/records/<table>")
@login_required
def records(table):
    if table not in MODELS:
        abort(404)
    if table in ("invoices", "meter_readings"):
        from ..billing_workspace import invoice_workspace

        return invoice_workspace()
    fields = [f for f in SCHEMA[table] if f["name"] not in HIDDEN or f["name"] == "id"]
    if table == "tenants":
        fields = [f for f in fields if f["name"] in CUSTOMER_FIELDS]
    q = request.args.get("q", "").strip()
    query = select(MODELS[table])
    texts = [
        getattr(MODELS[table], f["name"]).ilike("%" + q + "%")
        for f in fields
        if f["type"].startswith(("VARCHAR", "TEXT"))
    ]
    if q and texts:
        query = query.where(or_(*texts))
    page = max(1, request.args.get("page", 1, type=int))
    rows = db.session.scalars(
        query.order_by(MODELS[table].id.desc()).offset((page - 1) * 50).limit(51)
    ).all()
    display = fields[:8]
    lookup = {}
    for f in display:
        if f["fk"]:
            lookup[f["name"]] = {
                r.id: label(
                    r,
                    include_reservation_reference=not (
                        table == "rental_contracts" and f["name"] == "reservation_id"
                    ),
                )
                for r in db.session.scalars(select(MODELS[f["fk"]])).all()
            }

    summary_cards = record_summary(table)
    return render_template(
        "records.html",
        summary_cards=summary_cards,
        table=table,
        fields=display,
        rows=rows[:50],
        more=len(rows) > 50,
        page=page,
        q=q,
        lookup=lookup,
        editable=table in EDITABLE,
        customer_accounts={
            a.tenant_id: a
            for a in db.session.scalars(
                select(CustomerAccount).where(
                    CustomerAccount.tenant_id.in_([r.id for r in rows])
                )
            ).all()
        }
        if table == "tenants"
        else {},
    )


@bp.route("/records/<table>/new", methods=["GET", "POST"])
@bp.route("/records/<table>/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit(table, record_id=None):
    if table not in EDITABLE or not can_manage(table):
        abort(403)
    row = db.get_or_404(MODELS[table], record_id) if record_id else MODELS[table]()
    if table == "rooms" and record_id and request.method == "POST":
        db.session.refresh(row, with_for_update=True)
    if record_id and change_policy(table, row):
        flash(change_policy(table, row), "error")
        return redirect(url_for("main.records", table=table))
    automatic_field = AUTO_REFERENCES.get(table, (None, None))[0]
    reservation_based = table == "rental_contracts" and (
        not record_id or row.reservation_id is not None
    )
    if reservation_based and not record_id and request.method == "GET":
        booking_id = request.args.get("reservation_id", type=int)
        if booking_id:
            booking = db.get_or_404(MODELS["reservations"], booking_id)
            if booking.status != "Confirmed":
                abort(400, "Confirm this booking before preparing its rental.")
            row.reservation_id = booking.id
            row.start_date = booking.expected_move_in
            room = db.session.get(MODELS["rooms"], booking.room_id)
            row.monthly_rent = room.monthly_rent
            row.billing_day = 1
            row.payment_due_day = 5
    fields = [
        f
        for f in SCHEMA[table]
        if f["name"] not in HIDDEN
        and f["name"] != automatic_field
        and not (reservation_based and f["name"] == "room_id")
        and not (table == "users" and f["name"] == "last_login_at")
        and not (
            table in {"contract_renewals", "move_outs"} and f["name"] == "approved_by"
        )
        and not (table == "system_settings" and f["name"] == "updated_by")
        and not (
            table == "contract_renewals"
            and f["name"] in {"previous_end_date", "previous_rent"}
        )
    ]
    customer_account = None
    if table == "tenants":
        fields = [f for f in fields if f["name"] in CUSTOMER_FIELDS]
        customer_account = (
            db.session.scalar(
                select(CustomerAccount).where(CustomerAccount.tenant_id == record_id)
            )
            if record_id
            else None
        )
    choices = {
        f["name"]: [
            (r.id, label(r))
            for r in db.session.scalars(select(MODELS[f["fk"]]).limit(1000)).all()
        ]
        for f in fields
        if f["fk"]
    }
    if table == "tenants":
        choices["gender"] = [(gender, gender) for gender in ("Female", "Male", "Other")]
    original_status = row.status if record_id and hasattr(row, "status") else None
    statuses = list(STATUSES.get(table, []))
    if original_status and original_status not in statuses:
        statuses.append(original_status)
    if request.method == "POST":
        new_photo_files = []
        old_photo_files = []
        try:
            for f in fields:
                n = f["name"]
                v = request.form.get(n, "").strip()
                typ = f["type"]
                if n in REQUIRED.get(table, []) and not v:
                    raise ValueError(n.replace("_", " ").title() + " is required.")
                if n == "status" and v not in statuses:
                    raise ValueError("Invalid status.")
                if typ == "BOOLEAN":
                    value = v == "true"
                elif not v:
                    value = None
                elif typ == "DATE":
                    value = date.fromisoformat(v)
                elif typ == "TIMESTAMPTZ":
                    value = datetime.fromisoformat(v)
                elif typ.startswith(("INTEGER", "SMALLINT", "BIGINT")):
                    value = int(v)
                elif typ.startswith("NUMERIC"):
                    value = Decimal(v)
                    if not value.is_finite() or value < 0:
                        raise ValueError(
                            "Numeric values must be finite and nonnegative."
                        )
                elif typ == "JSONB":
                    value = json.loads(v)
                else:
                    value = v
                    if typ.startswith("VARCHAR"):
                        if len(v) > int(re.search(r"\d+", typ)[0]):
                            raise ValueError(n + " is too long.")
                if (
                    f["fk"]
                    and value is not None
                    and not db.session.get(MODELS[f["fk"]], value)
                ):
                    raise ValueError("Select an existing " + n + ".")
                setattr(row, n, value)
            if table == "tenants":
                from ..customer import phone_number

                if row.gender not in ("Female", "Male", "Other"):
                    raise ValueError("Choose a valid gender.")
                row.phone = phone_number(row.phone)
                password = request.form.get("password", "")
                if not customer_account and not password:
                    raise ValueError("Set a password for customer sign-in.")
                if password and not 8 <= len(password) <= 128:
                    raise ValueError("Use a password between 8 and 128 characters.")
                collision = db.session.scalar(
                    select(CustomerAccount.id).where(
                        CustomerAccount.phone == row.phone,
                        CustomerAccount.tenant_id != (record_id or 0),
                    )
                )
                if collision:
                    raise ValueError(
                        "This phone number already belongs to another customer account."
                    )
                row.status = row.status or "Active"
            if table == "users":
                row.email = row.email.lower()
                if "@" not in row.email:
                    raise ValueError("Enter a valid email address.")
                password = request.form.get("password", "")
                if not record_id and not password:
                    raise ValueError("Password is required for a new user.")
                if password:
                    if len(password) < 8:
                        raise ValueError("Password must contain at least 8 characters.")
                    row.password_hash = generate_password_hash(password)
            if table == "roles" and row.name in {
                "Admin",
                "Manager",
                "Cashier",
                "Viewer",
            }:
                raise ValueError("This name is reserved for a built-in role.")
            if table == "system_settings":
                row.updated_by = user().id
            if table == "contract_renewals":
                lease = db.session.get(MODELS["rental_contracts"], row.contract_id)
                row.previous_end_date = lease.end_date
                row.previous_rent = lease.monthly_rent
                if not lease.end_date or row.new_end_date <= lease.end_date:
                    raise ValueError(
                        "The new end date must follow the current lease end date."
                    )
            if table == "move_outs":
                if row.move_out_date < row.notice_date:
                    raise ValueError(
                        "Move-out date must be on or after the notice date."
                    )
                if row.inspection_id:
                    inspection = db.session.get(
                        MODELS["room_inspections"], row.inspection_id
                    )
                    if inspection.contract_id != row.contract_id:
                        raise ValueError(
                            "Inspection must belong to the selected contract."
                        )
            if (
                table == "rooms"
                and original_status in ("Occupied", "Reserved")
                and row.status != original_status
            ):
                raise ValueError(
                    "Use the lease or reservation workflow to change occupancy."
                )
            for attr in ("created_by", "recorded_by", "inspected_by"):
                if hasattr(row, attr):
                    setattr(row, attr, user().id)
            if table == "rental_contracts":
                if reservation_based:
                    booking = (
                        db.session.scalar(
                            select(MODELS["reservations"])
                            .where(MODELS["reservations"].id == row.reservation_id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                        if row.reservation_id
                        else None
                    )
                    if not booking or not booking.room_id or not booking.tenant_id:
                        raise ValueError(
                            "Select a reservation with a customer and room."
                        )
                    if booking.status != "Confirmed":
                        raise ValueError(
                            "Confirm the booking before preparing its rental."
                        )
                    duplicate = db.session.scalar(
                        select(MODELS["rental_contracts"].id).where(
                            MODELS["rental_contracts"].reservation_id == booking.id,
                            MODELS["rental_contracts"].id != (record_id or 0),
                            MODELS["rental_contracts"].status.in_(["Draft", "Active"]),
                        )
                    )
                    if duplicate:
                        raise ValueError(
                            "This booking already has a rental. Open the existing rental to continue."
                        )
                    row.room_id = booking.room_id
                if row.end_date <= row.start_date:
                    raise ValueError("Lease end must follow start.")
                if not (1 <= row.billing_day <= 28 and 1 <= row.payment_due_day <= 28):
                    raise ValueError("Billing and due days must be 1–28.")
            if table == "contract_tenants":
                lease = db.session.get(MODELS["rental_contracts"], row.contract_id)
                if lease.status != "Draft":
                    raise ValueError("Tenants can only be linked to a draft lease.")
                if row.is_primary and db.session.scalar(
                    select(MODELS["contract_tenants"].id).where(
                        MODELS["contract_tenants"].contract_id == row.contract_id,
                        MODELS["contract_tenants"].is_primary == True,
                        MODELS["contract_tenants"].id != (record_id or 0),
                    )
                ):
                    raise ValueError("This lease already has a primary tenant.")
            if table == "meter_readings":
                meter = db.session.get(MODELS["utility_meters"], row.meter_id)
                lease = db.session.get(MODELS["rental_contracts"], row.contract_id)
                db.session.refresh(lease, with_for_update=True)
                validate_reading_period(lease, row.reading_date)
                if meter.room_id != lease.room_id:
                    raise ValueError("Meter and lease must belong to the same room.")
                last = db.session.scalar(
                    select(MODELS["meter_readings"])
                    .where(
                        MODELS["meter_readings"].meter_id == row.meter_id,
                        MODELS["meter_readings"].id != (record_id or 0),
                    )
                    .order_by(
                        MODELS["meter_readings"].reading_date.desc(),
                        MODELS["meter_readings"].id.desc(),
                    )
                )
                if row.current_reading < row.previous_reading or (
                    last
                    and (
                        row.previous_reading != last.current_reading
                        or row.reading_date <= last.reading_date
                    )
                ):
                    raise ValueError(
                        "Reading must continue the last reading, with a later date and no decrease."
                    )
                row.usage_quantity = row.current_reading - row.previous_reading
                row.total_amount = money(row.usage_quantity * row.unit_rate)
            if table == "rooms":
                update_room_photos(
                    row, request.files, request.form, new_photo_files, old_photo_files
                )
            db.session.add(row)
            db.session.flush()
            if table == "tenants":
                if not customer_account:
                    customer_account = CustomerAccount(
                        tenant_id=row.id,
                        phone=row.phone,
                        password_hash=generate_password_hash(password),
                        is_active=True,
                    )
                    db.session.add(customer_account)
                else:
                    customer_account.phone = row.phone
                    if password:
                        customer_account.password_hash = generate_password_hash(
                            password
                        )
            if reservation_based:
                primary = db.session.scalar(
                    select(MODELS["contract_tenants"]).where(
                        MODELS["contract_tenants"].contract_id == row.id,
                        MODELS["contract_tenants"].is_primary == True,
                    )
                )
                customer_link = db.session.scalar(
                    select(MODELS["contract_tenants"]).where(
                        MODELS["contract_tenants"].contract_id == row.id,
                        MODELS["contract_tenants"].tenant_id == booking.tenant_id,
                    )
                )
                if primary and primary != customer_link:
                    primary.is_primary = False
                if customer_link:
                    customer_link.is_primary = True
                else:
                    db.session.add(
                        MODELS["contract_tenants"](
                            contract_id=row.id,
                            tenant_id=booking.tenant_id,
                            is_primary=True,
                        )
                    )
            audit(user().id, table, row, "Update" if record_id else "Create")
            db.session.commit()
            remove_room_photo_files(old_photo_files)
            flash("Record saved.", "success")
            return redirect(
                url_for("main.room_detail", record_id=row.id)
                if table == "rooms"
                else url_for("main.record_detail", table=table, record_id=row.id)
                if table == "rental_contracts"
                else url_for("main.records", table=table)
            )
        except (ValueError, InvalidOperation, IntegrityError, OSError) as exc:
            db.session.rollback()
            remove_room_photo_files(new_photo_files)
            flash(
                "A unique value already exists or a relationship is invalid."
                if isinstance(exc, IntegrityError)
                else "Room photos could not be saved. Please try again."
                if isinstance(exc, OSError)
                else str(exc),
                "error",
            )
    return render_template(
        "form.html",
        table=table,
        row=row,
        fields=fields,
        choices=choices,
        statuses=statuses,
        required=REQUIRED.get(table, [])
        + (["reservation_id"] if reservation_based else []),
        editing=record_id is not None,
        customer_has_account=customer_account is not None,
        customer_password_required=table == "tenants" and customer_account is None,
        photo_categories=ROOM_PHOTO_CATEGORIES,
        photos=(list(row.photos or []) + [None] * len(ROOM_PHOTO_CATEGORIES))[
            : len(ROOM_PHOTO_CATEGORIES)
        ]
        if table == "rooms"
        else [],
    )


@bp.get("/records/<table>/<int:record_id>")
@login_required
def record_detail(table, record_id):
    if table not in MODELS:
        abort(404)
    if table == "rooms":
        return redirect(url_for("main.room_detail", record_id=record_id))
    if table == "invoices":
        return redirect(url_for("main.invoice", record_id=record_id))
    row = db.get_or_404(MODELS[table], record_id)
    if table == "rental_contracts":
        room = db.session.get(MODELS["rooms"], row.room_id) if row.room_id else None
        floor = (
            db.session.get(MODELS["floors"], room.floor_id)
            if room and room.floor_id
            else None
        )
        property_record = (
            db.session.get(MODELS["properties"], floor.property_id)
            if floor and floor.property_id
            else None
        )
        links = db.session.scalars(
            select(MODELS["contract_tenants"])
            .where(MODELS["contract_tenants"].contract_id == row.id)
            .order_by(
                MODELS["contract_tenants"].is_primary.desc(),
                MODELS["contract_tenants"].id,
            )
        ).all()
        members = [
            (link, db.session.get(MODELS["tenants"], link.tenant_id)) for link in links
        ]
        lease_invoices = db.session.scalars(
            select(MODELS["invoices"])
            .where(MODELS["invoices"].contract_id == row.id)
            .order_by(
                MODELS["invoices"].billing_period.desc(), MODELS["invoices"].id.desc()
            )
        ).all()
        issued = [i for i in lease_invoices if i.status not in ("Draft", "Voided")]
        deposits = db.session.scalars(
            select(MODELS["deposits"]).where(MODELS["deposits"].contract_id == row.id)
        ).all()
        booking = (
            db.session.get(MODELS["reservations"], row.reservation_id)
            if row.reservation_id
            else None
        )
        month = date.today().replace(day=1)
        if row.start_date and row.end_date:
            month = max(
                row.start_date.replace(day=1), min(month, row.end_date.replace(day=1))
            )
        return render_template(
            "lease_detail.html",
            table=table,
            contract=row,
            room=room,
            floor=floor,
            property_record=property_record,
            members=members,
            booking=booking,
            lease_invoices=lease_invoices,
            collected=sum((i.paid_amount or 0 for i in issued), Decimal(0)),
            balance=sum((i.balance_due or 0 for i in issued), Decimal(0)),
            deposit_held=sum((d.held_amount or 0 for d in deposits), Decimal(0)),
            deposits=deposits,
            billing_month=month.strftime("%Y-%m"),
        )
    fields = []
    for field in SCHEMA[table]:
        name = field["name"]
        if table == "tenants" and name not in CUSTOMER_FIELDS:
            continue
        if name in HIDDEN and name != "id":
            continue
        value = getattr(row, name)
        if field["fk"] and value is not None:
            related = db.session.get(MODELS[field["fk"]], value)
            value = label(related) if related else "Unavailable"
        fields.append((name, value))
    if table == "tenants":
        linked_account = db.session.scalar(
            select(CustomerAccount.id).where(CustomerAccount.tenant_id == row.id)
        )
        fields.append(("password", "••••••••" if linked_account else "Not set"))
    return render_template("record_detail.html", table=table, row=row, details=fields)


@bp.route("/records/<table>/<int:record_id>/delete", methods=["GET", "POST"])
@login_required
def delete_record(table, record_id):
    if table not in MODELS:
        abort(404)
    if not can_manage(table):
        abort(403)
    row = db.get_or_404(MODELS[table], record_id)
    reason = change_policy(table, row)
    if reason:
        flash(reason, "error")
        return redirect(url_for("main.records", table=table))
    if request.method == "GET":
        return render_template(
            "delete_record.html", table=table, row=row, record_label=label(row)
        )
    # Explicit dependency checks complement foreign-key enforcement.
    for name, model in MODELS.items():
        for column in model.__table__.columns:
            if any(fk.target_fullname == table + ".id" for fk in column.foreign_keys):
                if db.session.scalar(
                    select(model.id).where(column == record_id).limit(1)
                ):
                    flash(
                        "This record is used by "
                        + name.replace("_", " ")
                        + ". Update related records first.",
                        "error",
                    )
                    return redirect(url_for("main.records", table=table))
    photos = list(getattr(row, "photos", None) or [])
    try:
        audit(user().id, table, row, "Delete")
        db.session.delete(row)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This record is still referenced and cannot be deleted.", "error")
        return redirect(url_for("main.records", table=table))
    if photos:
        for filename in photos:
            if filename:
                try:
                    (Path(current_app.instance_path) / "room_photos" / filename).unlink(
                        missing_ok=True
                    )
                except OSError:
                    current_app.logger.warning("Could not remove deleted room photo")
    flash("Record deleted.", "success")
    return redirect(url_for("main.records", table=table))

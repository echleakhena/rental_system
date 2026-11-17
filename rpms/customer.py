"""Mobile customer portal and staff review of customer-originated records."""

import re
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .customer_finances import invoice_breakdowns, payment_history
from .customer_models import CustomerAccount, PaymentProof
from .models import MODELS as M
from .models import db
from .photos import ROOM_PHOTO_CATEGORIES, process_photo
from .services import (
    approve_reading,
    audit,
    invoice_reading_details,
    money,
    pay_invoice,
    validate_reading_period,
)
from .web import can_manage, can_write, login_required, user

customer_bp = Blueprint("customer", __name__)


def account():
    a = (
        db.session.get(CustomerAccount, session.get("customer_id"))
        if session.get("customer_id")
        else None
    )
    tenant = db.session.get(M["tenants"], a.tenant_id) if a else None
    return a if a and a.is_active and tenant and tenant.status == "Active" else None


def customer_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not account():
            return redirect(url_for("customer.login"))
        return fn(*args, **kwargs)

    return wrapped


def phone_number(raw):
    raw = re.sub(r"[\s()\-]", "", raw.strip())
    # Cambodian domestic and international spellings identify the same account.
    if raw.startswith("00855"):
        raw = "+855" + raw[5:]
    if raw.startswith("0"):
        raw = "+855" + raw[1:]
    if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", raw):
        raise ValueError(
            "Enter a valid phone number, including country code if outside Cambodia."
        )
    return raw


def leases_for(a):
    return db.session.scalars(
        select(M["rental_contracts"])
        .join(
            M["contract_tenants"],
            M["contract_tenants"].contract_id == M["rental_contracts"].id,
        )
        .where(M["contract_tenants"].tenant_id == a.tenant_id)
        .distinct()
        .order_by(M["rental_contracts"].start_date.desc())
    ).all()


def own_lease(lease_id, current=False):
    lease = next((x for x in leases_for(account()) if x.id == lease_id), None)
    if not lease:
        abort(404)
    link = db.session.scalar(
        select(M["contract_tenants"]).where(
            M["contract_tenants"].contract_id == lease.id,
            M["contract_tenants"].tenant_id == account().tenant_id,
        )
    )
    if current and (
        lease.status != "Active"
        or not lease.start_date <= date.today() <= lease.end_date
        or (link.left_at and link.left_at < date.today())
    ):
        abort(403)
    return lease


def own_invoice(invoice_id):
    inv = db.get_or_404(M["invoices"], invoice_id)
    own_lease(inv.contract_id)
    if inv.status in ("Draft", "Voided"):
        abort(404)
    return inv


def public_room_query():
    """Shared visibility rules for room lists and individual room lookups."""
    return (
        select(M["rooms"])
        .join(M["floors"], M["rooms"].floor_id == M["floors"].id)
        .join(M["properties"], M["floors"].property_id == M["properties"].id)
        .where(M["rooms"].status == "Vacant", M["properties"].status == "Active")
        .order_by(M["rooms"].monthly_rent, M["rooms"].room_number)
    )


def public_rooms():
    return db.session.scalars(public_room_query()).all()


def public_room(room_id):
    return db.session.scalar(public_room_query().where(M["rooms"].id == room_id))


def room_location(room):
    floor = db.session.get(M["floors"], room.floor_id)
    prop = db.session.get(M["properties"], floor.property_id) if floor else None
    return prop.name if prop else "RPMS residence"


def store_image(upload):
    if not upload or not upload.filename:
        raise ValueError("Choose a photo to upload.")
    data = process_photo(upload)
    folder = Path(current_app.instance_path) / "customer_uploads"
    folder.mkdir(exist_ok=True)
    name = uuid4().hex + ".jpg"
    (folder / name).write_bytes(data)
    return name


def remove_image(name):
    if name:
        (Path(current_app.instance_path) / "customer_uploads" / name).unlink(
            missing_ok=True
        )


def qr_setting():
    row = db.session.scalar(
        select(M["system_settings"]).where(
            M["system_settings"].setting_key == "customer_payment_qr"
        )
    )
    return row


@customer_bp.context_processor
def customer_context():
    a = account()
    return dict(
        photo_categories=ROOM_PHOTO_CATEGORIES,
        customer_account=a,
        tenant=db.session.get(M["tenants"], a.tenant_id) if a else None,
        room_location=room_location,
        room_by_id=lambda rid: db.session.get(M["rooms"], rid),
    )


@customer_bp.after_request
def private_response(response):
    if request.path.startswith("/customer"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@customer_bp.get("/customer")
@customer_bp.get("/customer/")
def home():
    rooms = public_rooms()
    a = account()
    leases = leases_for(a) if a else []
    activity = []
    outstanding = Decimal(0)
    pending_bookings = 0

    def event(title, detail, when, icon, href, status):
        if isinstance(when, datetime):
            when = when.replace(tzinfo=timezone.utc) if not when.tzinfo else when
            from zoneinfo import ZoneInfo

            when = when.astimezone(ZoneInfo("Asia/Phnom_Penh")).date()
        activity.append(
            dict(
                title=title,
                detail=detail,
                date=when or date.today(),
                icon=icon,
                href=href,
                status=status,
            )
        )

    if a:
        bookings = db.session.scalars(
            select(M["reservations"])
            .where(M["reservations"].tenant_id == a.tenant_id)
            .order_by(M["reservations"].id.desc())
            .limit(10)
        ).all()
        pending_bookings = sum(b.status == "Pending" for b in bookings)
        for b in bookings:
            room = db.session.get(M["rooms"], b.room_id)
            event(
                "Booking " + b.status.lower(),
                f"Room {room.room_number if room else '—'} · Move-in {b.expected_move_in}",
                b.updated_at or b.created_at,
                "rentals",
                url_for("customer.rentals"),
                b.status,
            )
        lease_ids = [contract.id for contract in leases]
        bills = db.session.scalars(
            select(M["invoices"])
            .where(
                M["invoices"].contract_id.in_(lease_ids),
                M["invoices"].status.notin_(["Draft", "Voided"]),
            )
            .order_by(M["invoices"].billing_period.desc())
        ).all()
        outstanding = sum((b.balance_due or Decimal(0) for b in bills), Decimal(0))
        for b in bills[:5]:
            event(
                "Monthly bill " + ("paid" if b.status == "Paid" else "ready"),
                f"{b.billing_period.strftime('%B %Y')} · ${b.total_amount:,.2f}",
                b.updated_at or b.invoice_date,
                "receipt",
                url_for("customer.invoice", invoice_id=b.id),
                b.status,
            )
        readings = db.session.scalars(
            select(M["meter_readings"])
            .where(
                M["meter_readings"].contract_id.in_(lease_ids),
                M["meter_readings"].reading_type == "Customer",
            )
            .order_by(M["meter_readings"].id.desc())
            .limit(5)
        ).all()
        for r in readings:
            event(
                "Meter reading "
                + ("submitted" if r.status == "Draft" else r.status.lower()),
                f"{r.reading_date} · {r.current_reading} units",
                r.updated_at or r.reading_date,
                "electric",
                url_for("customer.rentals") + "#utilities",
                "In review" if r.status == "Draft" else r.status,
            )
        event(
            "Welcome to your new space",
            "Your resident account is ready to go.",
            a.created_at,
            "profile",
            url_for("customer.profile"),
            "Account created",
        )
    activity.sort(key=lambda e: e["date"], reverse=True)
    room_types = sorted({r.room_type for r in rooms if r.room_type})
    return render_template(
        "customer/home.html",
        nav="home",
        rooms=rooms[:3],
        available=len(rooms),
        leases=leases,
        activity=activity[:4],
        outstanding=outstanding,
        pending_bookings=pending_bookings,
        room_types=room_types,
        min_rent=min(
            (r.monthly_rent for r in rooms if r.monthly_rent is not None), default=None
        ),
    )


@customer_bp.route("/customer/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            phone = phone_number(request.form.get("phone", ""))
            password = request.form.get("password", "")
            if not name or len(name) > 150:
                raise ValueError("Enter your name (up to 150 characters).")
            if not 8 <= len(password) <= 128:
                raise ValueError("Use a password between 8 and 128 characters.")
            if db.session.scalar(
                select(CustomerAccount.id).where(CustomerAccount.phone == phone)
            ):
                raise ValueError(
                    "This phone number already has an account. Please sign in."
                )
            # Never claim an existing tenant's history merely by knowing their phone.
            tenant = M["tenants"](full_name=name, phone=phone, status="Active")
            db.session.add(tenant)
            db.session.flush()
            a = CustomerAccount(
                tenant_id=tenant.id,
                phone=phone,
                password_hash=generate_password_hash(password),
            )
            db.session.add(a)
            db.session.commit()
            session.clear()
            session.update(customer_id=a.id, csrf=secrets.token_hex(32))
            flash("Welcome! Your account is ready.", "success")
            return redirect(url_for("customer.home"))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(
                str(exc)
                if isinstance(exc, ValueError)
                else "This phone already has an account.",
                "error",
            )
    return render_template("customer/auth.html", registering=True, nav="profile")


@customer_bp.route("/customer/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            phone = phone_number(request.form.get("phone", ""))
        except ValueError:
            phone = ""
        a = db.session.scalar(
            select(CustomerAccount).where(CustomerAccount.phone == phone)
        )
        password = request.form.get("password", "")
        if (
            a
            and a.is_active
            and len(password) <= 128
            and check_password_hash(a.password_hash, password)
        ):
            tenant = db.session.get(M["tenants"], a.tenant_id)
            if tenant and tenant.status == "Active":
                session.clear()
                session.update(customer_id=a.id, csrf=secrets.token_hex(32))
                return redirect(url_for("customer.home"))
        flash("Phone number or password is incorrect.", "error")
    return render_template("customer/auth.html", registering=False, nav="profile")


@customer_bp.post("/customer/logout")
def logout():
    session.pop("customer_id", None)
    session["csrf"] = secrets.token_hex(32)
    return redirect(url_for("customer.home"))


@customer_bp.get("/customer/rooms")
def rooms():
    rows = public_rooms()
    room_types = sorted({r.room_type for r in rows if r.room_type})
    selected_type = request.args.get("type", "")
    if selected_type:
        rows = [r for r in rows if r.room_type == selected_type]
    q = request.args.get("q", "").strip().lower()
    if q:
        rows = [
            r
            for r in rows
            if q
            in (
                room_location(r) + " " + r.room_number + " " + (r.room_type or "")
            ).lower()
        ]
    return render_template(
        "customer/rooms.html",
        rooms=rows,
        nav="rooms",
        q=q,
        room_types=room_types,
        selected_type=selected_type,
    )


@customer_bp.get("/customer/rooms/<int:room_id>")
def room(room_id):
    row = public_room(room_id)
    if not row:
        abort(404)
    meters = db.session.scalars(
        select(M["utility_meters"]).where(
            M["utility_meters"].room_id == row.id,
            M["utility_meters"].status == "Active",
        )
    ).all()
    return render_template("customer/room.html", room=row, meters=meters, nav="rooms")


@customer_bp.get("/customer/rooms/<int:room_id>/photo/<int:slot>")
def room_photo(room_id, slot):
    row = db.get_or_404(M["rooms"], room_id)
    public = public_room(room_id) is not None
    owned = account() and (
        any(contract.room_id == room_id for contract in leases_for(account()))
        or db.session.scalar(
            select(M["reservations"].id).where(
                M["reservations"].room_id == room_id,
                M["reservations"].tenant_id == account().tenant_id,
            )
        )
    )
    if not public and not owned:
        abort(404)
    if (
        slot not in range(len(ROOM_PHOTO_CATEGORIES))
        or slot >= len(row.photos or [])
        or not row.photos[slot]
    ):
        abort(404)
    return send_from_directory(
        Path(current_app.instance_path) / "room_photos",
        row.photos[slot],
        mimetype="image/jpeg",
    )


@customer_bp.post("/customer/rooms/<int:room_id>/book")
@customer_required
def book(room_id):
    try:
        row = db.session.scalar(
            select(M["rooms"]).where(M["rooms"].id == room_id).with_for_update()
        )
        if not row or public_room(room_id) is None:
            raise ValueError("This room is no longer available.")
        move_in = date.fromisoformat(request.form.get("move_in", ""))
        if move_in < date.today():
            raise ValueError("Choose today or a future move-in date.")
        if db.session.scalar(
            select(M["reservations"].id).where(
                M["reservations"].tenant_id == account().tenant_id,
                M["reservations"].room_id == room_id,
                M["reservations"].status.in_(["Pending", "Confirmed"]),
            )
        ):
            raise ValueError("You already have an open booking for this room.")
        booking = M["reservations"](
            room_id=room_id,
            tenant_id=account().tenant_id,
            reservation_date=date.today(),
            expected_move_in=move_in,
            reservation_fee=0,
            status="Pending",
            notes="Requested through customer portal",
        )
        db.session.add(booking)
        db.session.flush()
        audit(None, "reservations", booking)
        db.session.commit()
        flash("Booking requested. Management will confirm availability.", "success")
        return redirect(url_for("customer.rentals"))
    except (ValueError, IntegrityError):
        db.session.rollback()
        flash(
            "Unable to book. Check your move-in date and existing bookings; the room may no longer be available.",
            "error",
        )
        return redirect(url_for("customer.rooms"))


@customer_bp.post("/customer/bookings/<int:booking_id>/cancel")
@customer_required
def cancel_booking(booking_id):
    booking = db.get_or_404(M["reservations"], booking_id)
    if booking.tenant_id != account().tenant_id:
        abort(404)
    if booking.status == "Pending":
        booking.status = "Cancelled"
        audit(None, "reservations", booking, "Cancel")
        db.session.commit()
        flash("Booking cancelled.", "success")
    return redirect(url_for("customer.rentals"))


@customer_bp.get("/customer/rentals")
@customer_required
def rentals():
    from .billing_workspace import period_bounds

    try:
        month, following = period_bounds(
            request.args.get("period", date.today().strftime("%Y-%m"))
        )
    except ValueError as exc:
        abort(400, str(exc))
    leases = leases_for(account())
    departed = set(
        db.session.scalars(
            select(M["contract_tenants"].contract_id).where(
                M["contract_tenants"].tenant_id == account().tenant_id,
                M["contract_tenants"].left_at < date.today(),
            )
        ).all()
    )
    current = [
        contract
        for contract in leases
        if contract.status == "Active"
        and contract.start_date <= date.today() <= contract.end_date
        and contract.id not in departed
    ]
    invoices = db.session.scalars(
        select(M["invoices"])
        .where(
            M["invoices"].contract_id.in_([contract.id for contract in leases]),
            M["invoices"].status.notin_(["Draft", "Voided"]),
        )
        .order_by(M["invoices"].billing_period.desc())
    ).all()
    bookings = db.session.scalars(
        select(M["reservations"])
        .where(M["reservations"].tenant_id == account().tenant_id)
        .order_by(M["reservations"].id.desc())
    ).all()
    meters = db.session.scalars(
        select(M["utility_meters"]).where(
            M["utility_meters"].room_id.in_([contract.room_id for contract in current]),
            M["utility_meters"].status == "Active",
        )
    ).all()
    readings = db.session.scalars(
        select(M["meter_readings"])
        .where(
            M["meter_readings"].contract_id.in_([contract.id for contract in leases])
        )
        .order_by(
            M["meter_readings"].reading_date.desc(), M["meter_readings"].id.desc()
        )
    ).all()
    pending_proofs = set(
        db.session.scalars(
            select(PaymentProof.invoice_id).where(
                PaymentProof.invoice_id.in_([i.id for i in invoices]),
                PaymentProof.status == "Pending",
            )
        )
    )
    upcoming = [
        lease
        for lease in leases
        if lease.status == "Active" and lease.start_date > date.today()
    ]
    drafts = [lease for lease in leases if lease.status == "Draft"]
    latest = {}
    for reading in readings:
        latest.setdefault(reading.meter_id, reading)
    billed_current = {
        i.contract_id
        for i in invoices
        if i.billing_period == date.today().replace(day=1)
    }
    breakdowns = invoice_breakdowns(invoices)
    monthly_invoices = [
        invoice for invoice in invoices if invoice.billing_period == month
    ]
    monthly = {
        key: sum(
            (breakdowns[invoice.id][key] for invoice in monthly_invoices), Decimal(0)
        )
        for key in ("rent", "water", "electricity", "other")
    }
    monthly.update(
        total=sum(
            (invoice.total_amount or 0 for invoice in monthly_invoices), Decimal(0)
        ),
        paid=sum(
            (invoice.paid_amount or 0 for invoice in monthly_invoices), Decimal(0)
        ),
        due=sum((invoice.balance_due or 0 for invoice in monthly_invoices), Decimal(0)),
    )
    return render_template(
        "customer/rentals.html",
        nav="rentals",
        leases=leases,
        current=current,
        upcoming=upcoming,
        drafts=drafts,
        latest_readings=latest,
        billed_current=billed_current,
        pending_proofs=pending_proofs,
        current_month_complete=bool(current)
        and all(lease.id in billed_current for lease in current),
        readings_awaiting_review=any(
            reading.status == "Draft"
            and reading.contract_id in {lease.id for lease in current}
            for reading in readings
        ),
        invoices=invoices,
        bookings=bookings,
        meters=meters,
        readings=readings,
        selected_month=month,
        monthly=monthly,
        monthly_invoices=monthly_invoices,
        bill_breakdowns=breakdowns,
        outstanding=sum((invoice.balance_due or 0 for invoice in invoices), Decimal(0)),
        payments=payment_history([invoice.id for invoice in invoices]),
        scheduled_rent=sum(
            (
                lease.monthly_rent or 0
                for lease in leases
                if lease.status == "Active"
                and lease.start_date < following
                and lease.end_date >= month
            ),
            Decimal(0),
        ),
    )


@customer_bp.post("/customer/rentals/<int:lease_id>/readings")
@customer_required
def submit_reading(lease_id):
    lease = own_lease(lease_id, current=True)
    db.session.refresh(lease, with_for_update=True)
    own_lease(lease_id, current=True)
    filename = None
    try:
        meter_id = int(request.form.get("meter_id", "0"))
        meter = db.session.scalar(
            select(M["utility_meters"])
            .where(M["utility_meters"].id == meter_id)
            .with_for_update()
        )
        if not meter or meter.room_id != lease.room_id or meter.status != "Active":
            raise ValueError("Choose a meter belonging to your room.")
        reading_date = date.fromisoformat(request.form.get("reading_date", ""))
        validate_reading_period(lease, reading_date)
        value = Decimal(request.form.get("current_reading", ""))
        if (
            not value.is_finite()
            or value < 0
            or value > Decimal("99999999999.999")
            or value.as_tuple().exponent < -3
        ):
            raise ValueError("Enter a valid meter reading with up to 3 decimal places.")
        last = db.session.scalar(
            select(M["meter_readings"])
            .where(M["meter_readings"].meter_id == meter.id)
            .order_by(
                M["meter_readings"].reading_date.desc(), M["meter_readings"].id.desc()
            )
        )
        if last and (reading_date <= last.reading_date or value < last.current_reading):
            raise ValueError(
                "Your reading must follow the latest reading date and cannot decrease."
            )
        if last and last.status == "Draft":
            raise ValueError(
                "Your previous reading is awaiting review. Please wait for management."
            )
        previous = last.current_reading if last else Decimal(0)
        rate = meter.default_rate or Decimal(0)
        filename = store_image(request.files.get("photo"))
        reading = M["meter_readings"](
            meter_id=meter.id,
            contract_id=lease.id,
            reading_date=reading_date,
            previous_reading=previous,
            current_reading=value,
            usage_quantity=value - previous,
            unit_rate=rate,
            total_amount=money((value - previous) * rate),
            photo_path=filename,
            reading_type="Customer",
            status="Draft",
        )
        db.session.add(reading)
        db.session.flush()
        audit(None, "meter_readings", reading)
        db.session.commit()
        flash("Reading and photo sent to management for review.", "success")
    except (ValueError, InvalidOperation, IntegrityError, OSError) as exc:
        db.session.rollback()
        remove_image(filename)
        flash(
            str(exc)
            if isinstance(exc, ValueError)
            else "Could not save your reading. Please try again.",
            "error",
        )
    return redirect(url_for("customer.rentals"))


@customer_bp.route("/customer/invoices/<int:invoice_id>", methods=["GET", "POST"])
@customer_required
def invoice(invoice_id):
    inv = own_invoice(invoice_id)
    filename = None
    if request.method == "POST":
        try:
            # Lock the invoice to serialize proof submission and staff confirmation.
            inv = db.session.scalar(
                select(M["invoices"])
                .where(M["invoices"].id == invoice_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            amount = money(request.form.get("amount", ""))
            reference = request.form.get("reference", "").strip()
            if not reference or len(reference) > 100:
                raise ValueError(
                    "Enter your bank transaction reference (up to 100 characters)."
                )
            if (
                inv.status in ("Draft", "Voided", "Paid")
                or amount <= 0
                or amount > inv.balance_due
            ):
                raise ValueError(
                    "Payment must be positive and no more than the balance."
                )
            if db.session.scalar(
                select(PaymentProof.id).where(
                    PaymentProof.invoice_id == inv.id, PaymentProof.status == "Pending"
                )
            ):
                raise ValueError("A payment for this bill is already awaiting review.")
            if not qr_setting():
                raise ValueError("Management has not configured the payment QR yet.")
            filename = store_image(request.files.get("photo"))
            db.session.add(
                PaymentProof(
                    customer_id=account().id,
                    invoice_id=inv.id,
                    amount=amount,
                    reference=reference,
                    photo_path=filename,
                    status="Pending",
                )
            )
            db.session.commit()
            flash(
                "Payment proof sent. Your balance updates after management confirms receipt.",
                "success",
            )
            return redirect(url_for("customer.invoice", invoice_id=inv.id))
        except (ValueError, IntegrityError, OSError) as exc:
            db.session.rollback()
            remove_image(filename)
            flash(
                str(exc)
                if isinstance(exc, ValueError)
                else "Could not save payment proof.",
                "error",
            )
    items = db.session.scalars(
        select(M["invoice_items"]).where(M["invoice_items"].invoice_id == inv.id)
    ).all()
    proofs = db.session.scalars(
        select(PaymentProof)
        .where(
            PaymentProof.invoice_id == inv.id, PaymentProof.customer_id == account().id
        )
        .order_by(PaymentProof.id.desc())
    ).all()
    return render_template(
        "customer/invoice.html",
        nav="rentals",
        invoice=inv,
        items=items,
        proofs=proofs,
        reading_details=invoice_reading_details(items),
        qr=qr_setting(),
        pending_payment=db.session.scalar(
            select(PaymentProof.id).where(
                PaymentProof.invoice_id == inv.id, PaymentProof.status == "Pending"
            )
        )
        is not None,
        payments=payment_history([inv.id]),
    )


@customer_bp.route("/customer/profile", methods=["GET", "POST"])
@customer_required
def profile():
    if request.method == "POST":
        a = account()
        if not check_password_hash(
            a.password_hash, request.form.get("current_password", "")
        ):
            flash("Your current password is incorrect.", "error")
        elif not 8 <= len(request.form.get("password", "")) <= 128:
            flash("Use a password between 8 and 128 characters.", "error")
        else:
            a.password_hash = generate_password_hash(request.form["password"])
            db.session.commit()
            session["csrf"] = secrets.token_hex(32)
            flash("Password updated.", "success")
        return redirect(url_for("customer.profile"))
    return render_template("customer/profile.html", nav="profile")


@customer_bp.get("/customer/payment-qr")
@customer_required
def payment_qr():
    setting = qr_setting()
    if not setting or not isinstance(setting.setting_value, dict):
        abort(404)
    return send_from_directory(
        Path(current_app.instance_path) / "customer_uploads",
        setting.setting_value["filename"],
        mimetype="image/jpeg",
    )


@customer_bp.get("/customer/evidence/<kind>/<int:record_id>")
def evidence(kind, record_id):
    staff = user()
    staff_access = bool(
        staff
        and staff.is_active
        and can_manage("meter_readings" if kind == "reading" else "invoices")
    )
    if not staff_access:
        if not account():
            abort(403)
    if kind == "reading":
        row = db.get_or_404(M["meter_readings"], record_id)
        if not staff_access:
            own_lease(row.contract_id)
    elif kind == "payment":
        row = db.get_or_404(PaymentProof, record_id)
        if not staff_access and row.customer_id != account().id:
            abort(404)
    else:
        abort(404)
    if not row.photo_path:
        abort(404)
    return send_from_directory(
        Path(current_app.instance_path) / "customer_uploads",
        row.photo_path,
        mimetype="image/jpeg",
    )


@customer_bp.route("/customer-management", methods=["GET", "POST"])
@login_required
def management():
    if not can_write():
        abort(403)
    filename = None
    if request.method == "POST":
        try:
            action = request.form.get("action")
            action_tables = {
                "qr": "invoices",
                "confirm_booking": "reservations",
                "reject_booking": "reservations",
                "approve_reading": "meter_readings",
                "confirm_payment": "invoices",
                "reject_payment": "invoices",
                "link_tenant": "tenants",
            }
            if action not in action_tables or not can_manage(action_tables[action]):
                abort(403)
            record_id = int(request.form.get("record_id", "0"))
            if action == "qr":
                filename = store_image(request.files.get("photo"))
                setting = qr_setting() or M["system_settings"](
                    setting_key="customer_payment_qr"
                )
                setting.setting_value = {"filename": filename}
                setting.description = "Customer portal bank payment QR"
                setting.updated_by = user().id
                db.session.add(setting)
            elif action in ("confirm_booking", "reject_booking"):
                booking = db.session.scalar(
                    select(M["reservations"])
                    .where(M["reservations"].id == record_id)
                    .with_for_update()
                )
                if not booking or booking.status != "Pending":
                    raise ValueError("Only pending bookings can be reviewed.")
                room = db.session.scalar(
                    select(M["rooms"])
                    .where(M["rooms"].id == booking.room_id)
                    .with_for_update()
                )
                if action == "confirm_booking":
                    conflict = db.session.scalar(
                        select(M["reservations"].id).where(
                            M["reservations"].room_id == room.id,
                            M["reservations"].status == "Confirmed",
                        )
                    )
                    if room.status != "Vacant" or conflict:
                        raise ValueError("Room is no longer available.")
                    booking.status = "Confirmed"
                    room.status = "Reserved"
                else:
                    booking.status = "Cancelled"
                audit(user().id, "reservations", booking, "Review")
            elif action == "approve_reading":
                reading = db.session.scalar(
                    select(M["meter_readings"]).where(
                        M["meter_readings"].id == record_id
                    )
                )
                if (
                    not reading
                    or reading.status != "Draft"
                    or reading.reading_type != "Customer"
                ):
                    raise ValueError("This reading has already been reviewed.")
                approve_reading(reading, user().id)
            elif action in ("confirm_payment", "reject_payment"):
                proof = db.session.scalar(
                    select(PaymentProof)
                    .where(PaymentProof.id == record_id)
                    .with_for_update()
                )
                if not proof or proof.status != "Pending":
                    raise ValueError("This payment has already been reviewed.")
                if action == "confirm_payment":
                    payment = pay_invoice(
                        proof.invoice_id,
                        proof.amount,
                        "KHQR",
                        proof.reference,
                        user().id,
                    )
                    proof.payment_id = payment.id
                    proof.status = "Confirmed"
                else:
                    proof.status = "Rejected"
                proof.reviewed_by = user().id
            elif action == "link_tenant":
                # Staff verify identity before linking an existing resident's history.
                a = db.get_or_404(CustomerAccount, record_id)
                tenant_id = int(request.form.get("tenant_id", "0"))
                db.get_or_404(M["tenants"], tenant_id)
                if leases_for(a) or db.session.scalar(
                    select(M["reservations"].id).where(
                        M["reservations"].tenant_id == a.tenant_id
                    )
                ):
                    raise ValueError(
                        "This account already has rental or booking history and cannot be relinked."
                    )
                a.tenant_id = tenant_id
                audit(user().id, "customer_accounts", a, "Link tenant")
            else:
                abort(400)
            db.session.commit()
            flash("Saved. Customer and management records are up to date.", "success")
        except (ValueError, IntegrityError, OSError) as exc:
            db.session.rollback()
            remove_image(filename)
            flash(
                str(exc)
                if isinstance(exc, ValueError)
                else "Could not save. Check for conflicting records.",
                "error",
            )
        return redirect(url_for("customer.management"))
    bookings = db.session.scalars(
        select(M["reservations"])
        .where(M["reservations"].status == "Pending")
        .order_by(M["reservations"].id.desc())
    ).all()
    confirmed_bookings = db.session.scalars(
        select(M["reservations"])
        .where(M["reservations"].status == "Confirmed")
        .order_by(M["reservations"].expected_move_in)
    ).all()
    booking_leases = {
        lease.reservation_id: lease
        for lease in db.session.scalars(
            select(M["rental_contracts"]).where(
                M["rental_contracts"].reservation_id.in_(
                    [b.id for b in confirmed_bookings]
                ),
                M["rental_contracts"].status.in_(["Draft", "Active"]),
            )
        )
    }
    readings = db.session.scalars(
        select(M["meter_readings"]).where(
            M["meter_readings"].reading_type == "Customer",
            M["meter_readings"].status == "Draft",
        )
    ).all()
    proofs = db.session.scalars(
        select(PaymentProof)
        .order_by(PaymentProof.status != "Pending", PaymentProof.id.desc())
        .limit(100)
    ).all()
    accounts = db.session.scalars(
        select(CustomerAccount).order_by(CustomerAccount.id.desc())
    ).all()
    return render_template(
        "customer/management.html",
        bookings=bookings,
        confirmed_bookings=confirmed_bookings,
        booking_leases=booking_leases,
        readings=readings,
        proofs=proofs,
        accounts=accounts,
        account_lookup={a.id: a for a in accounts},
        invoice_lookup={
            i.id: i
            for i in db.session.scalars(
                select(M["invoices"]).where(
                    M["invoices"].id.in_([p.invoice_id for p in proofs])
                )
            ).all()
        },
        tenants={t.id: t for t in db.session.scalars(select(M["tenants"])).all()},
        meters={m.id: m for m in db.session.scalars(select(M["utility_meters"])).all()},
        qr=qr_setting(),
    )

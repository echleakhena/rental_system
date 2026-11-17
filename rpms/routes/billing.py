"""Billing page routes."""

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import MODELS, db
from ..services import (
    activate,
    audit,
    invoice_reading_details,
    issue_invoice,
    money,
    pay_invoice,
)
from ..web import bp, can_manage, login_required, user


@bp.post("/contracts/<int:record_id>/<action>")
@login_required
def contract_action(record_id, action):
    if not can_manage("invoices" if action == "invoice" else "rental_contracts"):
        abort(403)
    contract = db.get_or_404(MODELS["rental_contracts"], record_id)
    try:
        if action == "activate":
            activate(contract, user().id)
        elif action == "invoice":
            issue_invoice(contract, request.form.get("period", ""), user().id)
        else:
            abort(404)
        db.session.commit()
        flash(
            "Lease activated." if action == "activate" else "Invoice generated.",
            "success",
        )
    except (ValueError, IntegrityError) as e:
        db.session.rollback()
        flash(
            str(e)
            if isinstance(e, ValueError)
            else "This operation conflicts with an existing record.",
            "error",
        )
    if request.form.get("return_detail") == "yes":
        return redirect(
            url_for("main.record_detail", table="rental_contracts", record_id=record_id)
        )
    return redirect(url_for("main.records", table="rental_contracts"))


@bp.route("/invoices/<int:record_id>", methods=["GET", "POST"])
@login_required
def invoice(record_id):
    inv = db.get_or_404(MODELS["invoices"], record_id)
    if request.method == "POST":
        if not can_manage("invoices"):
            abort(403)
        try:
            pay_invoice(
                inv.id,
                request.form.get("amount", ""),
                request.form.get("method"),
                request.form.get("reference", ""),
                user().id,
            )
            db.session.commit()
            flash("Payment recorded and allocated.", "success")
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(
                str(e)
                if isinstance(e, ValueError)
                else "Payment conflict. Please refresh.",
                "error",
            )
        return redirect(url_for("main.invoice", record_id=inv.id))
    items = db.session.scalars(
        select(MODELS["invoice_items"]).where(
            MODELS["invoice_items"].invoice_id == inv.id
        )
    ).all()
    rental = (
        db.session.get(MODELS["rental_contracts"], inv.contract_id)
        if inv.contract_id
        else None
    )
    invoice_room = (
        db.session.get(MODELS["rooms"], rental.room_id)
        if rental and rental.room_id
        else None
    )
    bill_to = (
        db.session.scalar(
            select(MODELS["tenants"])
            .join(
                MODELS["contract_tenants"],
                MODELS["contract_tenants"].tenant_id == MODELS["tenants"].id,
            )
            .where(
                MODELS["contract_tenants"].contract_id == inv.contract_id,
                MODELS["contract_tenants"].is_primary == True,
            )
        )
        if rental
        else None
    )
    return render_template(
        "invoice.html",
        invoice=inv,
        rental=rental,
        invoice_room=invoice_room,
        bill_to=bill_to,
        items=items,
        reading_details=invoice_reading_details(items),
    )


def billing_return(lease_id, period):
    from ..billing_workspace import period_bounds

    try:
        period_bounds(period)
    except ValueError:
        period = None
    return redirect(
        url_for("main.records", table="invoices", contract_id=lease_id, period=period)
        + "#prepare-invoice"
    )


@bp.post("/billing/readings")
@login_required
def billing_reading():
    if not can_manage("meter_readings"):
        abort(403)
    from ..billing_workspace import month_is_billed, period_bounds

    lease = db.get_or_404(
        MODELS["rental_contracts"], request.form.get("contract_id", type=int)
    )
    period = request.form.get("period", "")
    try:
        db.session.refresh(lease, with_for_update=True)
        month, following = period_bounds(period)
        if lease.status != "Active" or month_is_billed(lease.id, month):
            raise ValueError(
                "Use an active rental with a month that has not been invoiced."
            )
        meter = db.session.scalar(
            select(MODELS["utility_meters"])
            .where(
                MODELS["utility_meters"].id == request.form.get("meter_id", type=int)
            )
            .with_for_update()
        )
        if not meter or meter.room_id != lease.room_id or meter.status != "Active":
            raise ValueError("Choose an active meter for this rental room.")
        day = date.fromisoformat(request.form.get("reading_date", ""))
        if not month <= day < following or not lease.start_date <= day <= min(
            date.today(), lease.end_date
        ):
            raise ValueError(
                "The reading date must be in this billing month and within the rental, up to today."
            )
        last = db.session.scalar(
            select(MODELS["meter_readings"])
            .where(MODELS["meter_readings"].meter_id == meter.id)
            .order_by(
                MODELS["meter_readings"].reading_date.desc(),
                MODELS["meter_readings"].id.desc(),
            )
        )
        if last and (last.status == "Draft" or day <= last.reading_date):
            raise ValueError(
                "Review pending readings first and choose a date after the last reading."
            )
        previous = (
            last.current_reading
            if last
            else Decimal(request.form.get("previous_reading", ""))
        )
        current = Decimal(request.form.get("current_reading", ""))
        for value in (previous, current):
            if (
                not value.is_finite()
                or value < 0
                or value > Decimal("99999999999.999")
                or value.as_tuple().exponent < -3
            ):
                raise ValueError(
                    "Enter nonnegative meter values with up to 3 decimal places."
                )
        if current < previous:
            raise ValueError(
                "The current reading cannot be below the previous reading."
            )
        if meter.default_rate is None or meter.default_rate < 0:
            raise ValueError("Set a valid utility rate in Settings first.")
        charge = money((current - previous) * meter.default_rate)
        if charge > Decimal("9999999999.99"):
            raise ValueError(
                "This utility charge is too large. Check the meter values and rate."
            )
        reading = MODELS["meter_readings"](
            meter_id=meter.id,
            contract_id=lease.id,
            reading_date=day,
            previous_reading=previous,
            current_reading=current,
            usage_quantity=current - previous,
            unit_rate=meter.default_rate,
            total_amount=charge,
            reading_type="Regular",
            recorded_by=user().id,
            status="Approved",
        )
        db.session.add(reading)
        db.session.flush()
        audit(user().id, "meter_readings", reading, "Approve")
        db.session.commit()
        flash("Reading saved and approved. The invoice estimate is updated.", "success")
    except (ValueError, InvalidOperation, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc)
            if isinstance(exc, ValueError)
            else "Could not save this reading. Check the values and retry.",
            "error",
        )
    return billing_return(lease.id, period)


@bp.post("/billing/readings/<int:record_id>/approve")
@login_required
def billing_approve_reading(record_id):
    if not can_manage("meter_readings"):
        abort(403)
    from ..billing_workspace import month_is_billed

    reading = db.get_or_404(MODELS["meter_readings"], record_id)
    lease = db.get_or_404(MODELS["rental_contracts"], reading.contract_id)
    period = reading.reading_date.strftime("%Y-%m")
    try:
        db.session.refresh(lease, with_for_update=True)
        db.session.refresh(reading, with_for_update=True)
        if lease.status != "Active" or reading.status != "Draft":
            raise ValueError("Only draft readings for active rentals can be approved.")
        if month_is_billed(lease.id, reading.reading_date.replace(day=1)):
            raise ValueError("This month has already been invoiced.")
        meter = db.session.get(MODELS["utility_meters"], reading.meter_id)
        if (
            not meter
            or meter.room_id != lease.room_id
            or not lease.start_date
            <= reading.reading_date
            <= min(date.today(), lease.end_date)
        ):
            raise ValueError(
                "Check the meter, rental and reading date before approval."
            )
        if (
            reading.current_reading < reading.previous_reading
            or reading.unit_rate is None
            or reading.unit_rate < 0
        ):
            raise ValueError("Correct the meter values or unit rate first.")
        reading.usage_quantity = reading.current_reading - reading.previous_reading
        reading.total_amount = money(reading.usage_quantity * reading.unit_rate)
        reading.status = "Approved"
        reading.recorded_by = user().id
        audit(user().id, "meter_readings", reading, "Approve")
        db.session.commit()
        flash("Reading approved and included in the invoice estimate.", "success")
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc)
            if isinstance(exc, ValueError)
            else "Approval conflicts with an existing record.",
            "error",
        )
    return billing_return(lease.id, period)


@bp.post("/billing/issue")
@login_required
def billing_issue():
    if not can_manage("invoices"):
        abort(403)
    lease = db.get_or_404(
        MODELS["rental_contracts"], request.form.get("contract_id", type=int)
    )
    period = request.form.get("period", "")
    try:
        from ..billing_workspace import month_is_billed, period_bounds

        db.session.refresh(lease, with_for_update=True)
        month, following = period_bounds(period)
        if lease.status != "Active":
            raise ValueError("Invoices require an active rental.")
        if not month_is_billed(lease.id, month):
            expected = set(
                db.session.scalars(
                    select(MODELS["utility_meters"].id).where(
                        MODELS["utility_meters"].room_id == lease.room_id,
                        MODELS["utility_meters"].status == "Active",
                    )
                ).all()
            )
            covered = set(
                db.session.scalars(
                    select(MODELS["meter_readings"].meter_id).where(
                        MODELS["meter_readings"].contract_id == lease.id,
                        MODELS["meter_readings"].reading_date >= month,
                        MODELS["meter_readings"].reading_date < following,
                        MODELS["meter_readings"].status.in_(["Draft", "Approved"]),
                    )
                ).all()
            )
            if expected - covered:
                raise ValueError(
                    "Enter this month's readings for every active room meter before issuing the invoice."
                )
        inv = issue_invoice(lease, period, user().id)
        db.session.commit()
        flash(
            "Invoice issued. The customer can now view rent, utilities and the total in My Rental.",
            "success",
        )
        return redirect(url_for("main.invoice", record_id=inv.id))
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc)
            if isinstance(exc, ValueError)
            else "This month has already been invoiced.",
            "error",
        )
        return billing_return(lease.id, period)

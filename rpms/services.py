"""Lease, billing and payment rules. Routes commit or roll back these changes."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select

from .models import MODELS, db


def number(prefix):
    return prefix + "-" + uuid4().hex[:12].upper()


def money(value):
    """Validate a nonnegative finite amount and round to cents."""
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Enter a valid amount.")
    if not result.is_finite() or result < 0:
        raise ValueError("Amounts must be finite and nonnegative.")
    return result.quantize(Decimal(".01"))


def audit(user, table, record, action="Create"):
    """Add an audit entry in the same transaction as the record change."""
    db.session.add(
        MODELS["audit_logs"](
            user_id=user,
            table_name=table,
            record_id=record.id,
            action=action,
            new_values={"id": record.id},
        )
    )


def activate(contract, user):
    """Validate a draft lease, occupy the room and create its deposit record."""
    db.session.refresh(contract, with_for_update=True)
    room = db.session.execute(
        select(MODELS["rooms"])
        .where(MODELS["rooms"].id == contract.room_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if contract.status != "Draft":
        raise ValueError("Only draft leases can be activated.")
    if (
        not contract.start_date
        or not contract.end_date
        or contract.end_date <= contract.start_date
    ):
        raise ValueError("Lease end must follow its start.")
    if not db.session.scalar(
        select(MODELS["contract_tenants"].id).where(
            MODELS["contract_tenants"].contract_id == contract.id
        )
    ):
        raise ValueError("Link a tenant to this lease first.")
    clash = db.session.scalar(
        select(MODELS["rental_contracts"].id).where(
            MODELS["rental_contracts"].room_id == room.id,
            MODELS["rental_contracts"].id != contract.id,
            MODELS["rental_contracts"].status == "Active",
            MODELS["rental_contracts"].start_date <= contract.end_date,
            MODELS["rental_contracts"].end_date >= contract.start_date,
        )
    )
    if clash or room.status == "Maintenance":
        raise ValueError("Room is unavailable for this lease period.")
    reservation = db.session.scalar(
        select(MODELS["reservations"]).where(
            MODELS["reservations"].room_id == room.id,
            MODELS["reservations"].status == "Confirmed",
            MODELS["reservations"].id != (contract.reservation_id or 0),
        )
    )
    if reservation:
        raise ValueError("Room has another confirmed reservation.")
    if contract.reservation_id:
        booking = db.session.get(MODELS["reservations"], contract.reservation_id)
        if booking.room_id != room.id or booking.status != "Confirmed":
            raise ValueError(
                "Reservation does not match this room or is not confirmed."
            )
        booking.status = "Converted"
    contract.status = "Active"
    contract.signed_at = datetime.now(timezone.utc)
    room.status = "Occupied"
    db.session.add(
        MODELS["deposits"](
            contract_id=contract.id,
            required_amount=contract.deposit_required or 0,
            held_amount=0,
            status="Pending",
        )
    )
    audit(user, "rental_contracts", contract, "Approve")


def issue_invoice(contract, period, user):
    """Create one rent and utility invoice for the requested lease month."""
    db.session.refresh(contract, with_for_update=True)
    if contract.status != "Active":
        raise ValueError("Invoices require an active lease.")
    month = date.fromisoformat(period + "-01")
    if month < contract.start_date.replace(day=1) or month > contract.end_date.replace(
        day=1
    ):
        raise ValueError("Billing month is outside the lease.")
    if db.session.scalar(
        select(MODELS["invoices"].id).where(
            MODELS["invoices"].contract_id == contract.id,
            MODELS["invoices"].billing_period == month,
            MODELS["invoices"].status != "Voided",
        )
    ):
        raise ValueError("This month has already been invoiced.")
    next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    if db.session.scalar(
        select(MODELS["meter_readings"].id).where(
            MODELS["meter_readings"].contract_id == contract.id,
            MODELS["meter_readings"].reading_date >= month,
            MODELS["meter_readings"].reading_date < next_month,
            MODELS["meter_readings"].status == "Draft",
        )
    ):
        raise ValueError(
            "Review draft utility readings for this month before issuing the invoice."
        )
    rent = money(contract.monthly_rent)
    inv = MODELS["invoices"](
        invoice_no=number("INV"),
        contract_id=contract.id,
        billing_period=month,
        invoice_date=date.today(),
        due_date=month.replace(day=contract.payment_due_day or 5),
        subtotal=rent,
        total_amount=rent,
        adjustment_amount=0,
        paid_amount=0,
        balance_due=rent,
        status="Issued",
        created_by=user,
    )
    db.session.add(inv)
    db.session.flush()
    db.session.add(
        MODELS["invoice_items"](
            invoice_id=inv.id,
            item_type="Rent",
            description="Monthly rent",
            quantity=1,
            unit_price=rent,
            amount=rent,
        )
    )
    readings = db.session.scalars(
        select(MODELS["meter_readings"]).where(
            MODELS["meter_readings"].contract_id == contract.id,
            MODELS["meter_readings"].status == "Approved",
            MODELS["meter_readings"].reading_date >= month,
            MODELS["meter_readings"].reading_date
            < date(month.year + month.month // 12, month.month % 12 + 1, 1),
        )
    ).all()
    for r in readings:
        meter = db.session.get(MODELS["utility_meters"], r.meter_id)
        db.session.add(
            MODELS["invoice_items"](
                invoice_id=inv.id,
                meter_reading_id=r.id,
                item_type=meter.meter_type,
                description=meter.meter_type,
                quantity=r.usage_quantity,
                unit_price=r.unit_rate,
                amount=r.total_amount,
            )
        )
        inv.total_amount += r.total_amount
        r.status = "Billed"
    inv.subtotal = inv.total_amount
    inv.balance_due = inv.total_amount
    audit(user, "invoices", inv)
    return inv


def pay_invoice(invoice_id, amount, method, reference, user):
    """Record a confirmed payment and allocate it without overpaying the invoice."""
    inv = db.session.execute(
        select(MODELS["invoices"])
        .where(MODELS["invoices"].id == invoice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    amount = money(amount)
    if method not in ("Cash", "Transfer", "KHQR"):
        raise ValueError("Choose a valid payment method.")
    if inv.status in ("Voided", "Draft") or amount <= 0 or amount > inv.balance_due:
        raise ValueError(
            "Payment must be positive and cannot exceed the invoice balance."
        )
    tenant = db.session.scalar(
        select(MODELS["contract_tenants"]).where(
            MODELS["contract_tenants"].contract_id == inv.contract_id,
            MODELS["contract_tenants"].is_primary == True,
        )
    )
    if not tenant:
        raise ValueError("Lease requires a primary tenant.")
    payment = MODELS["payments"](
        payment_no=number("PAY"),
        tenant_id=tenant.tenant_id,
        payment_date=datetime.now(timezone.utc),
        amount=amount,
        payment_method=method,
        reference_no=reference,
        status="Confirmed",
        received_by=user,
    )
    db.session.add(payment)
    db.session.flush()
    db.session.add(
        MODELS["payment_allocations"](
            payment_id=payment.id,
            invoice_id=inv.id,
            allocated_amount=amount,
            allocated_at=datetime.now(timezone.utc),
        )
    )
    inv.paid_amount += amount
    inv.balance_due -= amount
    inv.status = "Paid" if inv.balance_due == 0 else "Partial"
    audit(user, "payments", payment)
    return payment


def invoice_reading_details(items):
    """Return the saved meter evidence behind invoice items for either portal."""
    ids = [item.meter_reading_id for item in items if item.meter_reading_id]
    readings = db.session.scalars(
        select(MODELS["meter_readings"]).where(MODELS["meter_readings"].id.in_(ids))
    ).all()
    meters = {
        m.id: m
        for m in db.session.scalars(
            select(MODELS["utility_meters"]).where(
                MODELS["utility_meters"].id.in_([r.meter_id for r in readings])
            )
        ).all()
    }
    return {
        r.id: dict(
            previous=r.previous_reading,
            current=r.current_reading,
            date=r.reading_date,
            unit=meters[r.meter_id].unit or "units"
            if r.meter_id in meters
            else "units",
        )
        for r in readings
    }

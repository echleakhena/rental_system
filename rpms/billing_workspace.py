"""One monthly workspace for meter review, invoice calculation and issue."""

from datetime import date, timedelta
from decimal import Decimal

from flask import abort, render_template, request
from sqlalchemy import func, or_, select

from .models import MODELS as M
from .models import db


def period_bounds(raw):
    try:
        month = date.fromisoformat(raw + "-01")
        following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Choose a valid billing month.")
    return month, following


def month_is_billed(lease_id, month):
    return db.session.scalar(
        select(M["invoices"]).where(
            M["invoices"].contract_id == lease_id,
            M["invoices"].billing_period == month,
            M["invoices"].status != "Voided",
        )
    )


def is_utility_item(item):
    return bool(item.meter_reading_id) or (item.item_type or "").casefold() in {
        "water",
        "electric",
        "electricity",
        "utility",
        "utilities",
    }


def invoice_workspace():
    leases = db.session.scalars(
        select(M["rental_contracts"]).order_by(M["rental_contracts"].id.desc())
    ).all()
    rooms = {r.id: r for r in db.session.scalars(select(M["rooms"])).all()}
    people = {t.id: t for t in db.session.scalars(select(M["tenants"])).all()}
    primary = {
        contract.contract_id: people.get(contract.tenant_id)
        for contract in db.session.scalars(
            select(M["contract_tenants"]).where(
                M["contract_tenants"].is_primary == True
            )
        ).all()
    }
    lease_id = request.args.get("contract_id", type=int)
    lease = (
        next((contract for contract in leases if contract.id == lease_id), None)
        if lease_id
        else next(
            (contract for contract in leases if contract.status == "Active"),
            leases[0] if leases else None,
        )
    )
    if lease_id and not lease:
        abort(404)
    raw = request.args.get("period")
    month = date.today().replace(day=1)
    if not raw and lease and lease.start_date and lease.end_date:
        month = max(
            lease.start_date.replace(day=1), min(month, lease.end_date.replace(day=1))
        )
    try:
        month, following = period_bounds(raw or month.strftime("%Y-%m"))
    except ValueError as exc:
        abort(400, str(exc))
    readings = (
        db.session.scalars(
            select(M["meter_readings"])
            .where(
                M["meter_readings"].contract_id == lease.id,
                M["meter_readings"].reading_date >= month,
                M["meter_readings"].reading_date < following,
            )
            .order_by(M["meter_readings"].meter_id, M["meter_readings"].reading_date)
        ).all()
        if lease
        else []
    )
    meters = (
        db.session.scalars(
            select(M["utility_meters"])
            .where(
                M["utility_meters"].room_id == lease.room_id,
                M["utility_meters"].status == "Active",
            )
            .order_by(M["utility_meters"].meter_type)
        ).all()
        if lease
        else []
    )
    meter_ids = {m.id for m in meters} | {r.meter_id for r in readings}
    meter_lookup = {
        m.id: m
        for m in db.session.scalars(
            select(M["utility_meters"]).where(M["utility_meters"].id.in_(meter_ids))
        )
    }
    # Rank in the database so this stays one query as the meter count grows.
    ranked = (
        select(
            M["meter_readings"].id,
            func.row_number()
            .over(
                partition_by=M["meter_readings"].meter_id,
                order_by=(
                    M["meter_readings"].reading_date.desc(),
                    M["meter_readings"].id.desc(),
                ),
            )
            .label("position"),
        )
        .where(M["meter_readings"].meter_id.in_([m.id for m in meters]))
        .subquery()
    )
    latest = {
        reading.meter_id: reading
        for reading in db.session.scalars(
            select(M["meter_readings"])
            .join(ranked, ranked.c.id == M["meter_readings"].id)
            .where(ranked.c.position == 1)
        )
    }
    latest = {m.id: latest.get(m.id) for m in meters}
    existing = month_is_billed(lease.id, month) if lease else None
    rent = lease.monthly_rent or Decimal(0) if lease else Decimal(0)
    utility_total = sum(
        (r.total_amount or 0 for r in readings if r.status in ("Draft", "Approved")),
        Decimal(0),
    )
    total = rent + utility_total
    if existing:
        items = db.session.scalars(
            select(M["invoice_items"]).where(
                M["invoice_items"].invoice_id == existing.id
            )
        ).all()
        rent = sum((i.amount or 0 for i in items if i.item_type == "Rent"), Decimal(0))
        utility_total = sum(
            (i.amount or 0 for i in items if is_utility_item(i)), Decimal(0)
        )
        total = existing.total_amount
    draft_count = sum(r.status == "Draft" for r in readings)
    covered_meters = {r.meter_id for r in readings if r.status in ("Draft", "Approved")}
    missing_meters = [m for m in meters if m.id not in covered_meters]
    in_period = bool(
        lease
        and lease.start_date
        and lease.end_date
        and lease.start_date.replace(day=1) <= month <= lease.end_date.replace(day=1)
    )
    reading_date = min(date.today(), following - timedelta(days=1))
    q = request.args.get("q", "").strip()
    query = select(M["invoices"])
    if q:
        matching = [
            contract.id
            for contract in leases
            if q.casefold()
            in (
                (
                    rooms[contract.room_id].room_number
                    if contract.room_id in rooms
                    else ""
                )
                + " "
                + (primary[contract.id].full_name if primary.get(contract.id) else "")
            ).casefold()
        ]
        query = query.where(
            or_(
                M["invoices"].invoice_no.ilike("%" + q + "%"),
                M["invoices"].contract_id.in_(matching),
            )
        )
    page = max(1, request.args.get("page", 1, type=int))
    invoices = db.session.scalars(
        query.order_by(M["invoices"].id.desc()).offset((page - 1) * 50).limit(51)
    ).all()
    breakdowns = {
        i.id: {
            "rent": Decimal(0),
            "utilities": Decimal(0),
            "known": False,
            "other": Decimal(0),
        }
        for i in invoices[:50]
    }
    saved_items = db.session.scalars(
        select(M["invoice_items"]).where(
            M["invoice_items"].invoice_id.in_(list(breakdowns))
        )
    ).all()
    for item in saved_items:
        parts = breakdowns[item.invoice_id]
        parts["known"] = True
        if item.item_type == "Rent":
            parts["rent"] += item.amount or 0
        elif is_utility_item(item):
            parts["utilities"] += item.amount or 0
    for bill in invoices[:50]:
        parts = breakdowns[bill.id]
        parts["other"] = (bill.total_amount or 0) - parts["rent"] - parts["utilities"]
    return render_template(
        "billing_workspace.html",
        table="invoices",
        page_title="Invoices & utilities",
        leases=leases,
        lease=lease,
        rooms=rooms,
        primary=primary,
        period=month.strftime("%Y-%m"),
        month=month,
        readings=readings,
        meters=meters,
        meter_lookup=meter_lookup,
        latest=latest,
        existing=existing,
        rent=rent,
        utility_total=utility_total,
        total=total,
        draft_count=draft_count,
        missing_meters=missing_meters,
        in_period=in_period,
        reading_date=reading_date,
        invoice_rows=invoices[:50],
        breakdowns=breakdowns,
        show_other_charges=any(p["known"] and p["other"] for p in breakdowns.values()),
        more=len(invoices) > 50,
        page=page,
        q=q,
    )

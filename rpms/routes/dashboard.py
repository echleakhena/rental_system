"""Dashboard page routes."""

from decimal import Decimal

from flask import render_template, request
from sqlalchemy import func, select

from ..customer_models import PaymentProof
from ..models import MODELS, db
from ..reporting import dashboard_reports
from ..web import bp, login_required


@bp.get("/")
@login_required
def dashboard():
    rooms = db.session.scalars(select(MODELS["rooms"])).all()
    invoices = db.session.scalars(
        select(MODELS["invoices"])
        .where(MODELS["invoices"].status.notin_(["Draft", "Voided"]))
        .order_by(MODELS["invoices"].id)
    ).all()
    revenue = db.session.scalar(
        select(func.coalesce(func.sum(MODELS["payments"].amount), 0)).where(
            MODELS["payments"].status == "Confirmed"
        )
    )
    outstanding = sum((i.balance_due or 0 for i in invoices), Decimal(0))
    occupied = sum(r.status == "Occupied" for r in rooms)

    reports = dashboard_reports(rooms, invoices)
    floors = {f.id: f for f in db.session.scalars(select(MODELS["floors"])).all()}
    properties = {
        p.id: p for p in db.session.scalars(select(MODELS["properties"])).all()
    }
    status = request.args.get("room_status", "All")
    if status not in ("All", "Vacant", "Occupied", "Reserved", "Maintenance"):
        status = "All"
    property_id = request.args.get("property", type=int)
    search = request.args.get("room_search", "").strip()
    board = []
    for room in sorted(
        rooms,
        key=lambda r: (
            (floors[r.floor_id].property_id if r.floor_id in floors else 0),
            r.floor_id or 0,
            r.room_number or "",
        ),
    ):
        floor = floors.get(room.floor_id)
        prop = properties.get(floor.property_id) if floor else None
        if status != "All" and room.status != status:
            continue
        if property_id and (not prop or prop.id != property_id):
            continue
        if search and search.casefold() not in (room.room_number or "").casefold():
            continue
        board.append(dict(room=room, floor=floor, property=prop))
    vacant = sum(r.status == "Vacant" for r in rooms)
    pending_bookings = db.session.scalar(
        select(func.count())
        .select_from(MODELS["reservations"])
        .where(MODELS["reservations"].status == "Pending")
    )
    pending_readings = db.session.scalar(
        select(func.count())
        .select_from(MODELS["meter_readings"])
        .where(
            MODELS["meter_readings"].status == "Draft",
            MODELS["meter_readings"].reading_type == "Customer",
        )
    )
    preparing_rentals = db.session.scalar(
        select(func.count())
        .select_from(MODELS["reservations"])
        .where(MODELS["reservations"].status == "Confirmed")
    )
    pending_payments = db.session.scalar(
        select(func.count())
        .select_from(PaymentProof)
        .where(PaymentProof.status == "Pending")
    )
    status_counts = {
        s: sum(r.status == s for r in rooms)
        for s in ["Vacant", "Occupied", "Reserved", "Maintenance"]
    }
    return render_template(
        "dashboard.html",
        pending_bookings=pending_bookings,
        preparing_rentals=preparing_rentals,
        pending_readings=pending_readings,
        pending_payments=pending_payments,
        status_counts=status_counts,
        board=board,
        properties=properties,
        room_status=status,
        property_id=property_id,
        room_search=search,
        vacant=vacant,
        reports=reports,
        rooms=rooms,
        occupied=occupied,
        revenue=revenue,
        outstanding=outstanding,
        invoices=invoices[-6:][::-1],
    )

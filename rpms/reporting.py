"""Dashboard summaries. Customers are primary tenants of billed contracts."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from .models import MODELS, db

COLORS = ["#287867", "#e9b75c", "#d97c68", "#91a6b8", "#9a8abb"]


def chart(title, subtitle, values, currency=False):
    total = sum((Decimal(str(value)) for _, value in values), Decimal(0))
    segments = []
    stops = []
    position = Decimal(0)
    for index, (label, value) in enumerate(values):
        value = Decimal(str(value))
        percentage = value / total * 100 if total else Decimal(0)
        color = COLORS[index % len(COLORS)]
        end = position + percentage
        stops.append(f"{color} {position:.6f}% {end:.6f}%")
        segments.append(
            dict(label=label, value=value, color=color, percentage=percentage)
        )
        position = end
    return dict(
        title=title,
        subtitle=subtitle,
        segments=segments,
        total=total,
        currency=currency,
        gradient="conic-gradient(" + ", ".join(stops) + ")" if total else "#e8ede9",
    )


def dashboard_reports(rooms, invoices):
    issued = [i for i in invoices if i.status not in ("Draft", "Voided")]
    room_counts = {
        status: 0 for status in ["Occupied", "Vacant", "Reserved", "Maintenance"]
    }
    for room in rooms:
        status = room.status or "Unknown"
        room_counts[status] = room_counts.get(status, 0) + 1
    # Each invoice belongs to one primary customer; co-tenants are not double-counted.
    primary = {
        link.contract_id: link.tenant_id
        for link in db.session.scalars(
            select(MODELS["contract_tenants"]).where(
                MODELS["contract_tenants"].is_primary == True
            )
        ).all()
    }
    totals = {}
    unlinked = 0
    for invoice in issued:
        tenant_id = primary.get(invoice.contract_id)
        if tenant_id is None:
            unlinked += 1
            continue
        bucket = totals.setdefault(tenant_id, {"paid": Decimal(0), "due": Decimal(0)})
        bucket["paid"] += invoice.paid_amount or 0
        bucket["due"] += invoice.balance_due or 0
    counts = {"Paid": 0, "Partially paid": 0, "Unpaid": 0}
    customers = []
    tenants = (
        {
            t.id: t
            for t in db.session.scalars(
                select(MODELS["tenants"]).where(MODELS["tenants"].id.in_(list(totals)))
            ).all()
        }
        if totals
        else {}
    )
    for tenant_id, amounts in totals.items():
        status = (
            "Paid"
            if amounts["due"] == 0
            else "Partially paid"
            if amounts["paid"] > 0
            else "Unpaid"
        )
        counts[status] += 1
        customers.append(
            dict(name=tenants[tenant_id].full_name, status=status, **amounts)
        )
    customers.sort(key=lambda c: c["due"], reverse=True)
    paid = sum((i.paid_amount or 0 for i in issued), Decimal(0))
    due = sum((i.balance_due or 0 for i in issued), Decimal(0))
    charts = [
        chart("Room status", "All rooms in your portfolio", list(room_counts.items())),
        chart(
            "Revenue collection",
            "All-time issued invoices · USD",
            [("Collected", paid), ("Outstanding", due)],
            True,
        ),
        chart(
            "Customer payments",
            "Primary customers with issued invoices",
            list(counts.items()),
        ),
    ]
    return dict(charts=charts, customers=customers, unlinked=unlinked)


def management_actions(rooms, invoices, maintenance, contracts, today):
    """Explainable operational suggestions; never change records automatically."""

    actions = []

    def add(priority, title, evidence, next_step, table):
        actions.append(
            dict(
                priority=priority,
                title=title,
                evidence=evidence,
                next_step=next_step,
                table=table,
            )
        )

    overdue = [
        i
        for i in invoices
        if i.status not in ("Draft", "Voided")
        and (i.balance_due or 0) > 0
        and i.due_date
        and i.due_date < today
    ]
    if overdue:
        due = sum((i.balance_due for i in overdue), Decimal(0))
        add(
            "High",
            "Follow up overdue invoices",
            f"{len(overdue)} overdue invoice(s) · ${due:,.2f} outstanding.",
            "Contact the billed customers, confirm payment plans, and record received payments.",
            "invoices",
        )
    open_requests = [
        m for m in maintenance if m.status not in ("Completed", "Cancelled")
    ]
    urgent = [m for m in open_requests if m.priority in ("High", "Urgent")]
    if open_requests:
        add(
            "High" if urgent else "Normal",
            "Review maintenance queue",
            f"{len(open_requests)} open request(s), including {len(urgent)} high or urgent.",
            "Assign unresolved requests and prioritize urgent issues before returning rooms to service.",
            "maintenance_requests",
        )
    ending = [
        c
        for c in contracts
        if c.status == "Active"
        and c.end_date
        and today <= c.end_date <= today + timedelta(days=30)
    ]
    if ending:
        add(
            "Normal",
            "Plan upcoming lease endings",
            f"{len(ending)} active lease(s) end within 30 days.",
            "Discuss renewal intentions with tenants and plan inspections or new listings.",
            "rental_contracts",
        )
    expired = [
        c
        for c in contracts
        if c.status == "Active" and c.end_date and c.end_date < today
    ]
    if expired:
        add(
            "High",
            "Review past-end-date leases",
            f"{len(expired)} lease(s) remain Active after their end date.",
            "Verify renewal or move-out status and reconcile the lease records.",
            "rental_contracts",
        )
    vacant = [r for r in rooms if r.status == "Vacant"]
    opportunity = sum((r.monthly_rent or 0 for r in vacant), Decimal(0))
    if vacant:
        add(
            "Normal",
            "Market vacant rooms",
            f"{len(vacant)} vacant room(s) · ${opportunity:,.2f}/month at listed rents.",
            "Check room readiness and promote available units; compare rent scenarios before changing prices.",
            "rooms",
        )
    unpaid = [
        i
        for i in invoices
        if i.status not in ("Draft", "Voided")
        and (i.balance_due or 0) > 0
        and (not i.due_date or i.due_date >= today)
    ]
    if unpaid:
        add(
            "Normal",
            "Monitor upcoming collections",
            f"{len(unpaid)} unpaid or partially paid invoice(s) are not yet overdue or have no due date.",
            "Check due dates and prepare payment reminders.",
            "invoices",
        )
    if not rooms:
        add(
            "Setup",
            "Set up your portfolio",
            "No rooms have been added.",
            "Add properties, floors and rooms to begin tracking occupancy.",
            "properties",
        )
    if not invoices:
        add(
            "Setup",
            "Build your billing picture",
            "No issued invoices are available.",
            "Review active leases and generate the appropriate monthly invoices.",
            "rental_contracts",
        )
    if not actions:
        add(
            "Routine",
            "Continue routine monitoring",
            "No issues were flagged by the current checks.",
            "Review new bookings, invoice balances and maintenance requests regularly.",
            "rooms",
        )
    actions.sort(
        key=lambda a: {"High": 0, "Normal": 1, "Setup": 2, "Routine": 3}[a["priority"]]
    )
    return dict(
        actions=actions,
        vacancy_opportunity=opportunity,
        overdue_count=len(overdue),
        as_of=today,
    )


def record_summary(table):
    """Unfiltered counts for portfolio and leasing record lists."""

    configurations = {
        "rooms": (
            "Total rooms",
            "portfolio",
            [
                ("Vacant", "Available", "room"),
                ("Occupied", "Occupied", "people"),
                ("Reserved", "Reserved", "calendar"),
                ("Maintenance", "Maintenance", "operations"),
            ],
        ),
        "tenants": (
            "Total tenants",
            "people",
            [("Active", "Active", "people"), ("Inactive", "Inactive", "people")],
        ),
        "reservations": (
            "Total reservations",
            "calendar",
            [
                (s, s, "calendar")
                for s in ["Pending", "Confirmed", "Cancelled", "Converted", "Expired"]
            ],
        ),
        "rental_contracts": (
            "Total leases",
            "leasing",
            [(s, s, "leasing") for s in ["Draft", "Active", "Expired", "Terminated"]],
        ),
    }

    def card(label, count, icon, status):
        return dict(label=label, count=count, icon=icon, status=status)

    if table == "contract_tenants":
        model = MODELS[table]
        total = db.session.scalar(select(func.count(model.id)))
        primary = db.session.scalar(
            select(func.count(model.id)).where(model.is_primary == True)
        )
        co = db.session.scalar(
            select(func.count(model.id)).where(model.is_primary == False)
        )
        cards = [
            card("Total tenant links", total, "people", "total"),
            card("Primary tenants", primary, "role", "active"),
            card("Co-tenant links", co, "people", "reserved"),
            card(
                "Unique tenants",
                db.session.scalar(select(func.count(func.distinct(model.tenant_id)))),
                "people",
                "total",
            ),
            card(
                "Linked leases",
                db.session.scalar(select(func.count(func.distinct(model.contract_id)))),
                "leasing",
                "total",
            ),
        ]
        if total > primary + co:
            cards.append(
                card("Unspecified role", total - primary - co, "people", "other")
            )
        return cards
    if table not in configurations:
        return None
    title, icon, statuses = configurations[table]
    model = MODELS[table]
    counts = dict(
        db.session.execute(
            select(model.status, func.count(model.id)).group_by(model.status)
        ).all()
    )
    cards = [card(title, sum(counts.values()), icon, "total")]
    for status, label, icon in statuses:
        cards.append(card(label, counts.get(status, 0), icon, status.lower()))
    other = sum(
        value for key, value in counts.items() if key not in {s[0] for s in statuses}
    )
    if other:
        cards.append(card("Other / unspecified", other, "items", "other"))
    return cards

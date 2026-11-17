"""Customer invoice breakdowns and confirmed, invoice-scoped payment history."""

from decimal import Decimal

from sqlalchemy import select

from .models import MODELS as M
from .models import db


def invoice_breakdowns(invoices):
    parts = {
        invoice.id: dict(
            rent=Decimal(0), water=Decimal(0), electricity=Decimal(0), other=Decimal(0)
        )
        for invoice in invoices
    }
    items = db.session.scalars(
        select(M["invoice_items"]).where(M["invoice_items"].invoice_id.in_(parts))
    )
    for item in items:
        kind = (item.item_type or "").casefold()
        key = (
            "rent"
            if kind == "rent"
            else "water"
            if kind == "water"
            else "electricity"
            if kind in {"electric", "electricity"}
            else "other"
        )
        parts[item.invoice_id][key] += item.amount or Decimal(0)
    for invoice in invoices:
        # Preserve adjustments and legacy bills whose item breakdown is incomplete.
        parts[invoice.id]["other"] += (invoice.total_amount or 0) - sum(
            parts[invoice.id].values(), Decimal(0)
        )
    return parts


def payment_history(invoice_ids):
    """Use allocated amounts so a payment split across bills is never duplicated."""
    return db.session.execute(
        select(M["payment_allocations"], M["payments"], M["invoices"])
        .join(M["payments"], M["payment_allocations"].payment_id == M["payments"].id)
        .join(M["invoices"], M["payment_allocations"].invoice_id == M["invoices"].id)
        .where(M["invoices"].id.in_(invoice_ids), M["payments"].status == "Confirmed")
        .order_by(M["payments"].payment_date.desc(), M["payment_allocations"].id.desc())
    ).all()

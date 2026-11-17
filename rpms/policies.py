"""Shared UI and server policy for generic record changes."""

from sqlalchemy import select

from .models import MODELS, db
from .record_config import EDITABLE


def change_policy(table, row):
    if table not in EDITABLE:
        return "Use the dedicated workflow for financial, system, and audit records."
    status = getattr(row, "status", None)
    if table in {"contract_renewals", "move_outs"} and status != "Draft":
        return "Only draft records can be edited or deleted."
    if table == "roles" and row.name in {"Admin", "Manager", "Cashier", "Viewer"}:
        return "Built-in roles are protected. Create a custom role instead."
    if table == "users":
        from .web import user

        current = user()
        if current and current.id == row.id:
            return "Your signed-in account is protected from changes here."
    if table == "rental_contracts" and status != "Draft":
        return "Only draft leases can be edited or deleted."
    if table == "reservations" and status not in ("Pending", "Cancelled"):
        return "This reservation is protected after confirmation."
    if table == "meter_readings" and status != "Draft":
        return "Approved or billed readings are protected."
    if table == "expenses" and status in ("Approved", "Paid"):
        return "Approved or paid expenses are protected."
    if table == "contract_tenants":
        lease = db.session.get(MODELS["rental_contracts"], row.contract_id)
        if lease and lease.status != "Draft":
            return "Tenant links are protected after lease activation."
    if table == "utility_meters" and db.session.scalar(
        select(MODELS["meter_readings"].id)
        .where(MODELS["meter_readings"].meter_id == row.id)
        .limit(1)
    ):
        return "Meters with readings are protected to preserve billing history."
    return None

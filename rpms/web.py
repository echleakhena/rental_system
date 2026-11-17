"""Shared blueprint, authentication, CSRF and template helpers."""

import secrets
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, request, session, url_for
from sqlalchemy import select

from .access import CAPABILITIES, TABLE_CAPABILITIES
from .models import MODELS, db
from .policies import change_policy
from .record_config import ADMIN_TABLES, GROUPS

bp = Blueprint("main", __name__)


def user():
    return (
        db.session.get(MODELS["users"], session.get("uid"))
        if session.get("uid")
        else None
    )


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = user()
        if not u or not u.is_active:
            return redirect(url_for("main.login"))
        role = db.session.get(MODELS["roles"], u.role_id)
        if not role or not role.is_active:
            abort(403)
        return fn(*a, **kw)

    return wrapper


def has_permission(code):
    u = user()
    role = db.session.get(MODELS["roles"], u.role_id) if u and u.is_active else None
    if not role or not role.is_active or code not in CAPABILITIES:
        return False
    if role.name in ("Admin", "Manager"):
        return True
    return (
        db.session.scalar(
            select(MODELS["role_permissions"].id)
            .join(
                MODELS["permissions"],
                MODELS["permissions"].id == MODELS["role_permissions"].permission_id,
            )
            .where(
                MODELS["role_permissions"].role_id == role.id,
                MODELS["permissions"].code == code,
            )
            .limit(1)
        )
        is not None
    )


def can_write():
    return any(has_permission(code) for code in CAPABILITIES)


def can_manage(table):
    if table in ADMIN_TABLES:
        u = user()
        role = db.session.get(MODELS["roles"], u.role_id) if u and u.is_active else None
        return bool(role and role.is_active and role.name == "Admin")
    return has_permission(TABLE_CAPABILITIES.get(table))


@bp.before_app_request
def csrf():
    session.setdefault("csrf", secrets.token_hex(32))
    if request.method == "POST" and not secrets.compare_digest(
        session["csrf"], request.form.get("_csrf", "")
    ):
        abort(400, "Invalid form token. Reload and try again.")


@bp.app_context_processor
def context():
    return dict(
        asset_url=asset_url,
        groups=GROUPS,
        current_user=user(),
        can_write=can_write(),
        can_manage=can_manage,
        today=date.today(),
        csrf_token=session.get("csrf"),
        titleize=lambda s: s.replace("_", " ").title(),
        change_policy=change_policy,
    )


def label(row, *, include_reservation_reference=True):
    if row.__tablename__ == "reservations":
        tenant = (
            db.session.get(MODELS["tenants"], row.tenant_id) if row.tenant_id else None
        )
        room = db.session.get(MODELS["rooms"], row.room_id) if row.room_id else None
        return " · ".join(
            part
            for part in (
                tenant.full_name if tenant else None,
                f"Room {room.room_number}" if room else None,
                (row.reservation_no or f"Reservation {row.id}")
                if include_reservation_reference
                else None,
            )
            if part
        )
    return next(
        (
            str(getattr(row, x))
            for x in [
                "name",
                "setting_key",
                "full_name",
                "room_number",
                "contract_no",
                "invoice_no",
                "meter_number",
                "tenant_code",
                "title",
            ]
            if hasattr(row, x) and getattr(row, x)
        ),
        str(row.id),
    )


def asset_url(filename):
    """Invalidate the browser cache automatically when a static file changes."""
    version = (Path(current_app.static_folder) / filename).stat().st_mtime_ns
    return url_for("static", filename=filename, v=version)

"""Utility prices and administrator-controlled staff access settings."""

from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..access import CAPABILITIES
from ..models import MODELS as M
from ..models import db
from ..services import audit
from ..web import bp, can_manage, can_write, login_required, user


@bp.route("/settings", defaults={"section": None}, methods=["GET", "POST"])
@bp.route("/settings/<section>", methods=["GET", "POST"])
@login_required
def settings(section):
    if not can_write():
        abort(403)
    requested_section = section
    if section is not None and section not in ("utilities", "permissions"):
        abort(404)
    if section is None:
        section = (
            "utilities"
            if can_manage("utility_meters")
            else "permissions"
            if can_manage("users")
            else "unavailable"
        )
    if request.method == "GET" and requested_section is not None:
        if not can_manage("utility_meters" if section == "utilities" else "users"):
            abort(403)
    if request.method == "POST":
        action_section = {
            "utility_rate": "utilities",
            "role_permissions": "permissions",
        }.get(request.form.get("action"))
        if requested_section is not None and action_section != requested_section:
            abort(400)
        if action_section:
            section = action_section
        try:
            action = request.form.get("action")
            if action == "utility_rate":
                if not can_manage("utility_meters"):
                    abort(403)
                meter = db.get_or_404(
                    M["utility_meters"], request.form.get("meter_id", type=int)
                )
                if meter.status != "Active":
                    raise ValueError(
                        "Only active meters can have their future rate changed."
                    )
                rate = Decimal(request.form.get("rate", ""))
                if (
                    not rate.is_finite()
                    or rate < 0
                    or rate > Decimal("99999999.9999")
                    or rate.as_tuple().exponent < -4
                ):
                    raise ValueError(
                        "Enter a nonnegative rate with up to four decimal places."
                    )
                meter.default_rate = rate.quantize(Decimal(".0001"))
                audit(user().id, "utility_meters", meter, "Update rate")
            elif action == "role_permissions":
                if not can_manage("roles"):
                    abort(403)
                role = db.get_or_404(M["roles"], request.form.get("role_id", type=int))
                if role.name in ("Admin", "Manager"):
                    raise ValueError(
                        "Admin and Manager have fixed access. Assign permissions to another role."
                    )
                selected = set(request.form.getlist("permissions"))
                if not selected.issubset(CAPABILITIES):
                    raise ValueError("Choose supported permissions.")
                for code, (name, description) in CAPABILITIES.items():
                    permission = db.session.scalar(
                        select(M["permissions"]).where(M["permissions"].code == code)
                    )
                    if not permission and code in selected:
                        permission = M["permissions"](
                            code=code, name=name, module=description
                        )
                        db.session.add(permission)
                        db.session.flush()
                    if not permission:
                        continue
                    link = db.session.scalar(
                        select(M["role_permissions"]).where(
                            M["role_permissions"].role_id == role.id,
                            M["role_permissions"].permission_id == permission.id,
                        )
                    )
                    if code in selected and not link:
                        db.session.add(
                            M["role_permissions"](
                                role_id=role.id, permission_id=permission.id
                            )
                        )
                    elif code not in selected and link:
                        db.session.delete(link)
                audit(user().id, "roles", role, "Update permissions")
            else:
                abort(400)
            db.session.commit()
            flash("Settings saved.", "success")
            return redirect(url_for("main.settings", section=section))
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            flash(
                str(exc)
                if isinstance(exc, ValueError)
                else "Could not save settings. Check the values and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "main.settings",
                    section=section if section != "unavailable" else None,
                )
            )
    meters = (
        db.session.scalars(
            select(M["utility_meters"])
            .where(M["utility_meters"].status == "Active")
            .order_by(M["utility_meters"].room_id, M["utility_meters"].meter_type)
        ).all()
        if section == "utilities" and can_manage("utility_meters")
        else []
    )
    roles = (
        db.session.scalars(select(M["roles"]).order_by(M["roles"].name)).all()
        if section == "permissions" and can_manage("roles")
        else []
    )
    grants = {r.id: set() for r in roles}
    if roles:
        for role_id, code in db.session.execute(
            select(M["role_permissions"].role_id, M["permissions"].code).join(
                M["permissions"],
                M["permissions"].id == M["role_permissions"].permission_id,
            )
        ):
            if role_id in grants:
                grants[role_id].add(code)
    return render_template(
        "settings.html",
        page_title="Utility prices"
        if section == "utilities"
        else "Permissions"
        if section == "permissions"
        else "Settings",
        settings_section=section,
        meters=meters,
        rooms={r.id: r for r in db.session.scalars(select(M["rooms"])).all()},
        roles=roles,
        grants=grants,
        capabilities=CAPABILITIES,
    )

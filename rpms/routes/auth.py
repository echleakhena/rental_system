"""Auth page routes."""

import secrets
from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash

from ..models import MODELS, db
from ..web import bp


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db.session.scalar(
            select(MODELS["users"]).where(
                MODELS["users"].email == request.form.get("email", "").strip().lower()
            )
        )
        if (
            u
            and u.is_active
            and check_password_hash(u.password_hash, request.form.get("password", ""))
        ):
            session.clear()
            session["uid"] = u.id
            session["csrf"] = secrets.token_hex(32)
            u.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            return redirect(url_for("main.dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))

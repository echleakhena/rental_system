"""Rooms page routes."""

from pathlib import Path
from uuid import uuid4

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import MODELS, db
from ..photos import ROOM_PHOTO_CATEGORIES, process_photo
from ..services import audit
from ..web import bp, can_manage, login_required, user


@bp.route("/rooms/<int:record_id>", methods=["GET", "POST"])
@login_required
def room_detail(record_id):
    room = (
        db.session.execute(
            select(MODELS["rooms"])
            .where(MODELS["rooms"].id == record_id)
            .with_for_update()
        ).scalar_one_or_none()
        if request.method == "POST"
        else db.session.get(MODELS["rooms"], record_id)
    )
    if room is None:
        abort(404)
    if request.method == "POST":
        if not can_manage("rooms"):
            abort(403)
        folder = Path(current_app.instance_path) / "room_photos"
        new_files = []
        old_files = []
        try:
            existing = list(room.photos or [])
            existing = (existing + [None] * len(ROOM_PHOTO_CATEGORIES))[
                : len(ROOM_PHOTO_CATEGORIES)
            ]
            changes = {}
            for slot in range(len(ROOM_PHOTO_CATEGORIES)):
                upload = request.files.get(f"photo_{slot}")
                if upload and upload.filename:
                    changes[slot] = process_photo(upload)
                elif request.form.get(f"remove_{slot}") == "yes":
                    changes[slot] = None
            if not changes:
                raise ValueError(
                    "Choose at least one photo or select a photo to remove."
                )
            folder.mkdir(exist_ok=True)
            for slot, data in changes.items():
                if existing[slot]:
                    old_files.append(existing[slot])
                if data is None:
                    existing[slot] = None
                else:
                    filename = uuid4().hex + ".jpg"
                    new_files.append(filename)
                    (folder / filename).write_bytes(data)
                    existing[slot] = filename
            room.photos = existing
            audit(user().id, "rooms", room, "Update")
            db.session.commit()
            for filename in old_files:
                try:
                    (folder / filename).unlink(missing_ok=True)
                except OSError:
                    current_app.logger.warning("Could not remove obsolete room photo")
            flash("Room photos saved.", "success")
        except (ValueError, OSError, IntegrityError) as exc:
            db.session.rollback()
            for filename in new_files:
                try:
                    (folder / filename).unlink(missing_ok=True)
                except OSError:
                    pass
            flash(
                str(exc)
                if isinstance(exc, ValueError)
                else "Photos could not be saved. Please try again.",
                "error",
            )
        return redirect(url_for("main.room_detail", record_id=record_id))
    floor = db.session.get(MODELS["floors"], room.floor_id) if room.floor_id else None
    prop = db.session.get(MODELS["properties"], floor.property_id) if floor else None
    return render_template(
        "room.html",
        table="rooms",
        room=room,
        floor=floor,
        property=prop,
        photo_categories=ROOM_PHOTO_CATEGORIES,
        photos=(list(room.photos or []) + [None] * len(ROOM_PHOTO_CATEGORIES))[
            : len(ROOM_PHOTO_CATEGORIES)
        ],
    )


@bp.get("/rooms/<int:record_id>/photos/<int:slot>")
@login_required
def room_photo(record_id, slot):
    if slot not in range(len(ROOM_PHOTO_CATEGORIES)):
        abort(404)
    room = db.get_or_404(MODELS["rooms"], record_id)
    photos = room.photos or []
    if slot >= len(photos) or not photos[slot]:
        abort(404)
    response = send_from_directory(
        Path(current_app.instance_path) / "room_photos",
        photos[slot],
        mimetype="image/jpeg",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.app_errorhandler(413)
def upload_too_large(error):
    if request.path.startswith("/customer/"):
        return render_template("customer/upload_error.html", nav="rentals"), 413
    return render_template("upload_error.html"), 413

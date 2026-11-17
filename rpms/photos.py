"""Bounded raster photo processing; uploaded names and metadata are not retained."""

import warnings
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

ROOM_PHOTO_CATEGORIES = ("Room", "Bathroom", "Kitchen", "Toilet")

MAX_PHOTO_BYTES = 5 * 1024 * 1024


def update_room_photos(room, files, form, new_files, old_files):
    """Stage validated photos; callers commit metadata before removing old files."""
    existing = (list(room.photos or []) + [None] * len(ROOM_PHOTO_CATEGORIES))[
        : len(ROOM_PHOTO_CATEGORIES)
    ]
    changes = {}
    for slot in range(len(ROOM_PHOTO_CATEGORIES)):
        upload = files.get(f"photo_{slot}")
        if upload and upload.filename:
            changes[slot] = process_photo(upload)
        elif form.get(f"remove_{slot}") == "yes":
            changes[slot] = None
    if not changes:
        return False
    folder = Path(current_app.instance_path) / "room_photos"
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
    return True


def remove_room_photo_files(names):
    for name in names:
        try:
            (Path(current_app.instance_path) / "room_photos" / name).unlink(
                missing_ok=True
            )
        except OSError:
            current_app.logger.warning("Could not remove unused room photo")


def process_photo(upload):
    data = upload.read(MAX_PHOTO_BYTES + 1)
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Each photo must be between 1 byte and 5 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("Use JPEG, PNG or WebP photos.")
                if image.width * image.height > 20_000_000:
                    raise ValueError("Photos must be 20 megapixels or smaller.")
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((1600, 1600))
                result = BytesIO()
                image.save(result, format="JPEG", quality=85)
                return result.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError("This file is not a valid supported photo.")

"""Bounded raster photo processing; uploaded names and metadata are not retained."""

import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

ROOM_PHOTO_CATEGORIES = ("Room", "Bathroom", "Kitchen", "Toilet")

MAX_PHOTO_BYTES = 5 * 1024 * 1024


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

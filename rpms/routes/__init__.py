"""Register page modules and expose the main Flask blueprint."""

from ..web import bp as bp
from . import auth, billing, dashboard, records, reports, rooms, settings  # noqa: F401

"""Web UI: a FastAPI layer over the existing modules plus a no-build HTML/JS frontend (static/)."""
from .api import create_app

__all__ = ["create_app"]

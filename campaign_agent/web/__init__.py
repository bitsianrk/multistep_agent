"""Web UI: Flask API (app.py) + React front end (frontend/, built into dist/)."""

from .app import create_app, main

__all__ = ["create_app", "main"]

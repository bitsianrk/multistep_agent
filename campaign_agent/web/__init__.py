"""Web UI: Flask API + a single static page (no build step, no CDN)."""

from .app import create_app, main

__all__ = ["create_app", "main"]

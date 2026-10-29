"""Web UI: a standard-library HTTP server + a single static page (no build step, no CDN)."""

from .app import create_app, main

__all__ = ["create_app", "main"]

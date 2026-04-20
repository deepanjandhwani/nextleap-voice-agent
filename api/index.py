"""Vercel FastAPI entrypoint — re-exports the main ASGI app after `pip install .`."""

from advisor_scheduler.api.app import app

__all__ = ["app"]

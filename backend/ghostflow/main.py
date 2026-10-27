"""ASGI entry point: `uvicorn ghostflow.main:app`."""

from ghostflow.api import create_app

app = create_app()

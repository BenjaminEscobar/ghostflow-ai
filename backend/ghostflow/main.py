"""ASGI entry point: `uvicorn ghostflow.main:app`."""

import logging

from ghostflow.api import create_app

# GhostFlow's own operational logs (provider, model, token counts; never prompts or secrets).
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
logging.getLogger("ghostflow").addHandler(_handler)
logging.getLogger("ghostflow").setLevel(logging.INFO)

app = create_app()

"""Demo Console (GF-004): a static page at /console plus read-only JSON views for it.

The console never starts, changes or deletes anything. It shows demo evidence bundles
(ghostflow/evidence.py) and the stored runs of backend workflows; every response is
sanitised again on the way out and served with a restrictive Content-Security-Policy.
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select

from ghostflow.dsl.models import Workflow
from ghostflow.evidence import EvidenceNotFound, EvidenceStore, bundle_from_runs, interpretation_badge, present
from ghostflow.storage import RunRow, WorkflowRow, list_rows

STATIC_DIR = Path(__file__).parent / "static"
MAX_RUNS_SHOWN = 5

CONSOLE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

router = APIRouter(prefix="/api/demo")


def _store(request: Request) -> EvidenceStore:
    return request.app.state.evidence_store


@router.get("/sources")
def list_sources(request: Request) -> dict:
    sources = _store(request).entries()
    with request.app.state.session_factory() as session:
        for row in list_rows(session, WorkflowRow):
            interp = (row.definition or {}).get("interpretation")
            sources.append(
                {
                    "collection": "workflows",
                    "name": row.id,
                    "title": row.name,
                    "updated_at": row.created_at.isoformat(),
                    "valid": True,
                    "presentation": {"mode": "stored", "text": "Stored runs from the backend database"},
                    "interpretation_badge": interpretation_badge(interp),
                }
            )
    settings = request.app.state.settings
    return {
        "sources": sources,
        "backend": {
            "ai_provider": request.app.state.provider.name,
            "healing_enabled": settings.healing_enabled,
            "step_delay_ms": settings.step_delay_ms,
        },
    }


@router.get("/sources/{collection}/{name}")
def get_source(collection: str, name: str, request: Request) -> dict:
    if collection == "workflows":
        with request.app.state.session_factory() as session:
            row = session.get(WorkflowRow, name)
            if row is None:
                raise HTTPException(404, "Workflow not found")
            runs = session.scalars(
                select(RunRow).where(RunRow.workflow_id == row.id).order_by(RunRow.created_at.desc()).limit(MAX_RUNS_SHOWN)
            ).all()
            try:
                bundle = bundle_from_runs(
                    Workflow.model_validate(row.definition),
                    [(r.id, r.created_at, r.inputs, r.result) for r in reversed(runs)],
                    title=row.name,
                )
            except ValidationError:
                raise HTTPException(422, "Stored workflow or run cannot be shown") from None
    else:
        try:
            bundle = _store(request).load(collection, name)
        except EvidenceNotFound:
            raise HTTPException(404, "Evidence not found") from None
        except (ValidationError, ValueError):
            raise HTTPException(422, "Not a GhostFlow demo evidence bundle") from None
    return {"collection": collection, "name": name, "bundle": bundle.model_dump(mode="json"), **present(bundle, collection)}


def mount_console(app: FastAPI, settings) -> None:
    app.state.evidence_store = EvidenceStore(Path(settings.demo_recorded_dir), Path(settings.demo_evidence_dir))
    app.include_router(router)

    @app.get("/console", include_in_schema=False)
    def console_root() -> RedirectResponse:
        return RedirectResponse("/console/")

    app.mount("/console", StaticFiles(directory=STATIC_DIR, html=True), name="console")

    @app.middleware("http")
    async def console_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/console", "/api/demo")):
            response.headers["Content-Security-Policy"] = CONSOLE_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
        return response

"""HTTP API: recordings in, workflows out, runs on demand."""

import logging
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from ghostflow import __version__
from ghostflow.ai import ModelProvider, ModelProviderError, RecordingContext, annotate_workflow, provider_from_settings
from ghostflow.config import Settings, get_settings
from ghostflow.console import mount_console
from ghostflow.dsl.models import Workflow
from ghostflow.dsl.schema import workflow_json_schema
from ghostflow.execution import ExecutionAdapter, Healer, InputError, RecoveryPolicy, redact_inputs
from ghostflow.recording import RecordingCreate, RecordingRejectedError, compile_recording, sanitize_recording
from ghostflow.recording.compiler import CompilationError
from ghostflow.security import UrlNotAllowedError, UrlPolicy
from ghostflow.storage import RecordingRow, RunRow, WorkflowRow, list_rows, make_engine, make_session_factory

log = logging.getLogger("ghostflow.api")

# -- response / request models ------------------------------------------------------------


class RecordingSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    event_count: int


class RecordingOut(RecordingSummary):
    recording: RecordingCreate


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    annotate: bool = True


class WorkflowSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    source_recording_id: str | None
    annotated_by: str | None


class WorkflowOut(WorkflowSummary):
    definition: Workflow
    # Set on compilation when the AI interpretation was skipped (the workflow is still valid).
    ai_error: str | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Annotated[str, Field(max_length=2000)]] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: str
    workflow_id: str
    created_at: datetime
    status: str
    inputs: dict[str, str]
    result: dict[str, Any]


# -- dependencies -------------------------------------------------------------------------


def _session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(_session)]


def _get_or_404(session: Session, model, id_: str, what: str):
    row = session.get(model, id_)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
    return row


def _workflow_out(row: WorkflowRow) -> WorkflowOut:
    return WorkflowOut(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        source_recording_id=row.source_recording_id,
        annotated_by=row.annotated_by,
        definition=Workflow.model_validate(row.definition),
    )


def _run_out(row: RunRow) -> RunOut:
    return RunOut(
        id=row.id, workflow_id=row.workflow_id, created_at=row.created_at, status=row.status, inputs=row.inputs, result=row.result
    )


def _check_workflow_origins(policy: UrlPolicy, workflow: Workflow) -> None:
    try:
        policy.restricted_to(workflow.allowed_origins)
    except UrlNotAllowedError as exc:
        raise HTTPException(422, str(exc)) from None


# -- routes -------------------------------------------------------------------------------

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict:
    provider = request.app.state.provider
    return {"status": "ok", "version": __version__, "ai_provider": provider.name, "ai_model": provider.model}


@router.get("/schema/workflow")
def workflow_schema() -> dict:
    return workflow_json_schema()


@router.post("/recordings", status_code=status.HTTP_201_CREATED)
def create_recording(body: RecordingCreate, request: Request, session: SessionDep) -> RecordingSummary:
    try:
        clean = sanitize_recording(body, request.app.state.policy)
    except RecordingRejectedError as exc:
        raise HTTPException(422, str(exc)) from None
    row = RecordingRow(name=clean.name, event_count=len(clean.events), payload=clean.model_dump(mode="json"))
    session.add(row)
    session.commit()
    return RecordingSummary(id=row.id, name=row.name, created_at=row.created_at, event_count=row.event_count)


@router.get("/recordings")
def list_recordings(session: SessionDep) -> list[RecordingSummary]:
    return [
        RecordingSummary(id=r.id, name=r.name, created_at=r.created_at, event_count=r.event_count)
        for r in list_rows(session, RecordingRow)
    ]


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: str, session: SessionDep) -> RecordingOut:
    r = _get_or_404(session, RecordingRow, recording_id, "Recording")
    return RecordingOut(
        id=r.id, name=r.name, created_at=r.created_at, event_count=r.event_count, recording=RecordingCreate.model_validate(r.payload)
    )


@router.post("/recordings/{recording_id}/workflow", status_code=status.HTTP_201_CREATED)
def compile_workflow(recording_id: str, body: CompileRequest, request: Request, session: SessionDep) -> WorkflowOut:
    r = _get_or_404(session, RecordingRow, recording_id, "Recording")
    recording = RecordingCreate.model_validate(r.payload)
    try:
        workflow = compile_recording(recording, name=body.name)
    except (CompilationError, ValueError) as exc:
        raise HTTPException(422, f"Cannot compile recording: {exc}") from None
    _check_workflow_origins(request.app.state.policy, workflow)

    annotated_by = ai_error = None
    if body.annotate:
        provider: ModelProvider = request.app.state.provider
        try:
            workflow = annotate_workflow(workflow, provider, RecordingContext.from_recording(recording))
            if body.name:
                workflow = workflow.model_copy(update={"name": body.name})
            annotated_by = provider.name
            log.info("ai.interpretation recording=%s provider=%s model=%s", r.id, provider.name, provider.model)
        except ModelProviderError as exc:
            # Interpretation is optional: keep the deterministic workflow and say why.
            ai_error = str(exc)
            log.warning(
                "ai.interpretation_skipped recording=%s provider=%s model=%s reason=%s",
                r.id, provider.name, provider.model, ai_error,
            )  # fmt: skip

    row = WorkflowRow(
        name=workflow.name, source_recording_id=r.id, annotated_by=annotated_by, definition=workflow.model_dump(mode="json")
    )
    session.add(row)
    session.commit()
    return _workflow_out(row).model_copy(update={"ai_error": ai_error})


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
def create_workflow(body: Workflow, request: Request, session: SessionDep) -> WorkflowOut:
    _check_workflow_origins(request.app.state.policy, body)
    row = WorkflowRow(name=body.name, definition=body.model_dump(mode="json"))
    session.add(row)
    session.commit()
    return _workflow_out(row)


@router.get("/workflows")
def list_workflows(session: SessionDep) -> list[WorkflowSummary]:
    return [
        WorkflowSummary(
            id=w.id, name=w.name, created_at=w.created_at, source_recording_id=w.source_recording_id, annotated_by=w.annotated_by
        )
        for w in list_rows(session, WorkflowRow)
    ]


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, session: SessionDep) -> WorkflowOut:
    return _workflow_out(_get_or_404(session, WorkflowRow, workflow_id, "Workflow"))


@router.post("/workflows/{workflow_id}/runs", status_code=status.HTTP_201_CREATED)
def run_workflow(workflow_id: str, body: RunRequest, request: Request, session: SessionDep) -> RunOut:
    row = _get_or_404(session, WorkflowRow, workflow_id, "Workflow")
    workflow = Workflow.model_validate(row.definition)
    _check_workflow_origins(request.app.state.policy, workflow)
    adapter: ExecutionAdapter | None = request.app.state.adapters.get(workflow.adapter)
    if adapter is None:
        raise HTTPException(422, f"No execution adapter for '{workflow.adapter}'")

    slots: threading.BoundedSemaphore = request.app.state.run_slots
    if not slots.acquire(blocking=False):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many runs in progress, retry later")
    try:
        result = adapter.execute(workflow, body.inputs)
    except InputError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        slots.release()

    run = RunRow(
        workflow_id=row.id,
        status=result.status,
        inputs=redact_inputs(workflow, body.inputs),
        result=result.model_dump(mode="json"),
    )
    session.add(run)
    session.commit()
    return _run_out(run)


@router.get("/workflows/{workflow_id}/runs")
def list_runs(workflow_id: str, session: SessionDep) -> list[RunOut]:
    _get_or_404(session, WorkflowRow, workflow_id, "Workflow")
    return [_run_out(r) for r in list_rows(session, RunRow, workflow_id=workflow_id)]


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: SessionDep) -> RunOut:
    return _run_out(_get_or_404(session, RunRow, run_id, "Run"))


# -- app factory --------------------------------------------------------------------------

AdapterFactory = Callable[[Settings, UrlPolicy], dict[str, ExecutionAdapter]]


def make_healer(settings: Settings, policy: UrlPolicy, provider: ModelProvider | None) -> Healer:
    """Self-healing is off unless GHOSTFLOW_HEALING_ENABLED=true. It reuses the app's AI provider,
    so Meta's request cap, timeout and token limit apply to recovery calls too."""
    from ghostflow.ai.recovery import ModelRecoveryAdvisor

    recovery_policy = RecoveryPolicy(
        enabled=settings.healing_enabled,
        min_confidence=settings.healing_min_confidence,
        allow_side_effects=settings.healing_allow_side_effects,
        max_candidates=settings.healing_max_candidates,
        max_attempts_per_run=settings.healing_max_attempts_per_run,
    )
    advisor = ModelRecoveryAdvisor(provider) if settings.healing_enabled and provider is not None else None
    return Healer(recovery_policy, policy, advisor)


def default_adapters(
    settings: Settings, policy: UrlPolicy, provider: ModelProvider | None = None
) -> dict[str, ExecutionAdapter]:
    from ghostflow.execution.playwright_web import PlaywrightWebAdapter

    return {
        "web": PlaywrightWebAdapter(
            policy,
            headless=settings.browser_headless,
            step_timeout_ms=settings.step_timeout_ms,
            step_delay_ms=settings.step_delay_ms,
            final_hold_ms=settings.final_hold_ms,
            healer=make_healer(settings, policy, provider),
        )
    }


def create_app(
    settings: Settings | None = None,
    *,
    provider: ModelProvider | None = None,
    adapters: AdapterFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="GhostFlow AI", version=__version__)
    app.state.settings = settings
    app.state.policy = UrlPolicy(settings.allowed_origin_list)
    app.state.session_factory = make_session_factory(make_engine(settings.database_url))
    app.state.provider = provider or provider_from_settings(settings)
    log.info(
        "AI provider: %s (model %s); self-healing %s",
        app.state.provider.name, app.state.provider.model, "enabled" if settings.healing_enabled else "disabled",
    )  # fmt: skip
    if adapters is None:
        app.state.adapters = default_adapters(settings, app.state.policy, app.state.provider)
    else:
        app.state.adapters = adapters(settings, app.state.policy)
    app.state.run_slots = threading.BoundedSemaphore(settings.max_concurrent_runs)
    app.include_router(router)
    mount_console(app, settings)
    return app

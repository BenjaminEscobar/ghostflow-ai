"""Demo evidence bundles (GF-004): the data the Demo Console shows.

A bundle describes one demonstration: the workflow (outline, parameters, AI interpretation and
SHA-256 before/after), one or more scenarios (A original portal, B redesigned portal without
self-healing, C redesigned portal with self-healing) and which AI provider took part.

Where bundles come from, and how the console must present them:
- the demo runner (ghostflow.demo_healing) writes one while it runs: "live" while it is being
  updated, a "saved run" afterwards;
- `import-gf003` converts the GF-003 manual-validation evidence into a bundle under
  docs/evidence: "recorded evidence", never shown as live;
- stored runs of a backend workflow are turned into a bundle on request (read-only).

Nothing here executes anything. Bundles never hold sensitive input values, secrets or full
page content; every string is re-checked when a bundle is read.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ghostflow.dsl.models import NavigateStep, Workflow, WaitForUrlStep
from ghostflow.execution.base import RunResult, StepResult, VerificationResult, redact_inputs
from ghostflow.execution.recovery import RecoveryAttempt
from ghostflow.security.redaction import looks_like_secret

FORMAT = "ghostflow.demo-evidence/1"
LIVE_WINDOW = timedelta(seconds=20)  # a running bundle not updated for this long is "stale"

StepState = Literal["pending", "running", "completed", "failed", "recovered", "skipped"]


def _now() -> datetime:
    return datetime.now(UTC)


def mask_secrets(text: str | None, limit: int = 600) -> str | None:
    """Replace token-shaped words (JWTs, bearer values, long opaque strings) and clip."""
    if text is None:
        return None
    words = re.split(r"(\s+)", text)
    out = "".join("[secret]" if looks_like_secret(w) else w for w in words)
    return out if len(out) <= limit else out[: limit - 1] + "…"


def workflow_digest(workflow: Workflow) -> str:
    return hashlib.sha256(workflow.model_dump_json().encode()).hexdigest()


# -- model --------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepOutline(_Model):
    id: str
    action: str
    label: str | None = None
    target: str | None = None  # accessible description of the element, e.g. link "Nueva solicitud"
    location: str | None = None  # navigate: origin + path (no query); wait_for_url: the path


class InputView(_Model):
    name: str
    label: str | None = None
    sensitive: bool = False


class WorkflowView(_Model):
    name: str
    description: str | None = None
    source: str  # where the workflow came from, in words
    sha256_before: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sha256_after: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    allowed_origins: list[str] = Field(default_factory=list)
    steps: list[StepOutline] = Field(default_factory=list)
    inputs: list[InputView] = Field(default_factory=list)
    verify: list[str] = Field(default_factory=list)
    # The workflow's own `interpretation` block (provider, model and date included), or None.
    interpretation: dict[str, Any] | None = None

    @property
    def unchanged(self) -> bool | None:
        if self.sha256_before is None or self.sha256_after is None:
            return None
        return self.sha256_before == self.sha256_after


class StepView(_Model):
    id: str
    action: str
    label: str | None = None
    target: str | None = None
    location: str | None = None
    state: StepState = "pending"
    phase: Literal["recovering"] | None = None  # live only: the step is in its recovery attempt
    exhausted_strategies: list[str] = Field(default_factory=list)  # live only, until `recovery` is set
    resolution: Literal["primary", "deterministic_fallback", "ai_recovery"] | None = None
    strategy_by: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    recovery: RecoveryAttempt | None = None


class ScenarioView(_Model):
    key: str
    title: str
    portal: str  # "original" or the variant name
    healing: bool
    state: Literal["pending", "running", "finished", "interrupted"] = "pending"
    status: Literal["passed", "failed", "error"] | None = None
    inputs: dict[str, str] = Field(default_factory=dict)  # sensitive values are "***"
    steps: list[StepView] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)
    blocked_requests: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # True when step states were reconstructed from a summary (imported evidence), not observed.
    derived_steps: bool = False


class AiUsage(_Model):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


class AiSource(_Model):
    """The AI provider that advised self-healing in this demonstration."""

    provider: Literal["meta", "mock", "none"]
    model: str | None = None
    requests_sent: int = 0  # real HTTP requests to Meta (always 0 for mock)
    request_cap: int | None = None
    usage: AiUsage | None = None


class EvidenceBundle(_Model):
    format: Literal["ghostflow.demo-evidence/1"] = FORMAT
    title: str
    state: Literal["running", "finished", "interrupted"] = "running"
    created_at: datetime
    updated_at: datetime
    origin: Literal["demo_runner", "gf003_import", "backend_runs"]
    provenance: str | None = None
    ai: AiSource
    workflow: WorkflowView
    scenarios: list[ScenarioView] = Field(default_factory=list)


# -- builders -----------------------------------------------------------------------------


def step_label(step) -> str | None:
    if isinstance(step, NavigateStep):
        return f"Open {step.url}"
    if isinstance(step, WaitForUrlStep):
        return f"Wait for {step.path}"
    target = getattr(step, "target", None)
    return step.description or (target.description if target is not None else None)


def step_location(step) -> str | None:
    if isinstance(step, NavigateStep):
        parts = urlsplit(step.url)
        return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"
    if isinstance(step, WaitForUrlStep):
        return step.path
    return None


def step_target(step) -> str | None:
    target = getattr(step, "target", None)
    return target.description if target is not None else None


def _verify_text(check) -> str:
    data = check.model_dump(mode="json")
    detail = ", ".join(f"{k}={v}" for k, v in data.items() if k != "type" and not isinstance(v, dict))
    return f"{check.type}: {detail}" if detail else check.type


def outline_workflow(workflow: Workflow, source: str) -> WorkflowView:
    return WorkflowView(
        name=workflow.name,
        description=workflow.description,
        source=source,
        sha256_before=workflow_digest(workflow),
        allowed_origins=list(workflow.allowed_origins),
        steps=[StepOutline(id=s.id, action=s.action, label=step_label(s), target=step_target(s), location=step_location(s)) for s in workflow.steps],
        inputs=[InputView(name=i.name, label=i.label, sensitive=i.sensitive) for i in workflow.inputs],
        verify=[_verify_text(v) for v in workflow.verify],
        interpretation=workflow.interpretation.model_dump(mode="json") if workflow.interpretation else None,
    )


def scenario_title(key: str, portal: str, healing: bool, provider: str | None = None) -> str:
    where = "Original portal" if portal == "original" else f"Redesigned portal ({portal})"
    return f"{key} · {where} · self-healing " + (f"on ({provider})" if healing else "off")


def new_scenario(
    key: str, title: str, *, portal: str, healing: bool, workflow: Workflow, inputs: dict[str, str]
) -> ScenarioView:
    return ScenarioView(
        key=key,
        title=title,
        portal=portal,
        healing=healing,
        inputs=redact_inputs(workflow, inputs),
        steps=[StepView(id=s.id, action=s.action, label=step_label(s), target=step_target(s), location=step_location(s)) for s in workflow.steps],
    )


def step_state(result: StepResult) -> StepState:
    if result.status == "failed":
        return "failed"
    if result.status == "skipped":
        return "skipped"
    return "recovered" if result.resolution == "ai_recovery" else "completed"


def _apply_result(view: StepView, result: StepResult) -> None:
    view.state = step_state(result)
    view.phase = None
    view.exhausted_strategies = []
    view.resolution = result.resolution
    view.strategy_by = result.strategy_by
    view.duration_ms = result.duration_ms if result.status != "skipped" else None
    view.error = result.error
    view.recovery = result.recovery


def apply_event(scenario: ScenarioView, event: str, payload: dict) -> None:
    """Update a running scenario from an adapter event (see PlaywrightWebAdapter.execute)."""
    by_id = {s.id: s for s in scenario.steps}
    if event == "step_started" and payload.get("step_id") in by_id:
        by_id[payload["step_id"]].state = "running"
    elif event == "recovery_started" and payload.get("step_id") in by_id:
        view = by_id[payload["step_id"]]
        view.phase = "recovering"
        view.exhausted_strategies = [mask_secrets(str(x), 200) or "" for x in payload.get("exhausted_strategies", [])][:10]
    elif event == "step_finished":
        result = StepResult.model_validate(payload)
        if result.step_id in by_id:
            _apply_result(by_id[result.step_id], result)
    elif event == "verification_finished":
        scenario.verifications.append(VerificationResult.model_validate(payload))


def finish_scenario(scenario: ScenarioView, result: RunResult) -> None:
    """The adapter's RunResult is authoritative: it replaces whatever events said."""
    by_id = {s.id: s for s in scenario.steps}
    for step in result.steps:
        if step.step_id in by_id:
            _apply_result(by_id[step.step_id], step)
    scenario.state = "finished"
    scenario.status = result.status
    scenario.verifications = list(result.verifications)
    scenario.blocked_requests = list(result.blocked_requests)
    scenario.error = result.error
    scenario.started_at, scenario.finished_at = result.started_at, result.finished_at


# -- sanitisation (on every read and write) -----------------------------------------------


def sanitize_bundle(bundle: EvidenceBundle) -> EvidenceBundle:
    """Defence in depth for files on disk: sensitive inputs are masked and token-shaped words
    in free text are replaced, whatever produced the file."""
    sensitive = {i.name for i in bundle.workflow.inputs if i.sensitive}
    for scenario in bundle.scenarios:
        scenario.inputs = {
            k: ("***" if k in sensitive or looks_like_secret(v) else mask_secrets(v, 200) or "")
            for k, v in scenario.inputs.items()
        }
        scenario.error = mask_secrets(scenario.error)
        scenario.blocked_requests = [mask_secrets(u, 300) or "" for u in scenario.blocked_requests[:50]]
        for step in scenario.steps:
            step.error = mask_secrets(step.error)
            if step.recovery is not None:
                r = step.recovery
                r.justification = mask_secrets(r.justification)
                r.reason = mask_secrets(r.reason)
                for check in r.validation:
                    check.detail = mask_secrets(check.detail)
        for v in scenario.verifications:
            v.detail = mask_secrets(v.detail)
    bundle.provenance = mask_secrets(bundle.provenance)
    return bundle


# -- presentation summary -----------------------------------------------------------------


def ai_badge(ai: AiSource) -> dict:
    """How the console labels the AI that advised self-healing. Never ambiguous."""
    if ai.provider == "meta" and ai.requests_sent > 0:
        n = ai.requests_sent
        return {"kind": "real", "text": f"Real Meta Model API · {ai.model} · {n} request{'s' if n != 1 else ''}"}
    if ai.provider == "meta":
        return {"kind": "none", "text": f"Meta configured ({ai.model}) · no request sent"}
    if ai.provider == "mock":
        return {"kind": "simulated", "text": "Simulated AI · offline mock · no Meta request"}
    return {"kind": "none", "text": "No AI recovery in this evidence"}


def interpretation_badge(interpretation: dict | None) -> dict:
    if not interpretation:
        return {"kind": "none", "text": "No AI interpretation stored for this workflow"}
    provider, model = interpretation.get("provider"), interpretation.get("model")
    if provider == "meta":
        return {"kind": "real", "text": f"Meta Muse Spark · {model}"}
    return {"kind": "simulated", "text": f"Simulated interpretation · {provider} ({model})"}


def presentation_mode(bundle: EvidenceBundle, collection: str, now: datetime | None = None) -> dict:
    """live / saved / recorded / stored, with the words the console shows. Recorded evidence is
    never presented as live, whatever its `state` says."""
    now = now or _now()
    if collection == "recorded":
        return {"mode": "recorded", "text": "Recorded evidence · replay of a saved run, not live"}
    if collection == "workflows":
        return {"mode": "stored", "text": "Stored runs from the backend database"}
    if bundle.state == "running" and now - bundle.updated_at <= LIVE_WINDOW:
        return {"mode": "live", "text": "Live · the demo runner is updating this run"}
    if bundle.state == "running":
        return {"mode": "stale", "text": "Interrupted · the demo runner stopped updating this run"}
    if bundle.state == "interrupted":
        return {"mode": "stale", "text": "Interrupted run"}
    return {"mode": "saved", "text": f"Saved run · finished {bundle.updated_at:%Y-%m-%d %H:%M} UTC"}


def present(bundle: EvidenceBundle, collection: str, now: datetime | None = None) -> dict:
    return {
        "presentation": presentation_mode(bundle, collection, now),
        "ai_badge": ai_badge(bundle.ai),
        "interpretation_badge": interpretation_badge(bundle.workflow.interpretation),
        "workflow_unchanged": bundle.workflow.unchanged,
    }


# -- stored backend runs ------------------------------------------------------------------


def bundle_from_runs(workflow: Workflow, runs: Iterable[tuple[str, datetime, dict, dict]], *, title: str) -> EvidenceBundle:
    """Read-only view of a backend workflow and its stored runs (id, created_at, inputs, result)."""
    view = outline_workflow(workflow, "Backend workflow (compiled from a recording)")
    view.sha256_after = view.sha256_before  # runs never modify the stored definition
    scenarios, providers, usage = [], set(), AiUsage()
    for i, (run_id, created_at, inputs, raw) in enumerate(runs):
        result = RunResult.model_validate(raw)
        scenario = new_scenario(
            f"R{i + 1}", f"Run {run_id[:8]} · {created_at:%Y-%m-%d %H:%M}", portal="configured origin",
            healing=any(s.recovery is not None for s in result.steps), workflow=workflow, inputs={},
        )  # fmt: skip
        scenario.inputs = dict(inputs)  # stored already redacted
        finish_scenario(scenario, result)
        scenarios.append(scenario)
        for s in result.steps:
            r = s.recovery
            if r and r.consulted_model and r.provider:
                providers.add((r.provider, r.model))
                usage.prompt_tokens += r.prompt_tokens or 0
                usage.completion_tokens += r.completion_tokens or 0
                usage.reasoning_tokens += r.reasoning_tokens or 0
    if any(p == "meta" for p, _ in providers):
        consulted = sum(1 for sc in scenarios for s in sc.steps if s.recovery and s.recovery.provider == "meta")
        ai = AiSource(provider="meta", model=next(m for p, m in providers if p == "meta"), requests_sent=consulted, usage=usage)
    elif providers:
        ai = AiSource(provider="mock", model=next(iter(providers))[1])
    else:
        ai = AiSource(provider="none")
    stamps = [sc.finished_at for sc in scenarios if sc.finished_at] or [_now()]
    return sanitize_bundle(
        EvidenceBundle(
            title=title, state="finished", created_at=min(stamps), updated_at=max(stamps), origin="backend_runs",
            provenance=(
                "Built on request from the backend database; nothing was executed to show it. "
                "Meta requests = recovery consultations recorded in the runs (retries are not stored)."
            ),
            ai=ai, workflow=view, scenarios=scenarios,
        )  # fmt: skip
    )


# -- GF-003 evidence import ---------------------------------------------------------------


def import_gf003(legacy: dict, workflow: Workflow, *, source: str, captured_at: datetime, note: str) -> EvidenceBundle:
    """Convert a GF-003 `demo_healing --out` summary into a bundle. The workflow must be the one
    that run used (same SHA-256). Step states other than the failed and AI-recovered steps are
    derived from the adapter's rules (sequential, stop at the first failure) and flagged."""
    digest = workflow_digest(workflow)
    if legacy.get("workflow_sha256_before") != digest:
        raise ValueError("The workflow does not match the evidence (SHA-256 differs)")
    view = outline_workflow(workflow, source)
    view.sha256_after = legacy.get("workflow_sha256_after")

    scenarios: list[ScenarioView] = []
    providers: set[tuple[str, str | None]] = set()
    for raw in legacy.get("scenarios", []):
        original_title = raw["scenario"]  # e.g. "B · variante crear-requerimiento, sin self-healing"
        key = original_title.split("·", 1)[0].strip()
        variant = "original" if "original" in original_title else original_title.split("variante", 1)[-1].split(",", 1)[0].strip()
        healing = "self-healing con" in original_title
        provider = original_title.rsplit("self-healing con", 1)[-1].strip() if healing else None
        title = scenario_title(key, variant, healing, provider)
        scenario = new_scenario(key, title, portal=variant, healing=healing, workflow=workflow, inputs={})
        scenario.derived_steps = True
        attempts = {a["step_id"]: {k: v for k, v in a.items() if k != "step_id"} for a in raw.get("recovery_attempts", [])}
        failed, after_failure = raw.get("failed_step"), False
        for step in scenario.steps:
            if after_failure:
                step.state = "skipped"
                continue
            if step.id in attempts:
                step.recovery = RecoveryAttempt.model_validate(attempts[step.id])
                r = step.recovery
                if r.consulted_model and r.provider:
                    providers.add((r.provider, r.model))
            if step.id == failed:
                step.state, step.error, after_failure = "failed", raw.get("failed_error"), True
            elif step.id in raw.get("ai_recovered_steps", []):
                step.state, step.resolution = "recovered", "ai_recovery"
            else:
                step.state = "completed"
                if step.id in raw.get("deterministic_fallback_steps", []):
                    step.resolution = "deterministic_fallback"
        scenario.state = "finished"
        scenario.status = raw["status"]
        scenario.verifications = [VerificationResult.model_validate(v) for v in raw.get("verifications", [])]
        scenario.blocked_requests = list(raw.get("blocked_requests", []))
        scenarios.append(scenario)

    usage = legacy.get("meta_usage")
    if any(p == "meta" for p, _ in providers):
        ai = AiSource(
            provider="meta",
            model=next(m for p, m in providers if p == "meta"),
            requests_sent=int(legacy.get("meta_requests_sent", 0)),
            request_cap=1,
            usage=AiUsage(**usage) if usage else None,
        )
    elif providers:
        ai = AiSource(provider="mock", model=next(iter(providers))[1])
    else:
        ai = AiSource(provider="none")
    return sanitize_bundle(
        EvidenceBundle(
            title=f"A/B/C self-healing · {'Meta Muse Spark' if ai.provider == 'meta' else ai.provider} · GF-003 validation",
            state="finished", created_at=captured_at, updated_at=captured_at, origin="gf003_import",
            provenance=note, ai=ai, workflow=view, scenarios=scenarios,
        )  # fmt: skip
    )


# -- storage ------------------------------------------------------------------------------

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
Collection = Literal["recorded", "runs"]


def write_bundle(path: Path, bundle: EvidenceBundle) -> None:
    """Atomic replace, so a reader polling the file never sees half a document."""
    bundle.updated_at = _now() if bundle.origin == "demo_runner" else bundle.updated_at
    data = sanitize_bundle(bundle.model_copy(deep=True)).model_dump_json(indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class EvidenceNotFound(LookupError):
    pass


class EvidenceStore:
    """Read-only access to bundles in two directories: recorded (versioned) and runs (local)."""

    MAX_BYTES = 2_000_000

    def __init__(self, recorded_dir: Path, runs_dir: Path):
        self.dirs: dict[str, Path] = {"recorded": Path(recorded_dir), "runs": Path(runs_dir)}

    def _path(self, collection: str, name: str) -> Path:
        base = self.dirs.get(collection)
        if base is None or not _NAME.match(name):
            raise EvidenceNotFound(name)
        path = (base / f"{name}.json").resolve()
        if path.parent != base.resolve() or not path.is_file():
            raise EvidenceNotFound(name)
        return path

    def load(self, collection: str, name: str) -> EvidenceBundle:
        path = self._path(collection, name)
        if path.stat().st_size > self.MAX_BYTES:
            raise ValueError("Evidence file too large")
        return sanitize_bundle(EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8")))

    def entries(self, now: datetime | None = None) -> list[dict]:
        out = []
        for collection, base in self.dirs.items():
            if not base.is_dir():
                continue
            for path in base.glob("*.json"):
                name = path.stem
                if not _NAME.match(name):
                    continue
                entry: dict[str, Any] = {"collection": collection, "name": name, "valid": True}
                try:
                    bundle = self.load(collection, name)
                except (ValidationError, ValueError, OSError):
                    entry.update(valid=False, title=name, error="Not a GhostFlow demo evidence bundle")
                    entry["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
                else:
                    entry.update(title=bundle.title, updated_at=bundle.updated_at.isoformat(), **present(bundle, collection, now))
                out.append(entry)
        return sorted(out, key=lambda e: e["updated_at"], reverse=True)


# -- CLI ----------------------------------------------------------------------------------


def _example_workflow() -> tuple[Workflow, str]:
    from ghostflow.config import Settings
    from ghostflow.demo_healing import compile_example

    return compile_example(Settings()), "Compiled from docs/examples/demo-recording.json (deterministic, no AI)"


def _cmd_import(args) -> int:
    legacy = json.loads(Path(args.legacy).read_text(encoding="utf-8"))
    workflow, source = _example_workflow()
    captured = (
        datetime.fromisoformat(args.captured_at)
        if args.captured_at
        else datetime.fromtimestamp(Path(args.legacy).stat().st_mtime, UTC)
    )
    note = args.note or (
        f"Imported from {Path(args.legacy).name}, the output of `make demo-healing"
        f"{'-meta' if 'meta' in Path(args.legacy).name else ''}` (GF-003). Captured {captured:%Y-%m-%d %H:%M} UTC "
        "(file time). Step-level timings and the candidate list were not stored by that version; "
        "step states other than the failed and recovered steps are derived."
    )
    bundle = import_gf003(legacy, workflow, source=source, captured_at=captured, note=note)
    write_bundle(Path(args.out), bundle)
    print(f"{args.out}: {len(bundle.scenarios)} scenarios, AI {bundle.ai.provider}, {bundle.ai.requests_sent} Meta requests")
    return 0


def _cmd_export_workflow(args) -> int:
    from sqlalchemy import select

    from ghostflow.config import Settings
    from ghostflow.storage import WorkflowRow, make_engine, make_session_factory

    settings = Settings()
    with make_session_factory(make_engine(settings.database_url))() as session:
        row = session.scalars(select(WorkflowRow).where(WorkflowRow.id.startswith(args.id))).first()
        if row is None:
            print(f"No workflow with id {args.id}", file=sys.stderr)
            return 1
        workflow = Workflow.model_validate(row.definition)
    # Tracking parameters the recording browser added (utm_*) are noise, not workflow intent.
    steps = []
    for step in workflow.steps:
        if isinstance(step, NavigateStep):
            parts = urlsplit(step.url)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
            step = step.model_copy(update={"url": urlunsplit(parts._replace(query=urlencode(query)))})
        steps.append(step)
    workflow = workflow.model_copy(update={"steps": steps})
    if not args.keep_defaults:
        # Recorded values are personal demo data; the runner supplies its own inputs.
        workflow = workflow.model_copy(
            update={"inputs": [i.model_copy(update={"default": None}) for i in workflow.inputs]}
        )
    Path(args.out).write_text(workflow.model_dump_json(indent=2) + "\n", encoding="utf-8")
    interp = workflow.interpretation
    print(f"{args.out}: '{workflow.name}', {len(workflow.steps)} steps, interpretation: {interp.provider if interp else 'none'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ghostflow.evidence", description="Demo evidence utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import-gf003", help="Convert GF-003 demo_healing output into a console bundle")
    p.add_argument("legacy")
    p.add_argument("--out", required=True)
    p.add_argument("--captured-at", help="ISO timestamp of the original run (default: file time)")
    p.add_argument("--note")
    p.set_defaults(func=_cmd_import)
    p = sub.add_parser("export-workflow", help="Write a stored workflow (with its interpretation) as JSON")
    p.add_argument("--id", required=True, help="Workflow id or id prefix")
    p.add_argument("--out", required=True)
    p.add_argument("--keep-defaults", action="store_true", help="Keep recorded input values (off by default)")
    p.set_defaults(func=_cmd_export_workflow)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

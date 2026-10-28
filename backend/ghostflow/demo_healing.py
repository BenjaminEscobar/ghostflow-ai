"""GF-003/GF-004 demonstration: the same workflow on the original portal and on a redesigned one.

    python -m ghostflow.demo_healing                     # A, B and C with the offline mock
    python -m ghostflow.demo_healing --provider meta     # C asks Meta Muse Spark (1 request max,
                                                         # after an explicit confirmation)

A. original portal, self-healing off         → passes with the recorded locators
B. "crear-requerimiento" variant, healing off → fails: every locator of "Nueva solicitud" finds nothing
C. same variant, healing on                   → the provider proposes a candidate, GhostFlow validates it,
                                                the run completes

The workflow is compiled once from docs/examples/demo-recording.json (or read from --workflow-file)
and reused unchanged in the three runs (its SHA-256 is recorded before and after). Progress is
written to a demo evidence bundle (ghostflow/evidence.py) that the Demo Console at /console shows
while the runner works. The portal is served on one fixed origin
(default http://localhost:8080, the recording's origin), switching only the files behind it.
Meta is used only with an explicit `--provider meta`, with retries off and a hard cap of one request.

GF-006: each scenario also stores real screenshots of the Chromium page (execution/capture.py)
in `<evidence dir>/captures/<bundle name>/`, which the console shows next to the steps. Only the
local synthetic portal is captured, sensitive steps never are, and `--no-captures` turns it off.
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_DIR = REPO_ROOT / "demo-portal"
VARIANTS_DIR = PORTAL_DIR / "variants"
RECORDING_FILE = REPO_ROOT / "docs" / "examples" / "demo-recording.json"
DEFAULT_VARIANT = "crear-requerimiento"
CAPTURE_RUNS_KEPT = 5  # retention: capture folders of the most recent runs only


def captures_allowed(evidence_dir: Path, repo_root: Path = REPO_ROOT) -> bool:
    """Captures go to the git-ignored demo-evidence/ or outside the repository, never to a
    versioned path such as docs/evidence."""
    where = Path(evidence_dir).resolve()
    root = repo_root.resolve()
    return not where.is_relative_to(root) or where.is_relative_to(root / "demo-evidence")


def build_variant(variant: str, dest: Path, portal_dir: Path = PORTAL_DIR) -> Path:
    """Copy the portal into `dest` and overlay the files of `variants/<variant>/`."""
    overlay = portal_dir / "variants" / variant
    if not overlay.is_dir():
        raise FileNotFoundError(f"Unknown portal variant '{variant}'")
    shutil.copytree(portal_dir, dest, ignore=shutil.ignore_patterns("variants"), dirs_exist_ok=True)
    for item in overlay.iterdir():
        if item.is_file() and item.suffix in {".html", ".css", ".js"}:
            shutil.copy2(item, dest / item.name)
    return dest


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


class SwitchableStaticServer:
    """Static HTTP server whose document root can change while the origin stays the same."""

    def __init__(self, root: Path, host: str = "127.0.0.1", port: int = 0):
        self.root = Path(root)
        server = self

        class Handler(_QuietHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server.root), **kwargs)

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def switch(self, root: Path) -> None:
        self.root = Path(root)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def workflow_digest(workflow) -> str:
    from ghostflow.evidence import workflow_digest as digest

    return digest(workflow)


def compile_example(settings):
    """The GF-003 demo workflow: docs/examples/demo-recording.json compiled deterministically."""
    from ghostflow.recording import RecordingCreate, compile_recording, sanitize_recording
    from ghostflow.security import UrlPolicy

    recording = sanitize_recording(
        RecordingCreate.model_validate(json.loads(RECORDING_FILE.read_text(encoding="utf-8"))),
        UrlPolicy(settings.allowed_origin_list),
    )
    return compile_recording(recording)  # no AI call


DEMO_PASSWORD = "ghostflow-demo"  # the demo portal's synthetic credential, shown on its own login page


def _parse_inputs(pairs: list[str] | None) -> dict[str, str]:
    values = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--input expects NAME=VALUE, got {pair!r}")
        values[name.strip()] = value
    return values


def _confirm_meta(model: str, assume_yes: bool) -> bool:
    """Real Meta requests need an explicit yes: the flag, or typing 'meta' at a terminal."""
    print(
        f"\n  Scenario C will call the REAL Meta Model API ({model}).\n"
        "  At most 1 request (retries off, hard cap 1). Scenarios A and B never call Meta.\n",
        file=sys.stderr,
    )
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("  Not confirmed: pass --confirm-meta or run it in a terminal.", file=sys.stderr)
        return False
    return input("  Type 'meta' to confirm: ").strip().lower() == "meta"


def _summary(name: str, result) -> dict:
    failed = next((s for s in result.steps if s.status == "failed"), None)
    recovered = [s for s in result.steps if s.resolution == "ai_recovery"]
    fallbacks = [s.step_id for s in result.steps if s.resolution == "deterministic_fallback"]
    attempts = [s for s in result.steps if s.recovery is not None]
    return {
        "scenario": name,
        "status": result.status,
        "failed_step": failed.step_id if failed else None,
        "failed_error": failed.error if failed else None,
        "deterministic_fallback_steps": fallbacks,
        "ai_recovered_steps": [s.step_id for s in recovered],
        "recovery_attempts": [{"step_id": s.step_id, **s.recovery.model_dump(mode="json")} for s in attempts],
        "verifications": [v.model_dump(mode="json") for v in result.verifications],
        "blocked_requests": result.blocked_requests,
    }


def main(argv: list[str] | None = None) -> int:
    from ghostflow.config import Settings

    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["mock", "meta"], default="mock", help="Advisor for scenario C (default: mock)")
    parser.add_argument("--confirm-meta", action="store_true", help="Confirm the single real Meta request without a prompt")
    parser.add_argument("--variant", default=DEFAULT_VARIANT)
    parser.add_argument("--port", type=int, default=8080, help="Port of http://localhost:<port> (must match the recording)")
    parser.add_argument("--headed", action="store_true", help="Also show the Chromium window (diagnosis)")
    parser.add_argument("--delay-ms", type=int, default=settings.step_delay_ms, help="Pause between steps (for filming)")
    parser.add_argument("--hold-ms", type=int, default=settings.final_hold_ms, help="Keep the final page visible this long")
    parser.add_argument("--step-timeout-ms", type=int, default=4000)
    parser.add_argument("--only", choices=["A", "B", "C"], action="append", help="Run only these scenarios")
    parser.add_argument("--workflow-file", type=Path, help="Workflow JSON (see `python -m ghostflow.evidence export-workflow`)")
    parser.add_argument("--input", action="append", metavar="NAME=VALUE", help="Input value (new data); repeatable")
    parser.add_argument("--evidence-dir", type=Path, default=Path(settings.demo_evidence_dir), help="Where bundles go")
    parser.add_argument("--name", help="Bundle file name (default: <UTC time>-<provider>)")
    parser.add_argument("--out", type=Path, help="Also write the GF-003 style summary JSON here")
    parser.add_argument("--no-captures", action="store_true", help="Do not store browser screenshots")
    args = parser.parse_args(argv)
    if not (0 <= args.delay_ms <= 5000 and 0 <= args.hold_ms <= 30000):
        parser.error("--delay-ms must be 0..5000 and --hold-ms 0..30000")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from ghostflow.ai import MetaModelProvider, MockModelProvider
    from ghostflow.ai.recovery import ModelRecoveryAdvisor
    from ghostflow.dsl.models import Workflow
    from ghostflow.evidence import AiSource, AiUsage, EvidenceBundle, apply_event, finish_scenario, new_scenario
    from ghostflow.evidence import outline_workflow, scenario_title, write_bundle
    from ghostflow.execution import resolve_inputs
    from ghostflow.execution.capture import CaptureBudget, CapturePolicy, CaptureRecorder, prune_captures
    from ghostflow.execution.playwright_web import PlaywrightWebAdapter
    from ghostflow.execution.recovery import Healer, RecoveryPolicy
    from ghostflow.security import UrlPolicy

    policy = UrlPolicy(settings.allowed_origin_list)
    if args.workflow_file:
        workflow = Workflow.model_validate_json(args.workflow_file.read_text(encoding="utf-8"))
        policy.restricted_to(workflow.allowed_origins)  # same origin checks as the API
        source = f"Workflow file {args.workflow_file.name}"
    else:
        workflow = compile_example(settings)
        source = "Compiled from docs/examples/demo-recording.json (deterministic, no AI)"
    declared = {i.name for i in workflow.inputs}
    provided = _parse_inputs(args.input)
    ignored = sorted(set(provided) - declared)
    if ignored:
        print(f"Ignoring inputs this workflow does not declare: {ignored}", file=sys.stderr)
    values = {k: v for k, v in provided.items() if k in declared}
    for i in workflow.inputs:
        if i.sensitive and i.name not in values:
            values[i.name] = DEMO_PASSWORD
    resolve_inputs(workflow, values)  # fail early on missing inputs, before any browser or AI work

    provider = None
    if args.provider == "meta":
        key = settings.meta_api_key.get_secret_value().strip() if settings.meta_api_key else ""
        if not key:
            print("--provider meta requiere GHOSTFLOW_META_API_KEY (backend/.env o variable exportada).", file=sys.stderr)
            return 2
        if "C" not in set(args.only or ["A", "B", "C"]):
            print("Scenario C is not selected: Meta will not be called.", file=sys.stderr)
        elif not _confirm_meta(settings.meta_model, args.confirm_meta):
            return 3
        provider = MetaModelProvider(
            api_key=settings.meta_api_key,
            base_url=settings.meta_api_base_url,
            model=settings.meta_model,
            timeout_s=settings.meta_timeout_s,
            max_output_tokens=settings.meta_max_output_tokens,
            reasoning_effort=settings.meta_reasoning_effort,
            max_retries=0,
            max_requests=1,  # hard cap for this demo, whatever happens
            allow_contributor_mode=settings.meta_allow_contributor_mode,
        )
    else:
        provider = MockModelProvider()
        print("Scenario C uses the offline mock: SIMULATED recovery, no Meta request.", file=sys.stderr)

    def adapter(healing: bool) -> PlaywrightWebAdapter:
        recovery_policy = RecoveryPolicy(
            enabled=healing,
            min_confidence=settings.healing_min_confidence,
            allow_side_effects=False,
            max_candidates=settings.healing_max_candidates,
            max_attempts_per_run=1,
        )
        healer = Healer(recovery_policy, policy, ModelRecoveryAdvisor(provider) if healing else None)
        return PlaywrightWebAdapter(
            policy,
            headless=not args.headed,
            step_timeout_ms=args.step_timeout_ms,
            step_delay_ms=args.delay_ms,
            final_hold_ms=args.hold_ms,
            healer=healer,
        )

    wanted = set(args.only or ["A", "B", "C"])
    started = datetime.now(UTC)
    name = args.name or f"{started:%Y%m%d-%H%M%S}-{args.provider}-{uuid.uuid4().hex[:8]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        parser.error("--name must be a plain bundle name (1..100 safe characters)")
    bundle_path = args.evidence_dir / f"{name}.json"
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(bundle_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        parser.error("Bundle name already exists; use a new --name")
    else:
        os.close(fd)  # reserve the name across processes before any writer can replace it
    is_meta = isinstance(provider, MetaModelProvider)
    bundle = EvidenceBundle(
        title=f"A/B/C self-healing demo · {'Meta Muse Spark' if is_meta else 'offline mock'}",
        created_at=started,
        updated_at=started,
        origin="demo_runner",
        provenance=f"python -m ghostflow.demo_healing --provider {args.provider} (delay {args.delay_ms} ms)",
        ai=AiSource(provider=args.provider, model=provider.model, request_cap=1 if is_meta else None),
        workflow=outline_workflow(workflow, source),
    )
    digest_before = bundle.workflow.sha256_before
    plan = [
        ("A", scenario_title("A", "original", False), "original", False),
        ("B", scenario_title("B", args.variant, False), args.variant, False),
        ("C", scenario_title("C", args.variant, True, args.provider), args.variant, True),
    ]
    plan = [p for p in plan if p[0] in wanted]
    for key, title, portal, healing in plan:
        bundle.scenarios.append(new_scenario(key, title, portal=portal, healing=healing, workflow=workflow, inputs=values))
    lock = threading.Lock()

    def save() -> None:
        with lock:
            if is_meta:
                bundle.ai.requests_sent = provider.requests_sent
                totals = provider.usage_totals
                bundle.ai.usage = AiUsage(**{k: totals.get(k, 0) for k in AiUsage.model_fields})
            write_bundle(bundle_path, bundle)

    capture_dir = args.evidence_dir / "captures" / name
    capturing = not args.no_captures and captures_allowed(args.evidence_dir)
    if not args.no_captures and not capturing:
        print(f"Browser captures off: {args.evidence_dir} is a versioned path (use demo-evidence/).", file=sys.stderr)
    capture_policy = CapturePolicy()
    capture_budget = CaptureBudget(capture_policy.max_bytes_per_run)
    if capturing:
        prune_captures(args.evidence_dir / "captures", CAPTURE_RUNS_KEPT - 1)
    save()
    print(f"Demo Console: http://127.0.0.1:8010/console/  (bundle {bundle_path})", file=sys.stderr)
    print(
        "Browser: Chromium window shown (diagnosis)" if args.headed
        else f"Browser: headless; follow the run in the Demo Console{' (real captures)' if capturing else ''}",
        file=sys.stderr,
    )
    summaries = []
    from ghostflow.execution.capture import capture_session

    session = capture_session(args.evidence_dir / "captures", name) if capturing else nullcontext(False)
    with session as capture_ready, tempfile.TemporaryDirectory(prefix="ghostflow-variant-") as tmp:
        capturing = capturing and capture_ready
        variant_dir = build_variant(args.variant, Path(tmp) / "portal")
        try:
            server = SwitchableStaticServer(PORTAL_DIR, port=args.port)
        except OSError as exc:
            bundle.state = "interrupted"
            save()
            print(f"No se pudo abrir el puerto {args.port} ({exc}). Detén `make portal` u otro servidor.", file=sys.stderr)
            return 2
        try:
            for (key, title, portal, healing), scenario in zip(plan, bundle.scenarios, strict=True):
                server.switch(PORTAL_DIR if portal == "original" else variant_dir)
                print(f"\n=== {title} ===", file=sys.stderr)
                scenario.state, scenario.started_at = "running", datetime.now(UTC)
                save()

                def observe(event: str, payload: dict, scenario=scenario) -> None:
                    with lock:
                        apply_event(scenario, event, payload)
                    save()

                recorder = (
                    CaptureRecorder(capture_dir, key, workflow, capture_policy, capture_budget) if capturing else None
                )
                result = adapter(healing).execute(workflow, values, observer=observe, capture=recorder)
                finish_scenario(scenario, result)
                save()
                summaries.append(_summary(title, result))
        finally:
            server.close()
            bundle.workflow.sha256_after = workflow_digest(workflow)
            bundle.state = "finished" if all(s.state == "finished" for s in bundle.scenarios) else "interrupted"
            save()
            if is_meta:
                provider.close()

    if capturing:
        prune_captures(args.evidence_dir / "captures", CAPTURE_RUNS_KEPT)

    if args.out:
        legacy = {
            "workflow": workflow.name,
            "workflow_sha256_before": digest_before,
            "scenarios": summaries,
            "workflow_sha256_after": bundle.workflow.sha256_after,
            "workflow_unchanged": bundle.workflow.sha256_after == digest_before,
        }
        if is_meta:
            legacy["meta_requests_sent"] = bundle.ai.requests_sent
            legacy["meta_usage"] = bundle.ai.usage.model_dump() if bundle.ai.usage else None
        args.out.write_text(json.dumps(legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for s in bundle.scenarios:
        print(f"{s.title}: {s.status}" + (f" (failed at {next(x.id for x in s.steps if x.state == 'failed')})" if s.status == "failed" else ""), file=sys.stderr)
    print(f"Meta requests sent: {bundle.ai.requests_sent}. DSL unchanged: {bundle.workflow.unchanged}. Bundle: {bundle_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

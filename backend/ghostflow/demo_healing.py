"""GF-003 demonstration: the same workflow on the original portal and on a redesigned one.

    python -m ghostflow.demo_healing                     # A, B and C with the offline mock
    python -m ghostflow.demo_healing --provider meta     # C asks Meta Muse Spark (1 request max)

A. original portal, self-healing off         → passes with the recorded locators
B. "crear-requerimiento" variant, healing off → fails: every locator of "Nueva solicitud" finds nothing
C. same variant, healing on                   → the provider proposes a candidate, GhostFlow validates it,
                                                the run completes

The workflow is compiled once from docs/examples/demo-recording.json and reused unchanged in the
three runs (its SHA-256 is printed before and after). The portal is served on one fixed origin
(default http://localhost:8080, the recording's origin), switching only the files behind it.
Meta is used only with an explicit `--provider meta`, with retries off and a hard cap of one request.
"""

import argparse
import functools
import hashlib
import json
import logging
import shutil
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_DIR = REPO_ROOT / "demo-portal"
VARIANTS_DIR = PORTAL_DIR / "variants"
RECORDING_FILE = REPO_ROOT / "docs" / "examples" / "demo-recording.json"
DEFAULT_VARIANT = "crear-requerimiento"


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
    return hashlib.sha256(workflow.model_dump_json().encode()).hexdigest()


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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["mock", "meta"], default="mock", help="Advisor for scenario C (default: mock)")
    parser.add_argument("--variant", default=DEFAULT_VARIANT)
    parser.add_argument("--port", type=int, default=8080, help="Port of http://localhost:<port> (must match the recording)")
    parser.add_argument("--headed", action="store_true", help="Show the browser")
    parser.add_argument("--delay-ms", type=int, default=0, help="Pause between steps (for filming)")
    parser.add_argument("--step-timeout-ms", type=int, default=4000)
    parser.add_argument("--only", choices=["A", "B", "C"], action="append", help="Run only these scenarios")
    parser.add_argument("--out", type=Path, help="Write the evidence as JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from ghostflow.ai import MetaModelProvider, MockModelProvider
    from ghostflow.ai.recovery import ModelRecoveryAdvisor
    from ghostflow.config import Settings
    from ghostflow.execution.playwright_web import PlaywrightWebAdapter
    from ghostflow.execution.recovery import Healer, RecoveryPolicy
    from ghostflow.recording import RecordingCreate, compile_recording, sanitize_recording
    from ghostflow.security import UrlPolicy

    settings = Settings()
    policy = UrlPolicy(settings.allowed_origin_list)
    recording = sanitize_recording(
        RecordingCreate.model_validate(json.loads(RECORDING_FILE.read_text(encoding="utf-8"))), policy
    )
    workflow = compile_recording(recording)  # deterministic; no AI call
    digest_before = workflow_digest(workflow)
    password = "ghostflow-demo"  # the demo portal's synthetic credential, shown on its own login page

    provider = None
    if args.provider == "meta":
        key = settings.meta_api_key.get_secret_value().strip() if settings.meta_api_key else ""
        if not key:
            print("--provider meta requiere GHOSTFLOW_META_API_KEY (backend/.env o variable exportada).", file=sys.stderr)
            return 2
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
        print(f"Escenario C usará Meta ({settings.meta_model}): como máximo 1 solicitud.", file=sys.stderr)
    else:
        provider = MockModelProvider()

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
            policy, headless=not args.headed, step_timeout_ms=args.step_timeout_ms, step_delay_ms=args.delay_ms, healer=healer
        )

    wanted = set(args.only or ["A", "B", "C"])
    evidence: dict = {"workflow": workflow.name, "workflow_sha256_before": digest_before, "scenarios": []}
    with tempfile.TemporaryDirectory(prefix="ghostflow-variant-") as tmp:
        variant_dir = build_variant(args.variant, Path(tmp) / "portal")
        try:
            server = SwitchableStaticServer(PORTAL_DIR, port=args.port)
        except OSError as exc:
            print(f"No se pudo abrir el puerto {args.port} ({exc}). Detén `make portal` u otro servidor.", file=sys.stderr)
            return 2
        try:
            plan = [
                ("A", "A · portal original, sin self-healing", PORTAL_DIR, False),
                ("B", f"B · variante {args.variant}, sin self-healing", variant_dir, False),
                ("C", f"C · variante {args.variant}, self-healing con {args.provider}", variant_dir, True),
            ]
            for key, name, root, healing in plan:
                if key not in wanted:
                    continue
                server.switch(root)
                print(f"\n=== {name} ===", file=sys.stderr)
                result = adapter(healing).execute(workflow, {"contrasena": password})
                evidence["scenarios"].append(_summary(name, result))
        finally:
            server.close()
            if isinstance(provider, MetaModelProvider):
                evidence["meta_requests_sent"] = provider.requests_sent
                evidence["meta_usage"] = provider.usage_totals
                provider.close()

    evidence["workflow_sha256_after"] = workflow_digest(workflow)
    evidence["workflow_unchanged"] = evidence["workflow_sha256_after"] == digest_before
    text = json.dumps(evidence, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")

    for s in evidence["scenarios"]:
        print(f"{s['scenario']}: {s['status']}" + (f" (falló {s['failed_step']})" if s["failed_step"] else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

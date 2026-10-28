"""GF-004 Demo Console: evidence bundles, real-vs-simulated labelling, sanitisation, the
read-only console API, the static page and the demo runner's Meta confirmation. Offline."""

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import REPO_ROOT
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from scenario import DEMO_PASSWORD, demo_recording
from test_healing import FakeMeta, FakeSource, chat, healer, meta, not_found, propose, step, variant_page

from ghostflow import demo_healing
from ghostflow.ai import MockModelProvider
from ghostflow.api import create_app
from ghostflow.config import Settings
from ghostflow.console import CONSOLE_CSP, STATIC_DIR
from ghostflow.dsl.models import Workflow
from ghostflow.evidence import (
    AiSource,
    EvidenceBundle,
    EvidenceStore,
    ai_badge,
    apply_event,
    finish_scenario,
    import_gf003,
    interpretation_badge,
    new_scenario,
    outline_workflow,
    presentation_mode,
    sanitize_bundle,
    workflow_digest,
    write_bundle,
)
from ghostflow.execution import playwright_web
from ghostflow.execution.base import RunResult, StepResult, VerificationResult
from ghostflow.execution.playwright_web import PlaywrightWebAdapter
from ghostflow.execution.recovery import RunState
from ghostflow.recording import RecordingCreate, compile_recording
from ghostflow.security import UrlPolicy
from ghostflow.storage import RunRow, WorkflowRow

BASE = "http://localhost:8080"
# Fictitious token shape (header {"alg":"HS256"}, payload {"sub":"demo"}), assembled at runtime.
JWT = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJkZW1vIn0", "c2lnbmF0dXJlLXZhbHVl"])
RECORDED_DIR = REPO_ROOT / "docs" / "evidence"
META_WORKFLOW = REPO_ROOT / "docs" / "examples" / "workflow-meta-interpreted.json"
NEW_DATA = {"contrasena": DEMO_PASSWORD, "nombre_del_cliente": "Lucía Méndez", "correo_del_cliente": "lucia@ejemplo.test"}


@pytest.fixture
def workflow() -> Workflow:
    return compile_recording(RecordingCreate.model_validate(demo_recording(BASE)))


def bundle_for(workflow, *, provider="mock", requests=0, state="running", updated=None) -> EvidenceBundle:
    now = updated or datetime.now(UTC)
    b = EvidenceBundle(
        title="demo", state=state, created_at=now, updated_at=now, origin="demo_runner",
        ai=AiSource(provider=provider, model="muse-spark-1.3" if provider == "meta" else "mock", requests_sent=requests),
        workflow=outline_workflow(workflow, "test"),
    )  # fmt: skip
    b.scenarios.append(new_scenario("C", "C", portal="crear-requerimiento", healing=True, workflow=workflow, inputs=NEW_DATA))
    return b


def ok(step_id, action="click", **kw) -> StepResult:
    return StepResult(step_id=step_id, action=action, status="passed", resolution="primary", **kw)


def recovered_attempt(workflow):
    attempt, _ = _healed(workflow)
    return attempt


def _healed(workflow):
    h = healer(meta(FakeMeta(chat(propose()))))
    source = FakeSource(variant_page())
    attempt = h.attempt(workflow=workflow, step=step(workflow, "s6"), failure=not_found(), source=source, state=RunState(), value=None)
    return attempt, source


# -- bundles --------------------------------------------------------------------------------


def test_outline_shows_parameters_but_never_values(workflow):
    view = outline_workflow(workflow, "test")
    assert len(view.steps) == len(workflow.steps) and view.steps[5].id == "s6"
    assert view.steps[5].label == 'link "Nueva solicitud"'
    assert {i.name: i.sensitive for i in view.inputs}["contrasena"] is True
    dumped = view.model_dump_json()
    for i in workflow.inputs:
        if i.default:
            assert i.default not in dumped  # recorded values are not part of the outline
    assert view.sha256_before == workflow_digest(workflow)


def test_scenario_inputs_show_new_data_and_mask_secrets(workflow):
    sc = new_scenario("A", "A", portal="original", healing=False, workflow=workflow, inputs=NEW_DATA)
    assert sc.inputs["contrasena"] == "***" and sc.inputs["nombre_del_cliente"] == "Lucía Méndez"
    assert DEMO_PASSWORD not in sc.model_dump_json()
    assert {s.state for s in sc.steps} == {"pending"}


def test_events_drive_pending_running_recovered_failed_skipped(workflow):
    b = bundle_for(workflow)
    sc = b.scenarios[0]
    apply_event(sc, "step_started", {"step_id": "s1", "index": 0})
    assert sc.steps[0].state == "running"
    apply_event(sc, "step_finished", ok("s1", "navigate").model_dump(mode="json"))
    assert sc.steps[0].state == "completed"

    apply_event(sc, "step_started", {"step_id": "s6", "index": 5})
    apply_event(sc, "recovery_started", {"step_id": "s6", "exhausted_strategies": ["x"]})
    assert (sc.steps[5].state, sc.steps[5].phase) == ("running", "recovering")
    attempt = recovered_attempt(workflow)
    healed = StepResult(step_id="s6", action="click", status="passed", resolution="ai_recovery", recovery=attempt)
    apply_event(sc, "step_finished", healed.model_dump(mode="json"))
    assert (sc.steps[5].state, sc.steps[5].phase, sc.steps[5].resolution) == ("recovered", None, "ai_recovery")

    apply_event(sc, "step_finished", StepResult(step_id="s8", action="fill", status="failed", error="boom").model_dump())
    apply_event(sc, "step_finished", StepResult(step_id="s9", action="fill", status="skipped").model_dump())
    assert (sc.steps[7].state, sc.steps[8].state) == ("failed", "skipped")
    apply_event(sc, "verification_finished", {"index": 0, "type": "text_visible", "status": "passed"})
    assert sc.verifications[0].status == "passed"
    apply_event(sc, "step_started", {"step_id": "nope"})  # unknown ids are ignored


def test_deterministic_fallback_is_completed_not_recovered(workflow):
    sc = bundle_for(workflow).scenarios[0]
    fallback = StepResult(step_id="s6", action="click", status="passed", resolution="deterministic_fallback", healed=True)
    apply_event(sc, "step_finished", fallback.model_dump(mode="json"))
    assert (sc.steps[5].state, sc.steps[5].resolution) == ("completed", "deterministic_fallback")


def test_final_result_is_authoritative(workflow):
    sc = bundle_for(workflow).scenarios[0]
    apply_event(sc, "step_started", {"step_id": "s1"})
    now = datetime.now(UTC)
    result = RunResult(
        adapter="web", status="failed", started_at=now, finished_at=now,
        steps=[ok("s1", "navigate"), StepResult(step_id="s2", action="fill", status="failed", error="x")],
        verifications=[VerificationResult(index=0, type="text_visible", status="skipped")],
    )  # fmt: skip
    finish_scenario(sc, result)
    assert (sc.state, sc.status, sc.steps[0].state, sc.steps[1].state) == ("finished", "failed", "completed", "failed")
    assert [v.status for v in sc.verifications] == ["skipped"]


def test_recovery_audit_lists_the_candidates_shown_to_the_model(workflow):
    attempt, _ = _healed(workflow)
    assert [c.name for c in attempt.candidates] == ["Crear requerimiento", "Consultar requerimientos", "Ayuda"]
    assert attempt.candidates_considered == 3 and attempt.proposed.id == attempt.candidates[0].id
    assert "http" not in attempt.model_dump_json()  # paths only, never absolute URLs


# -- real vs simulated -------------------------------------------------------------------------


def test_ai_badges_are_unambiguous():
    real = ai_badge(AiSource(provider="meta", model="muse-spark-1.3", requests_sent=1))
    assert real["kind"] == "real" and "Real Meta Model API" in real["text"] and "1 request" in real["text"]
    configured = ai_badge(AiSource(provider="meta", model="muse-spark-1.3", requests_sent=0))
    assert configured["kind"] == "none" and "no request" in configured["text"]
    mock = ai_badge(AiSource(provider="mock", model="mock-semantic-v1"))
    assert mock["kind"] == "simulated" and "Simulated" in mock["text"] and "Real" not in mock["text"]
    assert ai_badge(AiSource(provider="none"))["kind"] == "none"


def test_interpretation_badge():
    assert interpretation_badge(None)["kind"] == "none"
    assert interpretation_badge({"provider": "meta", "model": "muse-spark-1.3"})["kind"] == "real"
    assert interpretation_badge({"provider": "mock", "model": "mock-semantic-v1"})["kind"] == "simulated"


def test_saved_or_recorded_evidence_is_never_presented_as_live(workflow):
    now = datetime.now(UTC)
    running = bundle_for(workflow, updated=now)
    assert presentation_mode(running, "runs", now)["mode"] == "live"
    assert presentation_mode(running, "recorded", now)["mode"] == "recorded"  # whatever the file says
    assert presentation_mode(running, "runs", now + timedelta(minutes=2))["mode"] == "stale"
    finished = bundle_for(workflow, state="finished", updated=now)
    assert presentation_mode(finished, "runs", now)["mode"] == "saved"
    assert "not live" in presentation_mode(finished, "recorded", now)["text"]


# -- sanitisation ------------------------------------------------------------------------------


def test_sanitize_masks_secrets_whatever_wrote_the_file(workflow):
    b = bundle_for(workflow)
    sc = b.scenarios[0]
    sc.inputs["contrasena"] = DEMO_PASSWORD  # a tampered file
    sc.inputs["descripcion"] = JWT
    sc.error = f"failed with Authorization {JWT}"
    sc.steps[5].error = f"token {JWT}"
    attempt = recovered_attempt(workflow)
    attempt.justification = f"porque {JWT}"
    sc.steps[5].recovery = attempt
    text = sanitize_bundle(b).model_dump_json()
    assert JWT not in text and DEMO_PASSWORD not in text
    assert sc.inputs["contrasena"] == "***" and sc.inputs["descripcion"] == "***"
    assert "[secret]" in sc.error and "[secret]" in sc.steps[5].recovery.justification


def test_write_is_atomic_and_round_trips(tmp_path, workflow):
    b = bundle_for(workflow)
    path = tmp_path / "runs" / "x.json"
    write_bundle(path, b)
    write_bundle(path, b)
    assert [p.name for p in path.parent.iterdir()] == ["x.json"]
    loaded = EvidenceStore(tmp_path / "none", path.parent).load("runs", "x")
    assert loaded.scenarios[0].inputs["contrasena"] == "***"


def test_store_rejects_traversal_invalid_and_missing(tmp_path, workflow):
    runs = tmp_path / "runs"
    write_bundle(runs / "good.json", bundle_for(workflow, state="finished"))
    (runs / "broken.json").write_text("{not json", encoding="utf-8")
    (runs / "other.json").write_text('{"format": "something-else"}', encoding="utf-8")
    store = EvidenceStore(tmp_path / "missing-dir", runs)
    entries = {e["name"]: e for e in store.entries()}
    assert entries["good"]["valid"] and entries["good"]["presentation"]["mode"] == "saved"
    assert not entries["broken"]["valid"] and not entries["other"]["valid"]
    for collection, name in [("runs", "../runs/good"), ("runs", ".hidden"), ("etc", "good"), ("runs", "nope"), ("recorded", "good")]:
        with pytest.raises(LookupError):
            store.load(collection, name)


# -- versioned recorded evidence -------------------------------------------------------------


def test_recorded_meta_evidence_is_valid_real_and_matches_the_example_workflow():
    store = EvidenceStore(RECORDED_DIR, RECORDED_DIR / "none")
    b = store.load("recorded", "gf003-meta-recovery")
    assert b.origin == "gf003_import" and "Imported from" in (b.provenance or "")
    assert ai_badge(b.ai)["kind"] == "real" and b.ai.requests_sent == 1 and b.ai.request_cap == 1
    assert b.workflow.sha256_before == workflow_digest(demo_healing.compile_example(Settings()))
    assert b.workflow.unchanged is True
    a, bb, c = b.scenarios
    assert (a.status, bb.status, c.status) == ("passed", "failed", "passed")
    s6_b, s6_c = bb.steps[5], c.steps[5]
    assert s6_b.state == "failed" and s6_b.recovery.outcome == "skipped" and not s6_b.recovery.consulted_model
    assert s6_c.state == "recovered" and s6_c.recovery.proposed.name == "Crear requerimiento"
    assert s6_c.recovery.provider == "meta" and all(v.passed for v in s6_c.recovery.validation)
    assert all(s.derived_steps for s in b.scenarios)


def test_versioned_demo_files_hold_no_secrets_or_personal_defaults():
    for path in [*RECORDED_DIR.glob("*.json"), META_WORKFLOW]:
        text = path.read_text(encoding="utf-8")
        assert DEMO_PASSWORD not in text and "utm_" not in text
        assert not re.search(r"eyJ[\w-]{10,}\.|Bearer\s+\w|sk-[A-Za-z0-9]{10,}|api[_-]?key\"\s*:", text, re.I), path
    workflow = Workflow.model_validate_json(META_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow.interpretation is not None and workflow.interpretation.provider == "meta"
    assert all(i.default is None for i in workflow.inputs)  # personal demo values were not exported
    UrlPolicy(Settings().allowed_origin_list).restricted_to(workflow.allowed_origins)


def test_import_requires_the_exact_workflow(workflow):
    legacy = {"workflow_sha256_before": "0" * 64, "scenarios": []}
    with pytest.raises(ValueError, match="SHA-256"):
        import_gf003(legacy, workflow, source="x", captured_at=datetime.now(UTC), note="n")


# -- console API -----------------------------------------------------------------------------


def make_client(tmp_path, **settings) -> TestClient:
    s = Settings(
        database_url=f"sqlite:///{tmp_path / 'c.db'}", demo_recorded_dir=str(RECORDED_DIR),
        demo_evidence_dir=str(tmp_path / "runs"), **settings,
    )  # fmt: skip
    return TestClient(create_app(s, provider=MockModelProvider()), base_url="http://127.0.0.1:8010")


def test_sources_list_recorded_runs_and_workflows(tmp_path, workflow):
    with make_client(tmp_path) as c:
        assert c.get("/api/demo/sources").json()["sources"][0]["collection"] == "recorded"
        write_bundle(tmp_path / "runs" / "live.json", bundle_for(workflow))
        rec = c.post("/api/recordings", json=demo_recording(BASE)).json()
        c.post(f"/api/recordings/{rec['id']}/workflow", json={"annotate": False})
        data = c.get("/api/demo/sources").json()
    by = {(s["collection"], s["presentation"]["mode"]) for s in data["sources"]}
    assert {("recorded", "recorded"), ("runs", "live"), ("workflows", "stored")} <= by
    assert data["backend"] == {"ai_provider": "mock", "healing_enabled": False, "step_delay_ms": 0}


def test_source_detail_and_errors(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/api/demo/sources/recorded/gf003-meta-recovery")
        assert r.status_code == 200
        body = r.json()
        assert body["presentation"]["mode"] == "recorded" and body["ai_badge"]["kind"] == "real"
        assert body["workflow_unchanged"] is True and body["bundle"]["scenarios"][2]["steps"][5]["state"] == "recovered"
        for url in ["/api/demo/sources/runs/missing", "/api/demo/sources/x/y", "/api/demo/sources/workflows/nope",
                    "/api/demo/sources/recorded/..%2F..%2Fbackend%2Fpyproject"]:  # fmt: skip
            assert c.get(url).status_code == 404, url
        (tmp_path / "runs").mkdir()
        (tmp_path / "runs" / "bad.json").write_text("[]", encoding="utf-8")
        assert c.get("/api/demo/sources/runs/bad").status_code == 422


def test_backend_workflow_runs_become_a_read_only_view(tmp_path, workflow):
    with make_client(tmp_path) as c:
        factory = c.app.state.session_factory
        attempt = recovered_attempt(workflow)
        now = datetime.now(UTC)
        steps = [ok(s.id, s.action) for s in workflow.steps]
        steps[5] = StepResult(step_id="s6", action="click", status="passed", resolution="ai_recovery", recovery=attempt)
        result = RunResult(adapter="web", status="passed", started_at=now, finished_at=now, steps=steps)
        with factory() as session:
            row = WorkflowRow(name=workflow.name, definition=workflow.model_dump(mode="json"))
            session.add(row)
            session.commit()
            session.add(RunRow(workflow_id=row.id, status="passed", inputs={"contrasena": "***"}, result=result.model_dump(mode="json")))
            session.commit()
            wid = row.id
        before = c.get(f"/api/workflows/{wid}").json()
        body = c.get(f"/api/demo/sources/workflows/{wid}").json()
        assert c.get(f"/api/workflows/{wid}").json() == before
        assert len(c.get(f"/api/workflows/{wid}/runs").json()) == 1  # nothing was executed
    b = body["bundle"]
    assert body["presentation"]["mode"] == "stored" and b["origin"] == "backend_runs"
    assert b["ai"]["provider"] == "meta" and body["ai_badge"]["kind"] == "real"
    assert b["scenarios"][0]["steps"][5]["state"] == "recovered"


def test_console_page_is_served_with_a_strict_policy(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/console", follow_redirects=False).headers["location"] == "/console/"
        page = c.get("/console/")
        assert page.status_code == 200 and "GhostFlow Demo Console" in page.text
        assert page.headers["content-security-policy"] == CONSOLE_CSP
        assert page.headers["x-content-type-options"] == "nosniff"
        assert c.get("/console/console.js").status_code == 200
        assert c.get("/console/../pyproject.toml").status_code == 404
        assert set(c.get("/api/health").json()) == {"status", "version", "ai_provider", "ai_model"}  # unchanged contract


def test_console_frontend_cannot_turn_evidence_into_markup_or_reach_out():
    js = (STATIC_DIR / "console.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "console.css").read_text(encoding="utf-8")
    for banned in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage"]:
        assert banned not in js, banned
    assert re.findall(r"fetch\(([^,)]+)", js) == ["path"]
    calls = re.findall(r"getJSON\(`([^`]*)`[,)]", js)
    assert len(calls) == 2 and all(c.startswith("${API}/") for c in calls) and 'const API = "/api/demo";' in js
    for text in (js, html, css):
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", text)  # no external resources
    assert "method" not in js  # GET only
    assert "Content-Security-Policy" in html and "script-src 'self'" in html


# -- adapter observer and visual mode -----------------------------------------------------------


def test_visual_mode_is_off_by_default_and_bounded():
    s = Settings()
    assert (s.step_delay_ms, s.final_hold_ms, s.healing_enabled) == (0, 0, False)
    with pytest.raises(ValidationError):
        Settings(final_hold_ms=60_000)
    with pytest.raises(ValidationError):
        Settings(step_delay_ms=-1)


def test_observer_sees_recovery_start(monkeypatch, workflow):
    events = []
    adapter = PlaywrightWebAdapter(UrlPolicy([BASE]), healer=healer(meta(FakeMeta(chat(propose())))))

    def missing(page, target):
        raise not_found()

    monkeypatch.setattr(adapter, "_resolve", missing)
    monkeypatch.setattr(playwright_web, "PlaywrightCandidateSource", lambda page, timeout_ms: FakeSource(variant_page()))

    values, policy = {"contrasena": DEMO_PASSWORD}, UrlPolicy([BASE])
    notify = lambda event, payload: events.append((event, payload))  # noqa: E731
    result = adapter._run_step(None, workflow, step(workflow, "s6"), values, policy, [DEMO_PASSWORD], RunState(), notify)
    assert result.status == "passed" and result.resolution == "ai_recovery"
    assert events == [("recovery_started", {"step_id": "s6", "exhausted_strategies": not_found().tried})]


def test_no_recovery_event_when_healing_is_off(monkeypatch, workflow):
    events = []
    adapter = PlaywrightWebAdapter(UrlPolicy([BASE]))

    def missing(page, target):
        raise not_found()

    monkeypatch.setattr(adapter, "_resolve", missing)
    monkeypatch.setattr(playwright_web, "PlaywrightCandidateSource", lambda page, timeout_ms: FakeSource(variant_page()))
    result = adapter._run_step(None, workflow, step(workflow, "s6"), {}, UrlPolicy([BASE]), [], RunState(), lambda e, p: events.append(e))
    assert result.status == "failed" and events == []


# -- demo runner: Meta only with explicit confirmation -------------------------------------------


def test_runner_refuses_meta_without_confirmation(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GHOSTFLOW_META_API_KEY", "test-meta-key-not-real")
    created = []
    monkeypatch.setattr("ghostflow.ai.MetaModelProvider", lambda **kw: created.append(kw))
    code = demo_healing.main(["--provider", "meta", "--evidence-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 3 and created == [] and list(tmp_path.iterdir()) == []
    assert "REAL Meta Model API" in err and "At most 1 request" in err


def test_runner_without_key_never_builds_a_meta_provider(tmp_path, capsys):
    assert demo_healing.main(["--provider", "meta", "--evidence-dir", str(tmp_path)]) == 2
    assert "GHOSTFLOW_META_API_KEY" in capsys.readouterr().err


def test_runner_rejects_malformed_and_missing_inputs(tmp_path):
    with pytest.raises(SystemExit):
        demo_healing.main(["--input", "no-equals-sign", "--evidence-dir", str(tmp_path)])
    from ghostflow.execution import InputError

    with pytest.raises(InputError):  # exported workflow has no defaults: new data is required
        demo_healing.main(["--workflow-file", str(META_WORKFLOW), "--evidence-dir", str(tmp_path)])


def test_confirmation_prompt(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "meta")
    assert demo_healing._confirm_meta("muse-spark-1.3", False)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert not demo_healing._confirm_meta("muse-spark-1.3", False)
    assert demo_healing._confirm_meta("muse-spark-1.3", True)


def test_key_is_never_part_of_a_bundle(tmp_path, workflow):
    key = "meta-key-should-never-appear-0123456789"
    settings = Settings(meta_api_key=SecretStr(key))
    b = bundle_for(workflow, provider="meta", requests=1)
    b.provenance = f"settings {settings!r}"
    write_bundle(tmp_path / "k.json", b)
    assert key not in (tmp_path / "k.json").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))["ai"]["provider"] == "meta"


# -- GF-005: design system and accessibility of the console --------------------------------------


def _css_vars(css: str) -> dict[str, str]:
    root = css[css.index(":root {") : css.index("}", css.index(":root {"))]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root))


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_palette_is_light_blue_led_and_text_meets_wcag_aa():
    css = (STATIC_DIR / "console.css").read_text(encoding="utf-8")
    v = _css_vars(css)
    assert v["blue"].lower() == "#1877f2" and _luminance(v["bg"]) > 0.9 and _luminance(v["surface"]) == 1.0
    pairs = [
        ("text", "surface"), ("muted", "surface"), ("muted", "surface-2"), ("blue-ink", "surface"), ("blue-ink", "blue-soft"),
        ("violet-ink", "surface"), ("violet-ink", "violet-soft"), ("ok-ink", "ok-soft"), ("fail-ink", "fail-soft"),
        ("warn-ink", "warn-soft"), ("text", "bg"),
    ]
    for fg, bg in pairs:
        assert _contrast(v[fg], v[bg]) >= 4.5, (fg, bg, round(_contrast(v[fg], v[bg]), 2))
    assert _contrast("#ffffff", v["blue"]) >= 4.0  # white on the blue buttons and "Real" badge (bold/large)
    assert "prefers-reduced-motion" in css and ":focus-visible" in css
    assert "@media (max-width: 1080px)" in css and "@media (max-width: 760px)" in css  # tablet, narrow


def test_views_follow_the_tabs_pattern_and_controls_are_labelled():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "console.js").read_text(encoding="utf-8")
    assert '<html lang="en">' in html and 'class="skip" href="#app"' in html
    assert 'role="tablist" aria-label="View"' in html
    assert 'id="tab-demo" aria-controls="demo-view" aria-selected="true"' in html
    assert 'id="tab-dev" aria-controls="dev-view" aria-selected="false"' in html
    assert 'id="dev-view" role="tabpanel" aria-labelledby="tab-dev" hidden' in html
    assert '<label class="sr-only" for="source-select">' in html
    assert all('type="button"' in tag for tag in re.findall(r"<button[^>]*>", html))
    calls = re.findall(r'el\("button", \{\s*([^}]*)', js)
    assert calls and all(c.startswith('type: "button"') for c in calls)
    assert "ArrowRight" in js and "aria-current" in js


def test_mascot_is_an_original_inline_svg_with_an_accessible_name():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "console.js").read_text(encoding="utf-8")
    svg = html[html.index('<svg class="mascot"') : html.index("</svg>", html.index('<svg class="mascot"'))]
    assert 'role="img" aria-labelledby="mascot-title"' in svg and '<title id="mascot-title">' in svg
    for mood in ["eyes-open", "eyes-happy", "mouth-smile", "mouth-o", "mouth-worried", "sparkles"]:
        assert f'class="{mood}"' in svg
    assert "<img" not in html and "<image" not in svg  # no bitmap or third-party artwork
    for brand in ["facebook", "meta-logo", "instagram", "whatsapp", "infinity"]:
        assert brand not in html.lower() and brand not in js.lower()
    moods = set(re.findall(r'return "(idle|working|healing|alert|happy|proud)"', js)) | {"proud", "happy"}
    css = (STATIC_DIR / "console.css").read_text(encoding="utf-8")
    for mood in moods - {"idle"}:
        assert f'data-mood="{mood}"' in css, mood


def test_demo_view_never_presents_reconstruction_as_live_or_as_a_screenshot():
    js = (STATIC_DIR / "console.js").read_text(encoding="utf-8")
    assert "Reconstructed from saved evidence · not a screenshot" in js
    assert "Live from the demo runner · Chromium runs headless by default" in js
    assert "REPLAY of saved evidence · not a live execution" in js
    assert "when the workflow was compiled (GF-002)" in js and "separate execution with its own provenance" in js
    assert re.search(r"Math\.random|Date\.now\(\)", js) is None  # no invented timings or values
    # Current backend settings are never confused with the recorded results of the selected evidence.
    assert "Current backend settings" in js and "Recorded recovery · selected evidence" in js
    assert "independent of the interpretation (GF-002)" in js
    # Presentation copy is English; Spanish on screen only ever comes from evidence data.
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"[áéíóúñÁÉÍÓÚÑ¿¡]", js + html)


def test_step_locations_come_from_the_workflow(workflow):
    view = outline_workflow(workflow, "test")
    locations = {s.id: s.location for s in view.steps if s.location}
    assert locations["s1"] == "http://localhost:8080/index.html" and locations["s7"] == "/new-request.html"
    assert all(s.location is None for s in view.steps if s.action in {"click", "fill", "select", "check"})

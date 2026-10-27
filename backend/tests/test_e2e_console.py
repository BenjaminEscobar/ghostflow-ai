"""GF-004 in Chromium: a real run streams into a demo evidence bundle (visual mode on), and the
Demo Console renders recorded, saved and live evidence with unambiguous labels."""

import socket
import threading
import time
from datetime import UTC, datetime

import pytest
import uvicorn
from conftest import REPO_ROOT
from playwright.sync_api import expect, sync_playwright
from scenario import DEMO_PASSWORD, demo_recording

from ghostflow.ai import MockModelProvider
from ghostflow.ai.recovery import ModelRecoveryAdvisor
from ghostflow.api import create_app
from ghostflow.config import Settings
from ghostflow.demo_healing import SwitchableStaticServer, build_variant
from ghostflow.evidence import AiSource, EvidenceBundle, apply_event, finish_scenario, new_scenario
from ghostflow.evidence import outline_workflow, write_bundle
from ghostflow.execution.playwright_web import PlaywrightWebAdapter
from ghostflow.execution.recovery import Healer, RecoveryPolicy
from ghostflow.recording import RecordingCreate, compile_recording
from ghostflow.security import UrlPolicy

pytestmark = pytest.mark.e2e


@pytest.fixture
def variant_portal(tmp_path):
    variant = build_variant("crear-requerimiento", tmp_path / "variant")
    server = SwitchableStaticServer(variant)
    yield f"http://127.0.0.1:{server.port}"
    server.close()


def _adapter(origin, **kw) -> PlaywrightWebAdapter:
    policy = UrlPolicy([origin])
    healer = Healer(RecoveryPolicy(enabled=True, max_attempts_per_run=1), policy, ModelRecoveryAdvisor(MockModelProvider()))
    return PlaywrightWebAdapter(policy, step_timeout_ms=3000, healer=healer, **kw)


def test_run_streams_into_a_bundle_with_visual_mode(tmp_path, variant_portal):
    workflow = compile_recording(RecordingCreate.model_validate(demo_recording(variant_portal)))
    now = datetime.now(UTC)
    bundle = EvidenceBundle(
        title="e2e", created_at=now, updated_at=now, origin="demo_runner",
        ai=AiSource(provider="mock", model="mock"), workflow=outline_workflow(workflow, "test"),
    )  # fmt: skip
    scenario = new_scenario("C", "C", portal="crear-requerimiento", healing=True, workflow=workflow, inputs={"contrasena": DEMO_PASSWORD})
    bundle.scenarios.append(scenario)
    path = tmp_path / "runs" / "live.json"
    events, snapshots = [], []

    def observe(event, payload):
        events.append((event, payload.get("step_id")))
        apply_event(scenario, event, payload)
        write_bundle(path, bundle)
        snapshots.append([s.state for s in scenario.steps])

    delay, hold = 120, 400
    t0 = time.monotonic()
    result = _adapter(variant_portal, step_delay_ms=delay, final_hold_ms=hold).execute(
        workflow, {"contrasena": DEMO_PASSWORD}, observer=observe
    )
    elapsed_ms = (time.monotonic() - t0) * 1000
    finish_scenario(scenario, result)

    assert result.status == "passed", result
    assert elapsed_ms >= delay * (len(workflow.steps) - 1) + hold
    started = [sid for e, sid in events if e == "step_started"]
    assert started == [s.id for s in workflow.steps]  # each step once: no duplicated actions
    assert [e for e, sid in events if sid == "s6"] == ["step_started", "recovery_started", "step_finished"]
    assert events[-1][0] == "holding"
    assert any("running" in snap for snap in snapshots)
    s6 = scenario.steps[5]
    assert s6.state == "recovered" and s6.recovery.outcome == "recovered" and s6.recovery.provider == "mock"
    assert [c.name for c in s6.recovery.candidates][:2] == ["Crear requerimiento", "Consultar requerimientos"]
    assert DEMO_PASSWORD not in path.read_text(encoding="utf-8")


def test_a_failing_observer_never_changes_the_run(variant_portal):
    workflow = compile_recording(RecordingCreate.model_validate(demo_recording(variant_portal)))

    def broken(event, payload):
        raise RuntimeError("observer bug")

    result = _adapter(variant_portal).execute(workflow, {"contrasena": DEMO_PASSWORD}, observer=broken)
    assert result.status == "passed"
    assert [s.status for s in result.steps].count("passed") == len(workflow.steps)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def console_url(tmp_path):
    port = _free_port()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'console.db'}",
        demo_recorded_dir=str(REPO_ROOT / "docs" / "evidence"),
        demo_evidence_dir=str(tmp_path / "runs"),
    )
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "backend did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_console_shows_recorded_and_live_evidence(tmp_path, console_url):
    workflow = compile_recording(RecordingCreate.model_validate(demo_recording("http://localhost:8080")))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            problems = []
            page.on("pageerror", lambda exc: problems.append(str(exc)))
            page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" else None)
            # Anything but the console's own origin would be a leak (and is refused by its CSP).
            page.on("request", lambda req: problems.append(req.url) if not req.url.startswith((console_url, "data:")) else None)

            page.goto(f"{console_url}/console/#recorded%2Fgf003-meta-recovery")
            badges = page.locator("#badges")
            expect(badges).to_contain_text("Recorded evidence")
            expect(badges).to_contain_text("Real Meta Model API")
            expect(page.locator(".scenario.recovered")).to_contain_text("Recovered s6")
            expect(page.locator("#healing")).to_contain_text("Crear requerimiento")
            expect(page.locator("#healing")).to_contain_text("Re-checked live right before acting")
            expect(page.locator("#evidence")).to_contain_text("same SHA-256")
            expect(page.locator("#intel")).to_contain_text("No AI interpretation")

            page.locator(".scenario", has_text="SCENARIO B").click()
            expect(page.locator(".scenario[aria-selected=true]")).to_contain_text("Failed at s6")
            expect(page.locator(".step.failed")).to_contain_text('link "Nueva solicitud"')
            expect(page.locator("#healing-outcome")).to_contain_text("Not attempted")

            page.locator(".scenario", has_text="SCENARIO C").click()
            page.locator("#replay").click()
            expect(page.locator("#replay-banner")).to_contain_text("not a live execution")
            expect(page.locator("#badges")).not_to_contain_text("Live ·")
            page.locator("#replay").click()  # stop
            expect(page.locator("#replay-banner")).to_be_hidden()

            # A running bundle appears: the console follows it and labels the AI as simulated.
            now = datetime.now(UTC)
            live = EvidenceBundle(
                title="Live rehearsal", created_at=now, updated_at=now, origin="demo_runner",
                ai=AiSource(provider="mock", model="mock"), workflow=outline_workflow(workflow, "test"),
            )  # fmt: skip
            sc = new_scenario("C", "C · live", portal="crear-requerimiento", healing=True, workflow=workflow, inputs={})
            sc.state = "running"
            sc.steps[0].state = "running"
            live.scenarios.append(sc)
            write_bundle(tmp_path / "runs" / "rehearsal.json", live)
            expect(page.locator("#badges")).to_contain_text("Live", timeout=8000)
            expect(page.locator("#badges")).to_contain_text("Simulated AI")
            expect(page.locator(".step.running")).to_have_count(1)
            assert problems == []
        finally:
            browser.close()



# Visible text blocks rendered narrower than a few characters, i.e. text wrapping into a vertical column.
SQUEEZED_TEXT_JS = """[...document.querySelectorAll('#app *')]
  .filter((e) => e instanceof HTMLElement && e.offsetParent && !e.closest('.sr-only') && e.children.length === 0)
  .filter((e) => e.textContent.trim().length > 12 && e.getBoundingClientRect().width < 48)
  .map((e) => e.id || e.className || e.tagName)"""


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 1366), (820, 1180)])
def test_demo_and_developer_views_are_usable_on_desktop_and_tablet(console_url, width, height):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
            problems = []
            page.on("pageerror", lambda exc: problems.append(str(exc)))
            page.goto(f"{console_url}/console/#recorded%2Fgf003-meta-recovery")

            # Demo view first: one story, real data, honest labels.
            expect(page.locator("#demo-view")).to_be_visible()
            expect(page.locator("#dev-view")).to_be_hidden()
            expect(page.locator("#capabilities")).to_contain_text("Recovered s6 in scenario C")
            expect(page.locator("#stage-caption")).to_contain_text("not a screenshot")
            expect(page.locator("#address")).to_have_text("http://localhost:8080/dashboard.html")
            expect(page.locator("#action")).to_contain_text("Crear requerimiento")
            expect(page.locator("#heal-summary")).to_contain_text("10/10 deterministic checks passed")
            expect(page.locator("#heal-summary")).to_contain_text("Real · Meta muse-spark-1.3")
            expect(page.locator("#understand")).to_contain_text("No AI interpretation stored")
            expect(page.locator("#understand")).to_contain_text("independent of the interpretation (GF-002)")
            expect(page.locator("#system-status")).to_contain_text("Current backend settings")
            expect(page.locator("#heal-summary")).to_contain_text("Recorded recovery")
            expect(page.locator("#mascot")).to_have_attribute("data-mood", "proud")
            expect(page.locator("#page")).to_contain_text("Crear requerimiento")
            expect(page.locator("#events")).to_contain_text("Recorded element not found")

            # Layout: nothing squeezed into a sliver (no vertical text) and the mascot stays prominent.
            assert page.evaluate(SQUEEZED_TEXT_JS) == []
            assert page.locator("#mascot").bounding_box()["width"] >= 140

            # Keyboard: arrows move between scenario tabs; the mascot reacts to the failure.
            page.locator('.scenario[aria-selected="true"]').focus()
            page.keyboard.press("Home")
            expect(page.locator('.scenario[aria-selected="true"]')).to_contain_text("Scenario A")
            page.keyboard.press("ArrowRight")
            expect(page.locator('.scenario[aria-selected="true"]')).to_contain_text("Failed at s6")
            expect(page.locator("#mascot")).to_have_attribute("data-mood", "alert")
            expect(page.locator("#result-summary")).to_contain_text("Stopped safely")

            # Clicking a step in the flow inspects it.
            page.locator("#flow .node").first.click()
            expect(page.locator("#action")).to_contain_text("Open page")

            # No horizontal scrolling at this width.
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

            # Developer view keeps the full audit; the view tabs work with the keyboard too.
            page.locator("#tab-demo").focus()
            page.keyboard.press("ArrowRight")
            expect(page.locator("#tab-dev")).to_have_attribute("aria-selected", "true")
            expect(page.locator("#dev-view")).to_be_visible()
            expect(page.locator("#healing")).to_contain_text("Recorded locators exhausted (4)")
            assert "view=developer" in page.url
            assert page.evaluate(SQUEEZED_TEXT_JS) == []
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert problems == []
        finally:
            browser.close()

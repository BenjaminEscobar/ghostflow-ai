"""GF-006 in Chromium: a real run of the synthetic portal stores real screenshots tied to its
steps (never of the sensitive step), and the Demo Console shows them as "Recorded capture",
while the recorded GF-003 evidence (which has no images) stays a "Reconstructed view"."""

import threading
import time
from datetime import UTC, datetime

import pytest
from playwright.sync_api import expect, sync_playwright
from scenario import DEMO_PASSWORD, demo_recording
from test_e2e_console import _adapter, _free_port, console_url, variant_portal  # noqa: F401 - fixtures

from ghostflow import demo_healing
from ghostflow.execution import playwright_web

from ghostflow.evidence import AiSource, EvidenceBundle, apply_event, finish_scenario, new_scenario
from ghostflow.evidence import outline_workflow, write_bundle
from ghostflow.execution.capture import CapturePolicy, CaptureRecorder
from ghostflow.recording import RecordingCreate, compile_recording

pytestmark = pytest.mark.e2e


def _captured_run(tmp_path, origin, name="gf006", fail_verification=False):
    workflow = compile_recording(RecordingCreate.model_validate(demo_recording(origin)))
    if fail_verification:
        from ghostflow.dsl.models import TextVisible

        workflow = workflow.model_copy(update={"verify": [TextVisible(type="text_visible", text="Intentionally absent verification canary")]})
    now = datetime.now(UTC)
    bundle = EvidenceBundle(
        title="e2e captures", created_at=now, updated_at=now, origin="demo_runner",
        ai=AiSource(provider="mock", model="mock"), workflow=outline_workflow(workflow, "test"),
    )  # fmt: skip
    scenario = new_scenario("C", "C", portal="crear-requerimiento", healing=True, workflow=workflow, inputs={"contrasena": DEMO_PASSWORD})
    bundle.scenarios.append(scenario)
    runs = tmp_path / "runs"
    events = []

    def observe(event, payload):
        events.append((event, payload))
        apply_event(scenario, event, payload)

    recorder = CaptureRecorder(runs / "captures" / name, "C", workflow, CapturePolicy())
    result = _adapter(origin).execute(workflow, {"contrasena": DEMO_PASSWORD}, observer=observe, capture=recorder)
    finish_scenario(scenario, result)
    bundle.state = "finished"
    write_bundle(runs / f"{name}.json", bundle)
    return result, scenario, events, runs / "captures" / name


def test_real_captures_are_tied_to_steps_and_skip_the_sensitive_one(tmp_path, variant_portal):
    result, scenario, events, folder = _captured_run(tmp_path, variant_portal)
    assert result.status == "passed"
    assert next(s for s in result.steps if s.step_id == "s6").resolution == "ai_recovery"

    caps = {c.id: c for c in scenario.captures}
    taken = [c for c in scenario.captures if c.skipped is None]
    assert taken and len(taken) <= CapturePolicy().max_per_scenario
    for c in taken:
        data = (folder / f"{c.id}.jpg").read_bytes()
        assert data[:3] == b"\xff\xd8\xff" and len(data) == c.bytes <= CapturePolicy().max_bytes_per_image
        assert c.page.startswith(variant_portal) and "?" not in c.page
    # Nothing typed into the password field (s3) is captured, nor its page until it changes.
    assert {c.skipped for c in scenario.captures if c.step_id in {"s3", "s4"}} <= {"sensitive_step", "sensitive_page"}
    assert not list(folder.glob("*-s3-*")) and not list(folder.glob("*-s4-*"))
    assert sorted(p.name for p in folder.iterdir()) == sorted(f"{c.id}.jpg" for c in taken)
    # The page without the recorded element is captured before recovery starts. The healer acts
    # on its validated candidate itself, so a recovered step has no "before" image (none is faked).
    phases = {(c.step_id, c.phase) for c in taken}
    assert {("s1", "after"), ("s6", "not_found"), ("s7", "after")} <= phases
    # A fast machine can finish >10 steps in one second. The final capture still obeys the
    # documented rate/byte budgets; its metadata must explain a legitimately skipped image.
    final = next(c for c in scenario.captures if c.phase == "final")
    assert final.skipped in {None, "rate", "limit"}
    assert ("s6", "before") not in phases
    assert any(c.box for c in taken if c.phase == "before")  # the element in use, before acting
    # Every capture event comes while its step runs: after step_started, before step_finished.
    order = [(e, p.get("step_id"), p.get("id")) for e, p in events]
    for c in taken:
        if c.step_id:
            at = order.index(("capture", c.step_id, c.id))
            assert order.index(("step_started", c.step_id, None)) < at < order.index(("step_finished", c.step_id, None))
    assert len(caps) == len(scenario.captures)


def test_console_shows_recorded_captures_and_keeps_reconstruction_for_gf003(tmp_path, variant_portal, console_url):
    _, scenario, _, _ = _captured_run(tmp_path, variant_portal)
    ids = [s.id for s in scenario.steps]
    boxed = next(c.step_id for c in scenario.captures if c.phase == "before" and c.box and c.skipped is None)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            problems = []
            page.on("pageerror", lambda exc: problems.append(str(exc)))
            page.on("request", lambda req: problems.append(req.url) if not req.url.startswith((console_url, "data:")) else None)

            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#stage-prov")).to_have_text("Recorded capture")
            expect(page.locator("#stage-caption")).to_contain_text("not a live view")
            image = page.locator("#shot-frame img.full")
            expect(image).to_be_visible()
            assert page.evaluate("document.querySelector('#shot-frame img.full').naturalWidth") > 0
            assert "/api/demo/captures/gf006/" in image.get_attribute("src")
            expect(page.locator("#page")).to_be_hidden()

            page.locator("#steps .srow").nth(5).click()  # s6: the recovered step
            expect(page.locator("#shot-note")).to_contain_text("Step s6")
            expect(page.locator(".shot-tag")).to_contain_text("Recorded element not found here")
            page.locator("#steps .srow").nth(ids.index(boxed)).click()  # highlighted before acting
            expect(page.locator("#shot-frame .focus-box")).to_be_visible()
            page.locator("#steps .srow").nth(2).click()  # s3: the password, never captured
            expect(page.locator("#shot-note")).to_contain_text("never captured")

            page.locator("#show-reconstructed").click()
            expect(page.locator("#stage-prov")).to_have_text("Reconstructed view")
            expect(page.locator("#page")).to_be_visible()

            page.goto(f"{console_url}/console/#recorded%2Fgf003-meta-recovery")
            expect(page.locator("#badges")).to_contain_text("Recorded evidence")  # the source really switched
            expect(page.locator("#stage-prov")).to_have_text("Reconstructed view")
            expect(page.locator("#shot")).to_be_hidden()
            expect(page.locator("#view-toggle")).to_be_hidden()

            # Back and forth: each evidence starts from its own state, nothing carries over.
            for _ in range(2):
                page.goto(f"{console_url}/console/#runs%2Fgf006")
                expect(page.locator("#view-toggle")).to_be_visible()
                expect(page.locator("#stage-prov")).to_have_text("Recorded capture")
                expect(page.locator("#show-capture")).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#shot-frame img.full")).to_be_visible()
                page.goto(f"{console_url}/console/#recorded%2Fgf003-meta-recovery")
                expect(page.locator("#view-toggle")).to_be_hidden()
                expect(page.locator("#stage-prov")).to_have_text("Reconstructed view")
                expect(page.locator("#shot")).to_be_hidden()
                expect(page.locator("#page")).to_be_visible()
            # Choosing the source from the sidebar behaves the same as the address.
            page.select_option("#source-select", "runs/gf006")
            expect(page.locator("#stage-prov")).to_have_text("Recorded capture")
            expect(page.locator("#view-toggle")).to_be_visible()
            assert problems == []
        finally:
            browser.close()


# What the console shows, sampled while the rehearsal runs (scenario, mood, provenance, image tag).
CONSOLE_STATE_JS = """() => [
  (document.querySelector('#scenarios [aria-selected="true"]') || {}).dataset?.key || '-',
  document.getElementById('mascot').dataset.mood,
  document.getElementById('stage-prov').textContent,
  (document.querySelector('.shot-tag') || {}).textContent || '',
]"""


def test_integrated_rehearsal_runs_headless_and_the_console_follows_a_b_c(tmp_path, console_url, monkeypatch):
    """`make demo-rehearsal`: the runner (mock, no Meta) drives a headless Chromium through A, B and
    C, and the Demo Console, the only visible window, follows it with real live captures."""
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    workflow_file = tmp_path / "workflow.json"
    workflow_file.write_text(compile_recording(RecordingCreate.model_validate(demo_recording(origin))).model_dump_json())
    monkeypatch.setenv("GHOSTFLOW_ALLOWED_ORIGINS", origin)
    launched = []
    original_init = playwright_web.PlaywrightWebAdapter.__init__

    def spy(self, *args, **kwargs):
        launched.append(kwargs.get("headless", True))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(playwright_web.PlaywrightWebAdapter, "__init__", spy)
    argv = ["--provider", "mock", "--port", str(port), "--workflow-file", str(workflow_file),
            "--evidence-dir", str(tmp_path / "runs"), "--name", "rehearsal",
            "--delay-ms", "250", "--hold-ms", "600", "--step-timeout-ms", "2500"]  # fmt: skip
    outcome = {}
    runner = threading.Thread(target=lambda: outcome.setdefault("rc", demo_healing.main(argv)), daemon=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            problems = []
            page.on("pageerror", lambda exc: problems.append(str(exc)))
            page.goto(f"{console_url}/console/")  # "Follow live runs" is on by default
            runner.start()
            seen, done_at = [], None
            deadline = time.monotonic() + 120
            # Keep watching a little after the run ends: the console may still be showing the last events.
            while time.monotonic() < deadline and (done_at is None or time.monotonic() < done_at + 3):
                if done_at is None and not runner.is_alive():
                    done_at = time.monotonic()
                state = page.evaluate(CONSOLE_STATE_JS)
                if not seen or seen[-1] != state:
                    seen.append(state)
                page.wait_for_timeout(120)
            runner.join(timeout=5)
            assert outcome.get("rc") == 0 and launched == [True, True, True]  # three headless scenarios

            live = [x for x in seen if x[2] == "Live capture"]
            assert {x[0] for x in live} >= {"A", "B", "C"}, seen  # the viewer followed every scenario live
            # B: the missing element and the safe stop; C: missing element, investigation, recovery.
            assert any(x[0] == "B" and x[1] == "alert" and "not found here" in x[3] for x in live), seen
            moods_c = [x[1] for x in live if x[0] == "C"]
            assert "investigating" in moods_c and "proud" in moods_c[moods_c.index("investigating"):], seen
            assert any(x[0] == "C" and x[1] == "investigating" and "not found" in x[3] for x in live), seen

            # After the run, the same evidence is a recorded (not live) one, scenario by scenario.
            page.goto(f"{console_url}/console/#runs%2Frehearsal")
            page.reload()
            expect(page.locator("#badges")).to_contain_text("Saved run")
            for key, mood in [("A", "happy"), ("B", "alert"), ("C", "proud")]:
                page.locator(f'#scenarios [data-key="{key}"]').click()
                expect(page.locator("#stage-prov")).to_have_text("Recorded capture")
                expect(page.locator("#mascot")).to_have_attribute("data-mood", mood)
                page.wait_for_function("() => document.querySelector('#shot-frame img.full')?.naturalWidth > 0")
                page.screenshot(path=str(tmp_path / f"rehearsal-{key}.png"), full_page=True)
            page.locator('#scenarios [data-key="C"]').click()
            expect(page.locator(".shot-tag")).to_contain_text("GhostFlow then chose")
            expect(page.locator("#film")).to_contain_text("s7")  # the process continued after recovery
            assert problems == []
        finally:
            browser.close()
    folder = tmp_path / "runs" / "captures" / "rehearsal"
    names = {p.name for p in folder.iterdir() if p.suffix == ".jpg"}
    assert {n.split("-")[0] for n in names} == {"A", "B", "C"}
    assert not [n for n in names if "-s3-" in n or "-s4-" in n]  # the password page is never captured
    assert not [n for n in names if n.startswith("C-") and "-s6-before" in n]  # no faked image of the recovered element

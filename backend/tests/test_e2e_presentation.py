"""Final UX polish in Chromium: the expanded browser viewer (real captures larger, never upscaled,
in sync with a live A/B/C rehearsal, accessible), the AI provenance labelled stage by stage, and the
browser-first Demo layout with its sober palette, measured on the rendered page."""

import base64
import json
import threading
import time

import pytest
from conftest import REPO_ROOT
from playwright.sync_api import expect, sync_playwright
from scenario import demo_recording
from test_e2e_captures import _captured_run
from test_e2e_console import _free_port, console_url, variant_portal  # noqa: F401 - fixtures

from ghostflow import demo_healing
from ghostflow.dsl.models import Workflow
from ghostflow.recording import RecordingCreate, compile_recording

pytestmark = pytest.mark.e2e

META_WORKFLOW = REPO_ROOT / "docs" / "examples" / "workflow-meta-interpreted.json"
IMAGE_BOX_JS = """() => {
  const img = document.querySelector('#shot-frame img.full');
  const r = img.getBoundingClientRect();
  return {width: r.width, height: r.height, bottom: r.bottom, natural: img.naturalWidth, inDialog: !!img.closest('dialog')};
}"""


def _console(pw, width, height):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
    problems = []
    page.on("pageerror", lambda exc: problems.append(str(exc)))
    return browser, page, problems


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 1366), (820, 1180)])
def test_expanded_browser_shows_the_real_capture_larger_and_returns(tmp_path, variant_portal, console_url, width, height):
    _, scenario, _, _ = _captured_run(tmp_path, variant_portal)
    ids = [s.id for s in scenario.steps]
    with sync_playwright() as pw:
        browser, page, problems = _console(pw, width, height)
        try:
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#shot-frame img.full")).to_be_visible()
            page.wait_for_function("document.querySelector('#shot-frame img.full').naturalWidth > 0")
            normal = page.evaluate(IMAGE_BOX_JS)

            page.locator("#expand-browser").click()
            dialog = page.locator("#browser-dialog")
            expect(dialog).to_be_visible()
            assert page.evaluate("document.getElementById('browser-dialog').matches(':modal')")
            expect(page.locator("#xb-close")).to_be_focused()
            expect(dialog.locator("#stage-prov")).to_have_text("Recorded capture")  # provenance moves with it
            expect(dialog.locator("#stage-caption")).to_contain_text("not a live view")
            page.wait_for_function("document.querySelector('#browser-dialog #shot-frame img.full').naturalWidth > 0")
            big = page.evaluate(IMAGE_BOX_JS)
            assert big["inDialog"] and big["width"] >= normal["width"] - 1
            if width >= 1440:
                # Readable on a projector. The Demo browser is already the wide column (~850 px), so the
                # dialog gains by height: the capture takes most of the screen, still fully in view.
                assert big["width"] >= 1.15 * normal["width"] and big["height"] >= 0.6 * height, (normal, big)
            assert big["width"] <= big["natural"] + 1, big  # its own pixels at most: never upscaled
            assert big["bottom"] <= height, big  # the whole image is in view
            # Workflow progress, the agent and both AI stages stay visible, compactly.
            expect(page.locator("#xb-progress")).to_contain_text(f"of {len(ids)}")
            expect(page.locator("#xb-agent-label")).not_to_be_empty()
            expect(page.locator("#xb-prov")).to_contain_text("Workflow interpretation")
            expect(page.locator("#xb-prov")).to_contain_text("Self-Healing recovery")
            assert page.evaluate("(() => { const b = document.querySelector('.xb-body'); return b.scrollWidth <= b.clientWidth + 1; })()")

            # Keyboard: arrows move between steps; the sensitive step stays uncaptured.
            for _ in ids:
                page.keyboard.press("ArrowLeft")
            expect(page.locator("#xb-progress")).to_contain_text(f"Step 1 of {len(ids)}")
            expect(page.locator("#xb-prev")).to_have_attribute("aria-disabled", "true")
            for _ in range(ids.index("s3")):
                page.keyboard.press("ArrowRight")
            expect(page.locator("#xb-progress")).to_contain_text("· s3 ·")
            expect(dialog.locator("#shot-note")).to_contain_text("never captured")
            for _ in range(ids.index("s6") - ids.index("s3")):
                page.locator("#xb-next").click()
            expect(dialog.locator(".shot-tag")).to_contain_text("Recorded element not found here")
            expect(dialog.locator("#events")).to_contain_text("Recorded element not found")
            dialog.locator("#film .frame").first.click()  # the filmstrip works in the dialog too
            expect(page.locator("#xb-progress")).to_contain_text(f"Step 1 of {len(ids)}")

            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(page.locator("#expand-browser")).to_be_focused()
            assert page.evaluate("!!document.querySelector('.sc-body #stage-col #events') && !document.querySelector('dialog #film')")
            expect(page.locator("#shot-frame img.full")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert problems == []
        finally:
            browser.close()


def test_expanded_reconstruction_of_gf003_is_labelled_and_larger(console_url):
    with sync_playwright() as pw:
        browser, page, problems = _console(pw, 1440, 900)
        try:
            page.goto(f"{console_url}/console/#recorded%2Fgf003-meta-recovery")
            expect(page.locator("#stage-prov")).to_have_text("Reconstructed view")
            page.locator("#expand-browser").click()
            dialog = page.locator("#browser-dialog")
            expect(dialog.locator("#stage-prov")).to_have_text("Reconstructed view")  # no screenshot invented
            expect(dialog.locator("#shot")).to_be_hidden()
            expect(dialog.locator("#page")).to_be_visible()
            expect(dialog.locator("#stage-caption")).to_contain_text("not a screenshot")
            assert page.evaluate("getComputedStyle(document.querySelector('#browser-dialog .ptitle')).fontSize") == "24px"
            expect(page.locator("#xb-prov")).to_contain_text("Meta Muse Spark")
            page.locator("#xb-close").click()
            expect(dialog).to_be_hidden()
            expect(page.locator("#expand-browser")).to_be_focused()
            assert problems == []
        finally:
            browser.close()


def test_provenance_separates_the_interpretation_from_the_recovery(tmp_path, variant_portal, console_url):
    # A rehearsal-like run: real Playwright, offline mock recovery, on a workflow whose stored
    # interpretation came from Meta when it was compiled (GF-002).
    _captured_run(tmp_path, variant_portal)
    path = tmp_path / "runs" / "gf006.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = Workflow.model_validate_json(META_WORKFLOW.read_text(encoding="utf-8")).interpretation
    data["workflow"]["interpretation"] = stored.model_dump(mode="json")
    path.write_text(json.dumps(data), encoding="utf-8")
    with sync_playwright() as pw:
        browser, page, problems = _console(pw, 1440, 900)
        try:
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            # Demo view: one compact chip per stage, for the selected scenario (C).
            chips = page.locator("#prov-row .badge")
            expect(chips.nth(0)).to_have_text("Interpretation · Meta recorded")
            expect(chips.nth(0)).to_have_class("badge recorded-real")
            expect(chips.nth(1)).to_have_text("Recovery · Mock")
            expect(chips.nth(1)).to_have_class("badge simulated")
            expect(page.locator("#heal-summary")).to_contain_text("Mock · simulated · 0 Meta requests")
            expect(page.locator("#heal-summary")).not_to_contain_text("Meta Muse Spark")  # a mock never reads as Meta
            expect(page.locator("#badges")).to_contain_text("Recovery · Simulated AI")
            expect(page.locator("#demo-view")).not_to_contain_text("Where the AI came from")
            # Developer view keeps every detail: dates, models, requests and the separation note.
            page.locator("#tab-dev").click()
            interp, recovery = page.locator("#prov-stages .prov-stage").nth(0), page.locator("#prov-stages .prov-stage").nth(1)
            expect(interp).to_contain_text("Workflow interpretation")
            expect(interp).to_contain_text("Meta Muse Spark")
            expect(interp).to_contain_text("generated 2026-10-27 16:04 UTC")
            expect(interp).to_contain_text("not a new request")
            expect(interp).to_have_class("prov-stage recorded-real")
            expect(recovery).to_contain_text("Self-Healing recovery · scenario C")
            expect(recovery).to_contain_text("Offline mock · simulated recovery")
            expect(recovery).to_contain_text("0 Meta requests")
            expect(recovery).to_contain_text("Playwright drove Chromium")
            expect(recovery).to_have_class("prov-stage simulated")
            note = page.locator("#prov-note")
            expect(note).to_contain_text("Two independent AI stages, not one execution: the real Meta interpretation was stored 2026-10-27 16:04 UTC")
            expect(note).to_contain_text("was simulated by the offline mock. Neither stage sent a new Meta request.")

            # The recorded GF-003 evidence: a real Muse Spark recovery, shown from the file.
            page.locator("#tab-demo").click()
            page.goto(f"{console_url}/console/#recorded%2Fgf003-meta-recovery")
            expect(chips.nth(0)).to_have_text("Interpretation · None")
            expect(chips.nth(1)).to_have_text("Recovery · Meta real · recorded")
            expect(chips.nth(1)).to_have_class("badge recorded-real")
            expect(page.locator("#heal-summary")).to_contain_text("Meta Muse Spark · recorded · 1 request")
            expect(page.locator("#badges")).to_contain_text("Recovery · Real Meta Model API")
            page.locator("#tab-dev").click()
            expect(interp).to_contain_text("No interpretation stored")
            expect(recovery).to_contain_text("Meta Muse Spark · muse-spark-1.3")
            expect(recovery).to_contain_text("recorded evidence: shown from the file, not a new request")
            expect(recovery).to_contain_text("1 real Meta request recorded in this evidence")
            page.locator("#tab-demo").click()
            page.locator('#scenarios [data-key="B"]').click()
            expect(chips.nth(1)).to_have_text("Recovery · Off")
            expect(page.locator("#heal-summary")).to_contain_text("Self-healing off")
            expect(page.locator("#heal-summary")).to_contain_text("GhostFlow did not guess")
            expect(page.locator("#result-summary")).to_contain_text("Stopped safely")
            assert problems == []
        finally:
            browser.close()


SAMPLE_JS = """() => [
  document.getElementById('browser-dialog').open,
  !!document.querySelector('#browser-dialog #stage-col'),
  document.getElementById('xb-title').textContent,
  document.querySelector('#browser-dialog #stage-prov').textContent,
  document.getElementById('xb-agent').dataset.mood || '',
  document.getElementById('xb-prov').textContent,
]"""


def test_expanded_view_follows_the_live_rehearsal(tmp_path, console_url, monkeypatch):
    """Expanded before the run starts, the dialog stays open while the console follows the live
    rehearsal (offline mock, no Meta) through A, B and C with real captures."""
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    workflow_file = tmp_path / "workflow.json"
    workflow_file.write_text(compile_recording(RecordingCreate.model_validate(demo_recording(origin))).model_dump_json())
    monkeypatch.setenv("GHOSTFLOW_ALLOWED_ORIGINS", origin)
    argv = ["--provider", "mock", "--port", str(port), "--workflow-file", str(workflow_file),
            "--evidence-dir", str(tmp_path / "runs"), "--name", "rehearsal",
            "--delay-ms", "250", "--hold-ms", "600", "--step-timeout-ms", "2500"]  # fmt: skip
    outcome = {}
    runner = threading.Thread(target=lambda: outcome.setdefault("rc", demo_healing.main(argv)), daemon=True)
    with sync_playwright() as pw:
        browser, page, problems = _console(pw, 1440, 900)
        try:
            page.goto(f"{console_url}/console/")  # recorded GF-003 first; "Follow live runs" is on
            expect(page.locator("#stage-prov")).to_have_text("Reconstructed view")
            page.locator("#expand-browser").click()
            expect(page.locator("#browser-dialog")).to_be_visible()
            runner.start()
            seen, done_at = [], None
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and (done_at is None or time.monotonic() < done_at + 3):
                if done_at is None and not runner.is_alive():
                    done_at = time.monotonic()
                state = page.evaluate(SAMPLE_JS)
                if not seen or seen[-1] != state:
                    seen.append(state)
                page.wait_for_timeout(120)
            runner.join(timeout=5)
            assert outcome.get("rc") == 0
            assert all(s[0] and s[1] for s in seen), seen  # the dialog never closed nor lost the viewer
            live = [s for s in seen if s[3] == "Live capture"]
            assert {s[2].split(" — ")[0] for s in live} >= {"Scenario A", "Scenario B", "Scenario C"}, seen
            moods_c = [s[4] for s in live if s[2].startswith("Scenario C")]
            assert "investigating" in moods_c and "proud" in moods_c[moods_c.index("investigating"):], seen
            assert any(s[2].startswith("Scenario C") and "Offline mock · simulated recovery" in s[5] for s in live), seen
            assert not any("Real · Meta" in s[5].split("Self-Healing recovery")[1] for s in live), seen
            page.keyboard.press("Escape")
            expect(page.locator("#browser-dialog")).to_be_hidden()
            assert problems == []
        finally:
            browser.close()


# Share of the rendered screen in violet tones, measured on a real screenshot decoded in the page
# (a data: image on a canvas, so no new dependency). Blue (#1877F2, hue ~214) is not counted.
VIOLET_SHARE_JS = """async (src) => {
  const img = new Image(); img.src = src; await img.decode();
  const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
  const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  let violet = 0;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i] / 255, g = d[i + 1] / 255, b = d[i + 2] / 255, max = Math.max(r, g, b), min = Math.min(r, g, b);
    if (max - min < 0.12 || max < 0.3) continue;  // greys, whites and near-black are neutral
    let h = max === r ? ((g - b) / (max - min)) % 6 : max === g ? (b - r) / (max - min) + 2 : (r - g) / (max - min) + 4;
    h = (h * 60 + 360) % 360;
    if (h >= 245 && h <= 300) violet += 1;
  }
  return violet / (d.length / 4);
}"""
LAYOUT_JS = """() => {
  const box = (sel) => { const r = document.querySelector(sel).getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, right: r.right}; };
  return {browser: box('.sc-body .browser'), panel: box('.status-panel'), heal: box('#heal-summary'), hero: box('.hero'),
          overflow: document.documentElement.scrollWidth - window.innerWidth};
}"""


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 1366), (820, 1180)])
def test_demo_view_shows_the_browser_first_with_the_result_beside_it(tmp_path, variant_portal, console_url, width, height):
    _captured_run(tmp_path, variant_portal)
    from test_e2e_console import SQUEEZED_TEXT_JS

    with sync_playwright() as pw:
        browser, page, problems = _console(pw, width, height)
        try:
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#shot-frame img.full")).to_be_visible()
            page.wait_for_function("document.querySelector('#shot-frame img.full').naturalWidth > 0")
            m = page.evaluate(LAYOUT_JS)
            b, panel = m["browser"], m["panel"]
            # A compact header, one heading line and the page progress: the browser comes right after.
            assert m["hero"]["h"] <= 140, m
            if width >= 1440:
                assert b["y"] <= 350, m  # the brief's target at 1440 x 900
            assert b["y"] + 300 < height, m  # the capture is on the first screen at every size
            if width >= 1000:
                # Desktop and tablet landscape: the browser is the wide column (~75%); the panel with
                # GhostFlow's state, Recovery and Verification sits beside it, top-aligned.
                assert abs(panel["y"] - b["y"]) <= 2 and panel["x"] >= b["right"], m
                assert b["w"] >= 0.7 * (b["w"] + panel["w"]), m
            else:
                assert panel["y"] >= b["y"] + b["h"] and b["w"] >= 0.9 * width - 48, m  # below, full width
            assert m["overflow"] <= 1 and page.evaluate(SQUEEZED_TEXT_JS) == [], m
            # Recovery explains the decision; the audit (latency, tokens, candidates) stays in Developer.
            heal = page.locator("#heal-summary")
            expect(heal).to_contain_text("Recovered")
            expect(heal).to_contain_text("Crear requerimiento")
            expect(heal).to_contain_text("10/10 passed")
            expect(heal).to_contain_text("Mock · simulated")
            for detail in ("Latency", "tokens", "Confidence", "Candidates"):
                expect(heal).not_to_contain_text(detail)
            expect(page.locator("#verifs")).to_contain_text("2/2 passed")
            expect(page.locator("#mascot")).to_have_attribute("data-mood", "proud")
            expect(page.locator("#prov-row .badge")).to_have_count(2)
            # A brief scenario label (A/B navigation: test_e2e_console); screen readers keep the outcome.
            expect(page.locator('#scenarios [data-key="C"] .title')).to_have_text("Changed UI")
            expect(page.locator('#scenarios [data-key="C"] .st')).to_contain_text("Recovered")
            expect(page.locator('#scenarios [data-key="C"]')).to_contain_text("Scenario C")
            # A sober palette on the rendered page: violet is a small detail, not a theme.
            shot = "data:image/png;base64," + base64.b64encode(page.screenshot()).decode()
            assert page.evaluate(VIOLET_SHARE_JS, shot) <= 0.05
            page.locator("#heal-summary .link-btn").click()
            expect(page.locator("#dev-view")).to_be_visible()
            expect(page.locator("#healing")).to_contain_text("Deterministic validation by GhostFlow")
            assert problems == []
        finally:
            browser.close()

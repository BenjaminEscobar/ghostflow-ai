"""Adversarial regressions discovered in the independent GF-006 review. No Meta calls."""

import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect, sync_playwright
from test_captures import FakePage, JPEG, _run_with_captures, recorder
from test_demo_console import make_client, workflow  # noqa: F401
from test_e2e_console import console_url, variant_portal  # noqa: F401
from test_e2e_captures import _captured_run
from test_e2e_replay import _client, _record_compile_run

from ghostflow.execution.capture import CaptureBudget, CaptureRecorder, capture_session, prune_captures


def test_wait_for_url_does_not_clear_secrets_in_the_same_document(tmp_path, workflow):
    rec, page = recorder(tmp_path, workflow), FakePage()
    rec.take(page, step_id="s3", phase="before")
    page.url += "/spa-route"
    assert rec.take(page, step_id="s5", phase="after")["skipped"] == "sensitive_page"
    assert rec.take(page, step_id=None, phase="final")["skipped"] == "sensitive_page"
    assert not page.shots
    page.events["domcontentloaded"]()
    assert rec.take(page, step_id="s5", phase="after").get("skipped") is None


def test_capture_never_scrolls_or_logs_browser_exception_text(tmp_path, workflow, caplog):
    class Target:
        def scroll_into_view_if_needed(self, **kw):
            pytest.fail("Presentation must not scroll the execution page")

        def bounding_box(self, **kw):
            return {"x": 0, "y": 5000, "width": 100, "height": 20}

    rec, page = recorder(tmp_path, workflow), FakePage()
    assert rec.take(page, step_id="s6", phase="before", target=Target()).get("skipped") is None
    assert page.shots[0]["caret"] == "initial" and page.shots[0]["scale"] == "css"
    page.screenshot = lambda **kw: (_ for _ in ()).throw(RuntimeError("synthetic-sensitive-value"))
    assert rec.take(page, step_id="s7", phase="after")["skipped"] == "error"
    assert "synthetic-sensitive-value" not in caplog.text


def test_failed_capture_attempts_consume_the_rate_limit(tmp_path, workflow):
    rec, page = recorder(tmp_path, workflow, max_per_second=2), FakePage(fail=True)
    assert [rec.take(page, step_id="s1", phase="after")["skipped"] for _ in range(4)] == ["error", "error", "rate", "rate"]


@pytest.mark.parametrize("component", ["captures", "run1", "image", "bundle"])
def test_symlink_aliases_never_serve_captures(tmp_path, workflow, component):
    folder = _run_with_captures(tmp_path, workflow)
    if component == "bundle":
        original = tmp_path / "runs" / "run1.json"
    elif component == "image":
        original = folder / "C-001-s1-after.jpg"
    else:
        original = folder if component == "run1" else folder.parent
    moved = tmp_path / "outside"
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=moved.is_dir())
    with make_client(tmp_path) as client:
        assert client.get("/api/demo/captures/run1/C-001-s1-after").status_code == 404


def test_foreign_browser_cannot_embed_or_fetch_capture(tmp_path, workflow):
    _run_with_captures(tmp_path, workflow)
    with make_client(tmp_path) as client:
        for headers in [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://foreign.invalid"}]:
            assert client.get("/api/demo/captures/run1/C-001-s1-after", headers=headers).status_code == 403
        response = client.get("/api/demo/captures/run1/C-001-s1-after")
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        for path in ["/api/demo/sources", "/api/demo/captures/run1/C-001-s1-after"]:
            assert client.get(path, headers={"Host": "foreign.invalid:8010"}).status_code == 403


@pytest.mark.parametrize("data", [b"not an image", JPEG[:5], JPEG[:7] + b"\xff\xff\xff\xff" + JPEG[11:]])
def test_invalid_or_excessive_pixel_images_are_rejected(tmp_path, workflow, data):
    folder = _run_with_captures(tmp_path, workflow)
    (folder / "C-001-s1-after.jpg").write_bytes(data)
    with make_client(tmp_path) as client:
        assert client.get("/api/demo/captures/run1/C-001-s1-after").status_code == 404


def test_retention_never_follows_a_directory_symlink_or_deletes_active_run(tmp_path):
    root, outside = tmp_path / "captures", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "private.jpg").write_bytes(JPEG)
    (root / "alias").symlink_to(outside, target_is_directory=True)
    with capture_session(root, "active"):
        (root / "active" / "live.jpg").write_bytes(JPEG)
        assert prune_captures(root, 0) == []
        assert (root / "active" / "live.jpg").exists()
    assert prune_captures(root, 0) == ["active"]
    assert (outside / "private.jpg").read_bytes() == JPEG


def test_capture_writer_rejects_symlink_and_never_overwrites_another_run(tmp_path, workflow):
    root, outside = tmp_path / "captures", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "run").symlink_to(outside, target_is_directory=True)
    rec = CaptureRecorder(root / "run", "A", workflow)
    assert rec.take(FakePage(), step_id="s1", phase="after")["skipped"] == "error"
    assert not list(outside.iterdir())
    a = CaptureRecorder(root / "unique", "A", workflow)
    b = CaptureRecorder(root / "unique", "A", workflow)
    assert a.take(FakePage(), step_id="s1", phase="after").get("skipped") is None
    assert b.take(FakePage(data=b"different"), step_id="s1", phase="after")["skipped"] == "error"
    assert (root / "unique" / "A-001-s1-after.jpg").read_bytes() == JPEG


def test_shared_budget_is_atomic_under_concurrent_scenarios(tmp_path, workflow):
    budget = CaptureBudget(len(JPEG))
    barrier = threading.Barrier(2)

    def take(prefix):
        page = FakePage()
        original = page.screenshot
        page.screenshot = lambda **kw: (barrier.wait(), original(**kw))[1]
        return CaptureRecorder(tmp_path / "captures" / "run", prefix, workflow, budget=budget).take(page, step_id="s1", phase="after")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(take, ["A", "B"]))
    assert sum(r.get("skipped") is None for r in results) == 1
    assert budget.used == len(JPEG)


@pytest.mark.parametrize("name", ["../outside", "nested/run", "x" * 101])
def test_runner_rejects_unsafe_names_before_writing(tmp_path, name):
    from ghostflow.demo_healing import main

    with pytest.raises(SystemExit) as error:
        main(["--name", name, "--evidence-dir", str(tmp_path / "runs")])
    assert error.value.code == 2
    assert not (tmp_path / "outside.json").exists()


def test_unavailable_capture_directory_disables_only_presentation(tmp_path):
    root = tmp_path / "captures"
    root.write_text("not a directory")
    with capture_session(root, "run") as ready:
        assert ready is False


@pytest.mark.e2e
def test_console_preserves_keyboard_focus_and_handles_retained_images(tmp_path, variant_portal, console_url):
    _captured_run(tmp_path, variant_portal)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#shot-frame img.full")).to_be_visible()
            for width in [1920, 1440, 1024, 820]:
                page.set_viewport_size({"width": width, "height": 1080 if width == 1920 else 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(tmp_path / f"demo-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator("#tab-dev").click()
            page.screenshot(path=str(tmp_path / "developer-1440.png"), full_page=True)
            page.locator("#tab-demo").click()
            tab = page.locator('#scenarios [aria-selected="true"]')
            tab.focus()
            page.wait_for_timeout(3300)  # a complete sources polling cycle
            expect(tab).to_be_focused()
            row = page.locator("#steps .srow").nth(5)
            row.focus()
            page.keyboard.press("Enter")
            expect(row).to_be_focused()
            assert page.evaluate("imgCache.size") <= 83
            page.locator("#replay").click()
            page.locator("#steps .srow").nth(2).click()
            expect(page.locator("#replay-banner")).to_be_hidden()

            page.emulate_media(reduced_motion="reduce")
            assert page.locator("#mascot .look").evaluate("e => getComputedStyle(e).animationName") == "none"
            # Images can legitimately disappear after retention: never leave a broken browser view.
            for image in (tmp_path / "runs" / "captures" / "gf006").glob("*.jpg"):
                image.unlink()
            page.reload()
            expect(page.locator("#stage-prov")).to_have_text("Reconstructed view")
            expect(page.locator("#stage-caption")).to_contain_text("Capture unavailable")
            expect(page.locator("#page")).to_be_visible()
            page.goto(f"{console_url}/console/#%ZZ")
            expect(page.locator("#app")).to_be_visible()
            page.locator(".skip").evaluate("e => e.click()")
            assert page.evaluate("state.current") == "runs/gf006"  # #app is an anchor, not evidence
        finally:
            browser.close()


@pytest.mark.e2e
def test_masks_are_present_in_the_actual_encoded_pixels(tmp_path, workflow, portal_url):
    # No OCR heuristic: sample the encoded JPEG at the center of each protected region.
    workflow = workflow.model_copy(update={"allowed_origins": [portal_url]})
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"{portal_url}/index.html")
            page.set_content('''<style>body{margin:0}input,iframe,div{display:block;box-sizing:border-box;
              width:200px;height:50px;border:0;margin:10px;background:white}</style>
              <input type="password" value="synthetic-private">
              <input autocomplete="section-x cc-number" value="synthetic-private">
              <input name="api_token" value="synthetic-private">
              <div data-private>synthetic-private</div>
              <iframe srcdoc="synthetic-private"></iframe>
              <input id="api-key" value="synthetic-private">''')
            rec = CaptureRecorder(tmp_path / "captures" / "pixels", "A", workflow)
            meta = rec.take(page, step_id="s1", phase="after")
            assert meta.get("skipped") is None and meta["masked"] == 6
            data = (tmp_path / "captures" / "pixels" / f"{meta['id']}.jpg").read_bytes()
            colors = page.evaluate('''async data => {
              const image = new Image(); image.src = 'data:image/jpeg;base64,' + data; await image.decode();
              const canvas = document.createElement('canvas'); canvas.width=1280; canvas.height=720;
              const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0);
              return [35,95,155,215,275,335].map(y => [...ctx.getImageData(110,y,1,1).data].slice(0,3));
            }''', base64.b64encode(data).decode())
            assert all(all(abs(actual - expected) <= 4 for actual, expected in zip(rgb, [213, 220, 231])) for rgb in colors)
        finally:
            browser.close()


@pytest.mark.e2e
def test_replay_recovery_has_authentic_moods_and_readable_motion(tmp_path, variant_portal, console_url):
    _captured_run(tmp_path, variant_portal)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            page.locator("#replay").click()
            mascot = page.locator("#mascot")
            expect(mascot).to_have_attribute("data-mood", "investigating", timeout=10000)
            transforms = []
            for i in range(3):
                page.locator(".hero-art").screenshot(path=str(tmp_path / f"investigating-{i}.png"))
                transforms.append(page.locator("#mascot .lens").evaluate("e => getComputedStyle(e).transform"))
                page.wait_for_timeout(350)
            assert len(set(transforms)) > 1
            expect(mascot).to_have_attribute("data-mood", "evaluating")
            expect(mascot).to_have_attribute("data-mood", "proud")
            expect(page.locator("#replay-banner")).to_contain_text("playback pace, not the real duration")
            # The status panel is outside the JPEG bounds; it cannot cover the page header.
            frame = page.locator("#shot-frame").bounding_box()
            tag = page.locator(".shot-tag").bounding_box()
            assert tag and tag["y"] >= frame["y"] + frame["height"] - 1
        finally:
            browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("allowed", [False, True])
def test_websocket_cannot_bypass_the_workflow_allowlist(tmp_path, portal_copy, serve_dir, allowed):
    hits = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(portal_copy), **kw)

        def do_GET(self):
            if self.headers.get("upgrade", "").lower() == "websocket":
                hits.append(self.path)
                self.send_response(400)
                self.end_headers()
            else:
                super().do_GET()

        def log_message(self, *args):
            pass

    outside = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=outside.serve_forever, daemon=True)
    thread.start()
    try:
        address = f"ws://127.0.0.1:{outside.server_port}/outside"
        script = portal_copy / "assets" / "app.js"
        script.write_text(script.read_text() + f"\nnew WebSocket('{address}');\n")
        origin = f"http://127.0.0.1:{outside.server_port}" if allowed else serve_dir(portal_copy)
        with _client(tmp_path, origin) as client:
            result = _record_compile_run(client, origin)["result"]
        assert result["status"] == "passed"
        if allowed:
            assert hits and not result["blocked_requests"]
        else:
            assert hits == [], "A page reached a WebSocket outside the workflow allowlist"
            assert address in result["blocked_requests"]
    finally:
        outside.shutdown()
        outside.server_close()


@pytest.mark.e2e
def test_fast_source_changes_drop_old_responses_and_final_poll_keeps_real_events(tmp_path, variant_portal, console_url):
    _captured_run(tmp_path, variant_portal)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#app")).to_be_visible()
            detail = page.request.get(f"{console_url}/api/demo/sources/runs/gf006").json()
            result = page.evaluate('''async detail => {
              const original = window.fetch, pending = [];
              window.fetch = (url, options) => String(url).includes('/sources/runs/')
                ? new Promise(resolve => pending.push({resolve, options})) : original(url, options);
              try {
                const a = select('runs/gf006'), b = select('runs/other'), newerA = select('runs/gf006');
                const fresh = structuredClone(detail); fresh.bundle.title = 'fresh';
                pending[2].resolve(new Response(JSON.stringify(fresh))); await newerA;
                const stale = structuredClone(detail); stale.bundle.title = 'stale';
                pending[0].resolve(new Response(JSON.stringify(stale))); await a;
                pending[1].resolve(new Response(JSON.stringify(stale))); await b;
                const title = state.detail.bundle.title;
                const aborted = pending.slice(0,2).every(p => p.options.signal.aborted);
                const live = structuredClone(detail); live.presentation.mode = 'live';
                live.bundle.scenarios[0].state = 'running'; live.bundle.scenarios[0].steps[5].state = 'running';
                state.detail = live; state.seen = {}; noticeLive(state.current);
                state.detail = structuredClone(detail); noticeLive(state.current);
                const moods = [state.moment, ...state.moments].filter(Boolean).map(m => m.mood);
                pin(5);
                return {title, aborted, moods, cancelled: !state.moment && !state.moments.length};
              } finally { window.fetch = original; clearMoments(); }
            }''', detail)
            assert result["title"] == "fresh" and result["aborted"]
            assert result["moods"][:2] == ["investigating", "proud"]
            assert result["cancelled"]
        finally:
            browser.close()


@pytest.mark.e2e
def test_failed_final_verification_never_celebrates_an_earlier_recovery(tmp_path, variant_portal, console_url):
    result, scenario, _, _ = _captured_run(tmp_path, variant_portal, fail_verification=True)
    assert result.status == "failed" and scenario.steps[5].recovery.outcome == "recovered"
    assert scenario.verifications[0].status == "failed"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#mascot")).to_have_attribute("data-mood", "alert")
            expect(page.locator("#mascot-say")).to_have_text("Verification failed · review required")
            expect(page.locator("#result-summary")).not_to_contain_text("Stopped safely")
            page.evaluate("() => { addMoment(scenario().key, 5, 'proud'); render(); }")
            expect(page.locator("#mascot")).to_have_attribute("data-mood", "alert")
            page.locator("#steps .srow").last.click()
            expect(page.locator(".shot-tag")).to_contain_text("Verification failed")
        finally:
            browser.close()


@pytest.mark.e2e
def test_sources_polling_refreshes_the_same_run_after_it_was_stale(tmp_path, variant_portal, console_url):
    _captured_run(tmp_path, variant_portal)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{console_url}/console/#runs%2Fgf006")
            expect(page.locator("#badges")).to_contain_text("Saved run")
            # Reproduce the UI left by a recovery that exceeded LIVE_WINDOW. The backend has
            # since completed the real mock run; the next normal sources poll must reveal it.
            page.evaluate('''() => {
              state.detail.presentation = {mode:'stale', text:'Interrupted · test stale poll'};
              state.detail.bundle.updated_at = '2000-01-01T00:00:00Z';
              clearTimeout(state.timers.live); render();
            }''')
            expect(page.locator("#badges")).to_contain_text("Interrupted")
            expect(page.locator("#mascot-say")).to_have_text("Updates interrupted · review required")
            expect(page.locator("#badges")).to_contain_text("Saved run", timeout=7000)
            assert page.evaluate("state.current") == "runs/gf006"
        finally:
            browser.close()

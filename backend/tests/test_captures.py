"""GF-006 browser captures, offline: privacy before an image exists, resource limits, the
capture metadata in evidence bundles, the read-only image endpoint and the console's honest
labels. A fake page stands in for Chromium (the real one is in test_e2e_captures.py)."""

import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime

import pytest
from conftest import REPO_ROOT
from pydantic import ValidationError
from scenario import DEMO_PASSWORD
from test_demo_console import BASE, bundle_for, make_client, workflow  # noqa: F401 - fixture
from test_healing import FakeMeta, FakeSource, chat, healer, meta, not_found, propose, step, variant_page

from ghostflow import demo_healing
from ghostflow.console import STATIC_DIR
from ghostflow.evidence import MAX_CAPTURES, CaptureRef, apply_event, write_bundle
from ghostflow.execution import playwright_web
from ghostflow.execution.capture import (
    MASK_SELECTOR,
    CaptureBudget,
    CapturePolicy,
    CaptureRecorder,
    is_capturable_url,
    prune_captures,
    sensitive_steps,
)
from ghostflow.execution.playwright_web import PlaywrightWebAdapter
from ghostflow.execution.recovery import RunState
from ghostflow.security import UrlPolicy

# Minimal frame header (1x1) for bounded-file tests; E2E uses Chromium's actual JPEG encoder.
JPEG = b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00" + b"0" * 2000


class FakeLocator:
    def __init__(self, n=1, box=None, fail=False):
        self.n, self.box, self.fail = n, box, fail

    def count(self):
        return self.n

    def scroll_into_view_if_needed(self, timeout):
        if self.fail:
            raise RuntimeError("detached")

    def bounding_box(self, timeout):
        if self.fail:
            raise RuntimeError("detached")
        return self.box


class FakePage:
    def __init__(self, url=f"{BASE}/dashboard.html?token=abc#x", data=JPEG, fail=False):
        self.url, self.data, self.fail = url, data, fail
        self.viewport_size = {"width": 1600, "height": 1000}
        self.shots: list[dict] = []
        self.masked: list[str] = []
        self.events = {}

    def on(self, event, callback):
        self.events[event] = callback

    def locator(self, selector):
        self.masked.append(selector)
        return FakeLocator(n=1)

    def screenshot(self, **kw):
        if self.fail:
            raise TimeoutError("screenshot timed out")
        self.shots.append(kw)
        return self.data


def recorder(tmp_path, workflow, **policy) -> CaptureRecorder:
    return CaptureRecorder(tmp_path / "captures" / "run", "C", workflow, CapturePolicy(**policy))


# -- the recorder -------------------------------------------------------------------------------


def test_capture_is_a_real_bounded_viewport_image_tied_to_its_step(tmp_path, workflow):
    rec, page = recorder(tmp_path, workflow), FakePage()
    target = FakeLocator(box={"x": 400, "y": 360, "width": 320, "height": 45})
    meta = rec.take(page, step_id="s6", phase="before", target=target)

    assert meta["id"] == "C-001-s6-before" and meta["step_id"] == "s6" and meta["phase"] == "before"
    assert meta.get("skipped") is None and meta["bytes"] == len(JPEG)
    assert meta["page"] == f"{BASE}/dashboard.html"  # no query or fragment
    assert (meta["width"], meta["height"]) == (1440, 900)  # clipped to the policy's maximum
    assert meta["box"] == {"x": 0.2778, "y": 0.4, "w": 0.2222, "h": 0.05}
    shot = page.shots[0]
    assert shot["type"] == "jpeg" and shot["clip"] == {"x": 0, "y": 0, "width": 1440, "height": 900}
    assert page.masked == [MASK_SELECTOR] and len(shot["mask"]) == 1 and meta["masked"] == 1
    assert (tmp_path / "captures" / "run" / "C-001-s6-before.jpg").read_bytes() == JPEG
    CaptureRef.model_validate(meta)  # what the bundle stores


def test_sensitive_step_is_never_captured_nor_the_rest_of_its_page(tmp_path, workflow):
    assert sensitive_steps(workflow) == {"s3"}  # the password field
    rec, page = recorder(tmp_path, workflow), FakePage(url=f"{BASE}/login.html")
    assert rec.take(page, step_id="s2", phase="before").get("skipped") is None
    assert rec.take(page, step_id="s3", phase="before")["skipped"] == "sensitive_step"
    assert rec.take(page, step_id="s4", phase="before")["skipped"] == "sensitive_page"  # password typed here
    page.events["domcontentloaded"]()  # only a new document clears the sensitive page
    assert rec.take(page, step_id="s5", phase="after").get("skipped") is None  # a new page (wait_for_url)
    assert len(page.shots) == 2  # no screenshot was even taken for s3/s4
    assert not list((tmp_path / "captures" / "run").glob("*s3*")) and not list((tmp_path / "captures" / "run").glob("*s4*"))


@pytest.mark.parametrize(
    "url",
    ["https://example.com/", "http://10.0.0.5:8080/", "http://localhost:9999/", "file:///etc/passwd",
     "http://169.254.169.254/latest/meta-data/", "about:blank", "chrome://settings"],
)  # fmt: skip
def test_only_the_allowlisted_local_portal_is_captured(tmp_path, workflow, url):
    rec, page = recorder(tmp_path, workflow), FakePage(url=url)
    assert rec.take(page, step_id="s1", phase="after")["skipped"] == "off_origin"
    assert page.shots == []
    assert is_capturable_url(f"{BASE}/x", [BASE]) and not is_capturable_url("http://evil.test/", ["http://evil.test"])


def test_count_rate_size_and_run_budget_are_bounded(tmp_path, workflow):
    rec, page = recorder(tmp_path, workflow, max_per_scenario=2), FakePage()
    assert [rec.take(page, step_id="s1", phase="after").get("skipped") for _ in range(3)] == [None, None, "limit"]

    slow = CaptureRecorder(tmp_path / "c2", "A", workflow, CapturePolicy(max_per_second=2))
    assert [slow.take(page, step_id="s1", phase="after").get("skipped") for _ in range(3)] == [None, None, "rate"]

    big = recorder(tmp_path, workflow, max_bytes_per_image=100)
    assert big.take(page, step_id="s1", phase="after")["skipped"] == "too_large"

    budget = CaptureBudget(max_bytes=len(JPEG) + 10)  # shared by the scenarios of one run
    a = CaptureRecorder(tmp_path / "c3", "A", workflow, CapturePolicy(), budget)
    b = CaptureRecorder(tmp_path / "c3", "B", workflow, CapturePolicy(), budget)
    assert a.take(page, step_id="s1", phase="after").get("skipped") is None
    assert b.take(page, step_id="s1", phase="after")["skipped"] == "limit" and budget.used == len(JPEG)
    assert sorted(p.name for p in (tmp_path / "c3").iterdir()) == ["A-001-s1-after.jpg"]


def test_a_failing_capture_is_recorded_and_never_raises(tmp_path, workflow):
    rec = recorder(tmp_path, workflow)
    page = FakePage(fail=True)
    assert rec.take(page, step_id="s1", phase="after")["skipped"] == "error"
    page.fail = False
    meta = rec.take(page, step_id="s6", phase="before", target=FakeLocator(fail=True))
    assert meta.get("skipped") is None and meta["box"] is None  # the context box is optional


def test_adapter_captures_the_missing_element_before_recovery(monkeypatch, workflow):
    events, snaps = [], []
    adapter = PlaywrightWebAdapter(UrlPolicy([BASE]), healer=healer(meta(FakeMeta(chat(propose())))))

    def missing(page, target):
        raise not_found()

    monkeypatch.setattr(adapter, "_resolve", missing)
    monkeypatch.setattr(playwright_web, "PlaywrightCandidateSource", lambda page, timeout_ms: FakeSource(variant_page()))
    notify = lambda event, payload: events.append(event)  # noqa: E731
    snap = lambda page, step_id, phase, target=None: snaps.append((step_id, phase)) or events.append("snap")  # noqa: E731
    values = {"contrasena": DEMO_PASSWORD}
    result = adapter._run_step(None, workflow, step(workflow, "s6"), values, UrlPolicy([BASE]), [DEMO_PASSWORD], RunState(), notify, snap)
    assert result.resolution == "ai_recovery"
    assert snaps == [("s6", "not_found")] and events == ["snap", "recovery_started"]


def test_retention_keeps_recent_runs_only(tmp_path):
    root = tmp_path / "captures"
    for n, name in enumerate(["old", "mid", "new"]):
        (root / name).mkdir(parents=True)
        (root / name / "x.jpg").write_bytes(JPEG)
        if name != "old":
            (root / name / "notes.txt").write_text("not ours")  # never deleted
        os.utime(root / name, (time.time() - 100 + n, time.time() - 100 + n))
    assert prune_captures(root, 1, live={"mid"}) == ["old"]
    assert sorted(p.name for p in root.iterdir()) == ["mid", "new"]
    assert prune_captures(tmp_path / "missing", 1) == []


def test_runner_never_stores_captures_in_versioned_paths(tmp_path):
    assert demo_healing.captures_allowed(REPO_ROOT / "demo-evidence")
    assert demo_healing.captures_allowed(tmp_path)  # outside the repository
    assert not demo_healing.captures_allowed(REPO_ROOT / "docs" / "evidence")
    assert not demo_healing.captures_allowed(REPO_ROOT / "backend")
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^/?demo-evidence/?$", ignored, re.M)


# -- evidence and the image endpoint ------------------------------------------------------------


def _capture(cid, step_id="s1", **kw) -> dict:
    return {"id": cid, "step_id": step_id, "phase": "after", "taken_at": datetime.now(UTC).isoformat(), "page": f"{BASE}/", **kw}


def test_capture_events_are_validated_and_capped(workflow):
    sc = bundle_for(workflow).scenarios[0]
    apply_event(sc, "capture", _capture("C-001-s1-after", width=1280, height=720, bytes=10))
    assert sc.captures[0].step_id == "s1"
    for bad in ["../x", "a/b", "", "x" * 81]:
        with pytest.raises(ValidationError):
            apply_event(sc, "capture", _capture(bad))
    with pytest.raises(ValidationError):
        apply_event(sc, "capture", _capture("C-2", phase="during"))
    for n in range(MAX_CAPTURES + 5):
        apply_event(sc, "capture", _capture(f"C-{n}"))
    assert len(sc.captures) == MAX_CAPTURES


def _run_with_captures(tmp_path, workflow, name="run1"):
    bundle = bundle_for(workflow, state="finished")
    sc = bundle.scenarios[0]
    apply_event(sc, "capture", _capture("C-001-s1-after", bytes=len(JPEG)))
    apply_event(sc, "capture", _capture("C-002-s3-before", step_id="s3", skipped="sensitive_step", page=None))
    write_bundle(tmp_path / "runs" / f"{name}.json", bundle)
    folder = tmp_path / "runs" / "captures" / name
    folder.mkdir(parents=True)
    (folder / "C-001-s1-after.jpg").write_bytes(JPEG)
    (folder / "C-002-s3-before.jpg").write_bytes(JPEG)  # a file the bundle says was never taken
    (folder / "C-009-s9-after.jpg").write_bytes(JPEG)  # a file the bundle does not list
    return folder


def test_endpoint_serves_only_listed_captures_of_demo_runs(tmp_path, workflow):
    _run_with_captures(tmp_path, workflow)
    with make_client(tmp_path) as c:
        r = c.get("/api/demo/captures/run1/C-001-s1-after")
        assert r.status_code == 200 and r.content == JPEG
        assert r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
        for url in [
            "/api/demo/captures/run1/C-002-s3-before",  # skipped (sensitive): never served
            "/api/demo/captures/run1/C-009-s9-after",  # not listed
            "/api/demo/captures/run1/..%2F..%2Frun1",
            "/api/demo/captures/..%2Frun1/C-001-s1-after",
            "/api/demo/captures/gf003-meta-recovery/C-001-s1-after",  # recorded evidence has no captures
            "/api/demo/captures/missing/C-001-s1-after",
        ]:
            r = c.get(url)
            assert r.status_code == 404, url
            assert r.headers["cache-control"] == "no-store"
        assert c.post("/api/demo/captures/run1/C-001-s1-after").status_code == 405  # read-only
        assert c.delete("/api/demo/captures/run1/C-001-s1-after").status_code == 405


def test_oversized_or_replaced_capture_files_are_not_served(tmp_path, workflow):
    folder = _run_with_captures(tmp_path, workflow)
    (folder / "C-001-s1-after.jpg").write_bytes(b"0" * 1_000_001)
    with make_client(tmp_path) as c:
        assert c.get("/api/demo/captures/run1/C-001-s1-after").status_code == 404
    (folder / "C-001-s1-after.jpg").unlink()
    (folder / "C-001-s1-after.jpg").symlink_to(REPO_ROOT / "README.md")
    with make_client(tmp_path) as c:
        assert c.get("/api/demo/captures/run1/C-001-s1-after").status_code == 404


def test_recorded_gf003_evidence_has_no_captures():
    from test_demo_console import RECORDED_DIR

    from ghostflow.evidence import EvidenceStore

    bundle = EvidenceStore(RECORDED_DIR, RECORDED_DIR).load("recorded", "gf003-meta-recovery")
    assert all(sc.captures == [] for sc in bundle.scenarios)


# -- console: provenance, moods, accessibility -------------------------------------------------


def _static():
    return tuple((STATIC_DIR / f).read_text(encoding="utf-8") for f in ["index.html", "console.js", "console.css"])


def test_console_labels_every_browser_view_by_provenance():
    html, js, _ = _static()
    for label in ["Live capture", "Recorded capture", "Reconstructed view", "Simulated"]:
        assert label in js, label
    assert "Replay of recorded captures · not a live execution" in js
    assert "real screenshot saved during this run, not a live view" in js
    assert "/captures/${encodeURIComponent(state.detail.name)}/${encodeURIComponent(c.id)}" in js
    # Images are <img> elements created by script from listed ids, never markup or a browser session.
    assert "<iframe" not in html and "iframe" not in js and "WebSocket" not in js and "EventSource" not in js
    assert re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML", js) is None
    assert 'id="view-toggle"' in html and 'aria-pressed="true"' in html


def test_ghost_moods_follow_real_events_and_never_invent_model_activity():
    html, js, css = _static()
    assert {"investigating", "evaluating", "cautious"} <= set(re.findall(r'return "([a-z]+)"', js))
    for mood in ["working", "investigating", "evaluating", "proud", "happy", "alert", "cautious"]:
        assert f'data-mood="{mood}"' in css, mood
        assert mood in js
    # Investigating only after recovery_started; evaluating only in the playback of recorded checks.
    assert 'step.phase === "recovering") return "investigating"' in js
    assert 'phase: state.replay.stage === 1 ? "evaluating" : "recovering"' in js
    assert len(re.findall(r'phase: [^,}]*"evaluating"', js)) == 1  # assigned by the replay only
    assert "playback pace, not the real duration" in js
    for invented in ["thinking", "Asking AI", "generating candidates", "Recovering with AI"]:
        assert invented not in js, invented
    # One-shot celebration, and a rejected proposal never celebrates.
    assert re.search(r'data-mood="proud"\] \.float \{ animation: jump [^;]* 1,', css)
    assert '["rejected", "error"].includes(step.recovery.outcome)) return "cautious"' in js
    cautious = re.findall(r'data-mood="cautious"\][^{]*\{[^}]*\}', css)
    assert cautious and not any("sparkles" in rule or "jump" in rule for rule in cautious)
    for part in ["arm-l", "arm-r", "brows-focus", "brows-worried", "lens", "ticks", "eyes-open"]:
        assert f'class="{part}' in html or f'class="arm {part}"' in html, part


def test_ghost_is_accessible_and_respects_reduced_motion():
    html, js, css = _static()
    assert '<p class="sr-only" id="agent-status" role="status" aria-live="polite"></p>' in html
    assert "if (state.lastMood !== mood)" in js  # announced on change, not on every poll
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)") :]
    assert "animation: none" in reduced and "transition: none" in reduced
    assert "MOOD_LABEL[mood]" in js  # every state is also written as text, not only colour or motion


def test_switching_evidence_never_keeps_the_previous_view():
    _, js, _ = _static()
    # The address selects the evidence; a hash change (link, goto, back) switches source.
    assert 'window.addEventListener("hashchange"' in js and "if (key && key !== state.current)" in js
    # Every source starts on its real captures; the toggle only shows when the evidence has some.
    select_fn = js[js.index("async function select(key)") : js.index("// Arrow keys move between tabs")]
    for reset in ['state.stageView = "capture";', "state.pinned = null;", "state.shot = null;", "stopReplay();"]:
        assert reset in select_fn, reset
    assert '$("view-toggle").hidden = !available;' in js
    # A slow answer for a source the user already left is dropped, never rendered.
    load = js[js.index("async function loadDetail()") : js.index("async function select(key)")]
    assert "if (key !== state.current) return;" in load
    assert load.index("if (key !== state.current) return;") < load.index("state.detail = detail;")


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_rehearsal_is_integrated_by_default_and_headed_only_for_diagnosis():
    def dry_run(*extra):
        out = subprocess.run(["make", "-n", "-C", str(REPO_ROOT), *extra], capture_output=True, text=True, check=True)
        return out.stdout

    for target in ["demo-rehearsal", "demo-live"]:
        assert "ghostflow.demo_healing" in dry_run(target) and "--headed" not in dry_run(target), target
        assert "--headed" in dry_run(target, "DEMO_HEADED=1"), target
    assert "--provider mock" in dry_run("demo-rehearsal") and "--no-captures" not in dry_run("demo-rehearsal")


def test_live_moments_show_only_real_events_the_console_observed():
    _, js, _ = _static()
    notice = js[js.index("function noticeLive(key)") : js.index("function addMoment(")]
    # Only for live runs, only from a step the console saw pending/running that then finished.
    assert "if (!b || (!isLive()" in notice and "/^(pending|running)\\//.test(was)" in notice
    assert 's.state === "recovered"' in notice and 's.state === "failed"' in notice
    # Investigating is shown only for a real recovery (recovered); B (healing off) is a safe stop.
    assert notice.index('addMoment(sc.key, i, "investigating")') > notice.index('if (s.state === "recovered")')
    assert notice.index('addMoment(sc.key, i, "investigating")') < notice.index('} else if (s.state === "failed")')
    # Bounded, never slowing the run, worded in the past tense, cleared by any user choice.
    assert "if (state.moments.length >= MAX_MOMENTS) return;" in js and "const MAX_MOMENTS = 4;" in js
    assert 'investigating: "Recovery started · recorded element missing"' in js
    for fn in ["async function select(key)", "function chooseScenario(key)"]:
        body = js[js.index(fn) : js.index("\n}\n", js.index(fn))]
        assert "clearMoments()" in body, fn
    assert "Live capture · just observed at step" in js
    assert "stopped safely without acting" in js and "Page where the run stopped safely" in js

"""Replay a recorded workflow in a real browser against the demo portal and verify it."""

import re

import pytest
from fastapi.testclient import TestClient
from scenario import DEMO_PASSWORD, demo_recording

from ghostflow.api import create_app
from ghostflow.config import Settings

pytestmark = pytest.mark.e2e


def _client(tmp_path, origin: str) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'e2e.db'}",
        allowed_origins=origin,
        step_timeout_ms=5_000,
    )
    return TestClient(create_app(settings))


def _record_compile_run(client: TestClient, origin: str, password: str = DEMO_PASSWORD) -> dict:
    rec = client.post("/api/recordings", json=demo_recording(origin))
    assert rec.status_code == 201, rec.text
    wf = client.post(f"/api/recordings/{rec.json()['id']}/workflow", json={})
    assert wf.status_code == 201, wf.text
    run = client.post(f"/api/workflows/{wf.json()['id']}/runs", json={"inputs": {"contrasena": password}})
    assert run.status_code == 201, run.text
    return run.json()


def test_replay_passes_and_is_verified(tmp_path, portal_url):
    with _client(tmp_path, portal_url) as client:
        run = _record_compile_run(client, portal_url)
    result = run["result"]
    assert run["status"] == "passed", result
    assert all(s["status"] == "passed" for s in result["steps"])
    assert [v["status"] for v in result["verifications"]] == ["passed", "passed"]
    assert not any(s["healed"] for s in result["steps"])
    assert result["blocked_requests"] == []
    assert DEMO_PASSWORD not in str(run)


def test_replay_survives_ui_changes(tmp_path, serve_dir, portal_copy):
    """Simulate a redesign: test ids removed, ids renamed, button text unchanged."""
    for page in portal_copy.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        html = re.sub(r'\sdata-testid="[^"]*"', "", html)
        html = re.sub(r'\b(id|for)="([^"]+)"', r'\1="\2-v2"', html)
        page.write_text(html, encoding="utf-8")
    origin = serve_dir(portal_copy)

    with _client(tmp_path, origin) as client:
        run = _record_compile_run(client, origin)
    result = run["result"]
    assert run["status"] == "passed", result
    healed = [s for s in result["steps"] if s["healed"]]
    assert healed, "expected fallback locator strategies to be used"
    assert all(s["strategy_by"] != "test_id" for s in result["steps"] if s["strategy_by"])


def test_wrong_password_fails_verification(tmp_path, portal_url):
    with _client(tmp_path, portal_url) as client:
        run = _record_compile_run(client, portal_url, password="not-the-password")
    result = run["result"]
    assert run["status"] == "failed"
    failed = [s for s in result["steps"] if s["status"] == "failed"]
    assert failed[0]["action"] == "wait_for_url"
    assert all(v["status"] == "skipped" for v in result["verifications"])
    assert "not-the-password" not in str(run)


def test_requests_outside_allowlist_are_blocked(tmp_path, serve_dir, portal_copy):
    dashboard = portal_copy / "dashboard.html"
    html = dashboard.read_text(encoding="utf-8").replace(
        "</main>",
        '<img src="http://169.254.169.254/latest/meta-data/" alt=""><img src="http://localhost:9/track.gif" alt=""></main>',
    )
    dashboard.write_text(html, encoding="utf-8")
    origin = serve_dir(portal_copy)

    with _client(tmp_path, origin) as client:
        run = _record_compile_run(client, origin)
    result = run["result"]
    assert run["status"] == "passed", result
    blocked = result["blocked_requests"]
    assert "http://169.254.169.254/latest/meta-data/" in blocked
    assert "http://localhost:9/track.gif" in blocked

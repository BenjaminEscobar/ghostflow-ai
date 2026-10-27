"""API tests with a fake execution adapter (no browser needed)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from scenario import DEMO_PASSWORD, demo_recording

from ghostflow.api import create_app
from ghostflow.config import Settings
from ghostflow.execution import RunResult, resolve_inputs

BASE = "http://localhost:8080"


class FakeWebAdapter:
    kind = "web"

    def __init__(self):
        self.calls = []

    def execute(self, workflow, inputs):
        resolve_inputs(workflow, inputs)  # same input contract as the real adapter
        self.calls.append((workflow, dict(inputs)))
        now = datetime.now(UTC)
        return RunResult(adapter="web", status="passed", started_at=now, finished_at=now)


@pytest.fixture
def adapter():
    return FakeWebAdapter()


@pytest.fixture
def client(adapter, tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", allowed_origins=BASE)
    app = create_app(settings, adapters=lambda s, p: {"web": adapter})
    with TestClient(app) as c:
        yield c


def _record_and_compile(client) -> dict:
    rec = client.post("/api/recordings", json=demo_recording(BASE, leak_password=True))
    assert rec.status_code == 201, rec.text
    wf = client.post(f"/api/recordings/{rec.json()['id']}/workflow", json={})
    assert wf.status_code == 201, wf.text
    return wf.json()


def test_health_reports_mock_provider(client):
    assert client.get("/api/health").json()["ai_provider"] == "mock"


def test_recording_is_stored_without_secrets(client):
    data = demo_recording(BASE, leak_password=True)
    created = client.post("/api/recordings", json=data).json()
    assert created["event_count"] == len(data["events"])
    stored = client.get(f"/api/recordings/{created['id']}")
    assert stored.status_code == 200
    assert DEMO_PASSWORD not in stored.text
    assert any(e["sensitive"] for e in stored.json()["recording"]["events"])
    assert client.get("/api/recordings").json()[0]["id"] == created["id"]


def test_recording_outside_allowlist_is_rejected(client):
    data = demo_recording("http://intranet.local")
    response = client.post("/api/recordings", json=data)
    assert response.status_code == 422


def test_malformed_recording_is_rejected(client):
    data = demo_recording(BASE)
    data["events"][0]["type"] = "keylogger"
    assert client.post("/api/recordings", json=data).status_code == 422
    data = demo_recording(BASE)
    data["unexpected"] = True
    assert client.post("/api/recordings", json=data).status_code == 422


def test_compile_produces_annotated_workflow(client):
    wf = _record_and_compile(client)
    assert wf["annotated_by"] == "mock"
    assert wf["definition"]["dsl_version"] == "0.1"
    assert wf["definition"]["description"]
    assert client.get(f"/api/workflows/{wf['id']}").json()["definition"] == wf["definition"]
    assert DEMO_PASSWORD not in client.get(f"/api/workflows/{wf['id']}").text


def test_run_requires_sensitive_inputs_and_never_stores_them(client, adapter):
    wf = _record_and_compile(client)
    missing = client.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {}})
    assert missing.status_code == 422 and "contrasena" in missing.text

    run = client.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {"contrasena": DEMO_PASSWORD}})
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["inputs"] == {"contrasena": "***"}
    assert adapter.calls[-1][1] == {"contrasena": DEMO_PASSWORD}

    stored = client.get(f"/api/runs/{run.json()['id']}")
    assert DEMO_PASSWORD not in stored.text
    assert [r["id"] for r in client.get(f"/api/workflows/{wf['id']}/runs").json()] == [run.json()["id"]]


def test_unknown_inputs_are_rejected(client):
    wf = _record_and_compile(client)
    r = client.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {"contrasena": "x", "rm_rf": "/"}})
    assert r.status_code == 422


def test_create_workflow_validates_dsl_and_server_allowlist(client):
    wf = _record_and_compile(client)["definition"]
    assert client.post("/api/workflows", json=wf).status_code == 201

    other_origin = dict(wf, allowed_origins=["http://evil.test"])
    other_origin["steps"] = [{"id": "s1", "action": "navigate", "url": "http://evil.test/"}]
    other_origin["inputs"] = []
    assert client.post("/api/workflows", json=other_origin).status_code == 422

    scripted = dict(wf, steps=[{"id": "s1", "action": "evaluate", "script": "fetch('/')"}])
    assert client.post("/api/workflows", json=scripted).status_code == 422


def test_schema_endpoint(client):
    schema = client.get("/api/schema/workflow").json()
    assert schema["title"] == "Workflow"


def test_not_found(client):
    assert client.get("/api/workflows/nope").status_code == 404
    assert client.post("/api/recordings/nope/workflow", json={}).status_code == 404
    assert client.get("/api/runs/nope").status_code == 404

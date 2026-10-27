"""Full vertical slice: the real Chrome extension records a demo session, uploads it to a
live backend, the backend compiles it into a workflow and replays it with Playwright."""

import json
import re
import shutil
import socket
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn
from conftest import EXTENSION_DIR
from playwright.sync_api import sync_playwright
from scenario import CUSTOMER, DEMO_PASSWORD, DEMO_USER, DESCRIPTION, EMAIL, SUCCESS_TEXT

from ghostflow.api import create_app
from ghostflow.config import Settings

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def backend_url(tmp_path, portal_url):
    port = _free_port()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ext.db'}", allowed_origins=portal_url, step_timeout_ms=5_000
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


def _build_extension(dst, backend: str, origin: str):
    """Copy the extension, pointing it at the test backend and portal ports."""
    shutil.copytree(EXTENSION_DIR, dst)
    config = (dst / "config.js").read_text(encoding="utf-8")
    config = re.sub(r'backendUrl: "[^"]+"', f'backendUrl: "{backend}"', config)
    config = re.sub(r"allowedOrigins: Object\.freeze\(\[[^\]]*\]\)", f'allowedOrigins: Object.freeze(["{origin}"])', config)
    (dst / "config.js").write_text(config, encoding="utf-8")
    manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    manifest["host_permissions"] = [f"{origin}/*", f"{backend}/*"]
    manifest["content_scripts"][0]["matches"] = [f"{origin}/*"]
    (dst / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dst


def test_extension_records_and_backend_replays(tmp_path, portal_url, backend_url):
    ext = _build_extension(tmp_path / "extension", backend_url, portal_url)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            tempfile.mkdtemp(dir=tmp_path),
            channel="chromium",
            headless=True,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"],
        )
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            extension_id = worker.url.split("/")[2]

            popup = context.new_page()
            popup.goto(f"chrome-extension://{extension_id}/popup.html")
            popup.fill("#name", "Registrar solicitud (E2E)")
            popup.click("#start")
            popup.wait_for_selector('#state[data-state="recording"]')

            page = context.new_page()
            page.goto(f"{portal_url}/index.html")
            page.wait_for_timeout(500)  # content script asks the worker for the recording state
            page.get_by_label("Usuario", exact=True).fill(DEMO_USER)
            page.get_by_label("Contraseña", exact=True).fill(DEMO_PASSWORD)
            page.get_by_role("button", name="Ingresar").click()
            page.wait_for_url("**/dashboard.html")
            page.wait_for_timeout(300)
            page.get_by_role("link", name="Nueva solicitud").click()
            page.wait_for_url("**/new-request.html")
            page.wait_for_timeout(300)
            page.get_by_label("Nombre del cliente", exact=True).fill(CUSTOMER)
            page.get_by_label("Correo del cliente", exact=True).fill(EMAIL)
            page.get_by_label("Categoría", exact=True).select_option("facturacion")
            page.get_by_label("Alta", exact=True).check()
            page.get_by_label("Descripción", exact=True).fill(DESCRIPTION)
            page.get_by_label("Notificar al cliente por correo", exact=True).check()
            page.get_by_role("button", name="Crear solicitud").click()
            page.wait_for_url("**/confirmation.html*")
            page.get_by_role("status").filter(has_text=SUCCESS_TEXT).wait_for()
            page.wait_for_timeout(1_000)  # status-region observer is debounced

            popup.bring_to_front()
            popup.click("#stop")
            popup.wait_for_selector('#upload[data-status="uploaded"]', timeout=15_000)
            recording_id = popup.get_attribute("#upload", "data-recording-id")
        finally:
            context.close()

    with httpx.Client(base_url=backend_url, timeout=120) as api:
        recording = api.get(f"/api/recordings/{recording_id}")
        assert recording.status_code == 200
        assert DEMO_PASSWORD not in recording.text
        events = recording.json()["recording"]["events"]
        kinds = [e["type"] for e in events]
        assert kinds[0] == "navigation"
        assert {"input", "select", "check", "click", "observation"} <= set(kinds)
        assert any(e["sensitive"] and e["value"] is None for e in events if e["type"] == "input")

        workflow = api.post(f"/api/recordings/{recording_id}/workflow", json={})
        assert workflow.status_code == 201, workflow.text
        definition = workflow.json()["definition"]
        assert {"type": "text_visible", "text": SUCCESS_TEXT} in definition["verify"]
        secret_inputs = [i["name"] for i in definition["inputs"] if i["sensitive"]]
        assert len(secret_inputs) == 1

        run = api.post(
            f"/api/workflows/{workflow.json()['id']}/runs", json={"inputs": {secret_inputs[0]: DEMO_PASSWORD}}
        )
        assert run.status_code == 201, run.text
        assert run.json()["status"] == "passed", json.dumps(run.json()["result"], indent=2)
        assert DEMO_PASSWORD not in run.text

"""Regression tests for the test-suite network guard (tests/conftest.py): local servers a test
starts are reachable once registered; everything else, Meta first of all, stays blocked."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from conftest import RealNetworkBlocked
from pydantic import SecretStr

from ghostflow.ai import MetaModelProvider, provider_from_settings
from ghostflow.ai.annotate import WorkflowAnnotation
from ghostflow.config import Settings


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_registered_local_server_is_reachable(local_server, local_http):
    local_http.allow(local_server)
    assert httpx.get(f"{local_server}/health").json() == {"ok": True}
    with httpx.Client(base_url=local_server) as client:
        assert client.get("/x").status_code == 200


def test_unregistered_local_server_is_blocked(local_server):
    with pytest.raises(RealNetworkBlocked):
        httpx.get(f"{local_server}/health")


def test_registration_is_exact_host_and_port(local_server, local_http):
    local_http.allow(local_server)
    port = int(local_server.rsplit(":", 1)[1])
    with pytest.raises(RealNetworkBlocked):
        httpx.get(f"http://127.0.0.1:{port + 1 if port < 65535 else port - 1}/")  # other port
    with pytest.raises(RealNetworkBlocked):
        httpx.get(f"http://localhost:{port}/")  # other host spelling
    with pytest.raises(RealNetworkBlocked):
        httpx.get(f"https://127.0.0.1:{port}/")  # other scheme


@pytest.mark.parametrize(
    "url", ["https://api.meta.ai:443", "http://example.com:80", "http://169.254.169.254:80", "http://127.0.0.1", "ftp://127.0.0.1:21"]
)
def test_only_loopback_endpoints_with_explicit_port_can_be_registered(local_http, url):
    with pytest.raises(ValueError):
        local_http.allow(url)


@pytest.mark.parametrize("url", ["https://api.meta.ai/v1/chat/completions", "https://example.com/", "http://169.254.169.254/"])
def test_external_hosts_stay_blocked(url, local_server, local_http):
    local_http.allow(local_server)  # an allowed local server does not open anything else
    with pytest.raises(RealNetworkBlocked):
        httpx.get(url)


def test_async_transport_is_guarded_too(local_server, local_http):
    async def fetch(url):
        async with httpx.AsyncClient() as client:
            return await client.get(url)

    with pytest.raises(RealNetworkBlocked):
        asyncio.run(fetch("https://api.meta.ai/v1/models"))
    local_http.allow(local_server)
    assert asyncio.run(fetch(f"{local_server}/")).status_code == 200


def test_real_meta_provider_cannot_reach_meta(local_server, local_http):
    local_http.allow(local_server)
    provider = MetaModelProvider(api_key=SecretStr("not-a-real-key"), max_retries=0, max_requests=1)
    with pytest.raises(RealNetworkBlocked):
        provider.generate_structured(task="t", instruction="i", payload={}, response_model=WorkflowAnnotation)
    assert provider.requests_sent == 1  # the attempt was counted and refused, nothing left the machine


def test_mock_transport_is_unaffected():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"mock": True}))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://api.meta.ai/v1/models").json() == {"mock": True}


def test_dotenv_and_ghostflow_variables_are_ignored(tmp_path, monkeypatch):
    # A developer's backend/.env with a real-looking Meta configuration must not be loaded.
    (tmp_path / ".env").write_text(
        "GHOSTFLOW_AI_PROVIDER=meta\nGHOSTFLOW_META_API_KEY=key-from-dotenv\nGHOSTFLOW_HEALING_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.ai_provider == "mock" and settings.meta_api_key is None and settings.healing_enabled is False
    assert provider_from_settings(settings).name == "mock"
    assert json.dumps(settings.model_dump(mode="json")).count("key-from-dotenv") == 0

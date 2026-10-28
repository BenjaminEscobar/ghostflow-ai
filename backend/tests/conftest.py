import functools
import os
import shutil
import threading
from collections.abc import Callable, Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from ghostflow.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


class RealNetworkBlocked(RuntimeError):
    pass


class LocalHttpAllowlist:
    """Exact loopback endpoints (scheme http, host, port) that tests started themselves.

    Empty by default: a test fixture that launches a local server registers that server's own
    address, and only for the duration of that test."""

    LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

    def __init__(self) -> None:
        self._endpoints: set[tuple[str, int]] = set()

    def allow(self, base_url: str) -> None:
        url = httpx.URL(base_url)
        if url.scheme != "http" or url.host not in self.LOOPBACK_HOSTS or url.port is None:
            raise ValueError("Only http://<loopback>:<explicit port> endpoints started by a test can be allowed")
        self._endpoints.add((url.host, url.port))

    def permits(self, url: httpx.URL) -> bool:
        return url.scheme == "http" and (url.host, url.port) in self._endpoints


@pytest.fixture(autouse=True)
def offline_only(monkeypatch) -> LocalHttpAllowlist:
    """Tests never spend Meta credits: ignore any local .env / GHOSTFLOW_* variables (a developer's
    backend/.env may hold a real key) and refuse every real outgoing httpx request, except to the
    exact loopback endpoints a fixture registered (see `local_http`). Meta is only ever reached
    through httpx.MockTransport in tests."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in [n for n in os.environ if n.startswith("GHOSTFLOW_")]:
        monkeypatch.delenv(name)
    # The suite controls its network transports; inherited proxies must not bypass that guard
    # or require optional SOCKS packages before the guard can even run.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    allowlist = LocalHttpAllowlist()
    real_sync = httpx.HTTPTransport.handle_request
    real_async = httpx.AsyncHTTPTransport.handle_async_request

    def blocked(request: httpx.Request) -> RealNetworkBlocked:
        return RealNetworkBlocked(f"Real network access is disabled in tests ({request.url.host})")

    def guarded_sync(self, request):
        if allowlist.permits(request.url):
            return real_sync(self, request)
        raise blocked(request)

    async def guarded_async(self, request):
        if allowlist.permits(request.url):
            return await real_async(self, request)
        raise blocked(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
    return allowlist


@pytest.fixture
def local_http(offline_only) -> LocalHttpAllowlist:
    """For fixtures that start a local HTTP server and talk to it with httpx: call
    `local_http.allow(base_url)` with that server's own address."""
    return offline_only


PORTAL_DIR = REPO_ROOT / "demo-portal"
EXTENSION_DIR = REPO_ROOT / "extension"
SCHEMA_FILE = REPO_ROOT / "schemas" / "workflow.schema.json"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def serve_dir() -> Iterator[Callable[[Path], str]]:
    """Serve a directory over HTTP on a random local port; returns the base URL."""
    servers: list[ThreadingHTTPServer] = []

    def start(directory: Path) -> str:
        handler = functools.partial(_QuietHandler, directory=str(directory))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def portal_url(serve_dir) -> str:
    return serve_dir(PORTAL_DIR)


@pytest.fixture
def portal_copy(tmp_path) -> Path:
    """A writable copy of the demo portal, for tests that simulate UI changes."""
    dst = tmp_path / "portal"
    shutil.copytree(PORTAL_DIR, dst)
    return dst

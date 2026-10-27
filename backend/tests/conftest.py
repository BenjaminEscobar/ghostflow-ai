import functools
import shutil
import threading
from collections.abc import Callable, Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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

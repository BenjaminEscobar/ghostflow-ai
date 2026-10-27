"""Static checks on the Chrome extension: permissions stay minimal and consistent."""

import json
import re
import shutil
import subprocess

import pytest
from conftest import EXTENSION_DIR

MANIFEST = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
CONFIG_JS = (EXTENSION_DIR / "config.js").read_text(encoding="utf-8")


def _config_origins() -> list[str]:
    block = re.search(r"allowedOrigins:\s*Object\.freeze\(\[(.*?)\]\)", CONFIG_JS, re.S)
    return re.findall(r'"([^"]+)"', block.group(1))


def _backend_url() -> str:
    return re.search(r'backendUrl:\s*"([^"]+)"', CONFIG_JS).group(1)


def test_manifest_v3_with_minimal_permissions():
    assert MANIFEST["manifest_version"] == 3
    assert set(MANIFEST["permissions"]) <= {"storage"}
    assert "externally_connectable" not in MANIFEST
    for pattern in MANIFEST["host_permissions"] + MANIFEST["content_scripts"][0]["matches"]:
        assert "<all_urls>" not in pattern and "*://" not in pattern and "://*" not in pattern


def test_content_scripts_only_on_allowed_origins():
    matches = MANIFEST["content_scripts"][0]["matches"]
    assert sorted(matches) == sorted(f"{o}/*" for o in _config_origins())
    assert MANIFEST["content_scripts"][0]["all_frames"] is False


def test_host_permissions_cover_backend_and_origins_only():
    expected = {f"{o}/*" for o in _config_origins()} | {f"{_backend_url()}/*"}
    assert expected <= set(MANIFEST["host_permissions"])
    extra = set(MANIFEST["host_permissions"]) - expected
    # Only the 127.0.0.1 alias of the backend is allowed beyond that.
    assert extra <= {_backend_url().replace("localhost", "127.0.0.1") + "/*"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["background.js", "content.js", "shared.js", "config.js", "popup.js"])
def test_extension_scripts_parse(name):
    subprocess.run(["node", "--check", str(EXTENSION_DIR / name)], check=True, capture_output=True)

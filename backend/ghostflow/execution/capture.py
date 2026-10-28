"""Browser captures for the Demo Console (GF-006): real screenshots of the Chromium page that
GhostFlow controls, taken at a few points of a run and stored next to the demo evidence.

Privacy is enforced *before* an image exists, never by editing it afterwards:
- only pages on a loopback origin that the workflow allows are captured (the synthetic demo
  portal); anything else is skipped, so no arbitrary site is ever photographed;
- a step that types a sensitive input is never captured, and nothing more is captured on that
  page until the browser reaches another page;
- password-type fields are masked by Playwright while the screenshot is taken;
- only the page viewport is captured (page.screenshot), never other tabs, windows or the OS.

Resources are bounded: image count per scenario, total bytes per run, captures per second,
maximum size of each file and viewport clip. A failed capture is recorded as skipped and never
affects the run: the adapter ignores any error raised here.
"""

import logging
import fcntl
import os
import re
import stat
import threading
import time
import uuid
from collections import deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ghostflow.dsl.models import FillStep, SelectStep, Workflow
from ghostflow.dsl.templating import template_references
from ghostflow.security.files import artifact_dir
from ghostflow.security.urls import UrlPolicy

log = logging.getLogger("ghostflow.capture")

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CAPTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
# Fields hidden by Playwright during the screenshot itself (it overlays them before encoding).
MASK_SELECTOR = (
    'input[type="password" i], [autocomplete*="password" i], [autocomplete*="cc-" i], '
    '[autocomplete*="one-time-code" i], input[name*="token" i], input[name*="secret" i], '
    'input[name*="api_key" i], textarea[name*="token" i], textarea[name*="secret" i], '
    '[data-private], iframe, frame'
)
MASK_SELECTOR += ", " + ", ".join(
    f':is(input,textarea,select,[contenteditable])[{attribute}*="{name}" i]'
    for attribute in ("id", "name", "aria-label")
    for name in ("pass", "contrase", "token", "secret", "api_key", "api-key", "otp", "pin", "credential", "session", "auth", "cookie", "card", "tarjeta", "iban")
)


@dataclass(frozen=True)
class CapturePolicy:
    max_per_scenario: int = 40
    max_bytes_per_run: int = 25_000_000
    max_bytes_per_image: int = 600_000
    max_per_second: int = 10  # sliding window; no waits are ever added to the run
    max_width: int = 1440
    max_height: int = 900
    jpeg_quality: int = 70
    timeout_ms: int = 2_000


@dataclass
class CaptureBudget:
    """Shared by every scenario of one run, so the run as a whole stays bounded."""

    max_bytes: int
    used: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def is_capturable_url(url: str, allowed_origins: list[str]) -> bool:
    """Loopback and inside the workflow's allowlist: the local synthetic portal, nothing else."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or (parts.hostname or "") not in LOOPBACK_HOSTS:
        return False
    return UrlPolicy(allowed_origins).is_allowed(url)


def sensitive_steps(workflow: Workflow) -> set[str]:
    """Steps that type or choose a value coming from a sensitive input."""
    secret = {i.name for i in workflow.inputs if i.sensitive}
    return {
        s.id for s in workflow.steps
        if isinstance(s, (FillStep, SelectStep)) and template_references(s.value) & secret
    }  # fmt: skip


@dataclass
class CaptureRecorder:
    """Takes and stores the captures of one scenario. `take` returns the metadata the evidence
    bundle keeps (also for skipped captures, so the console can say why a step has no image)."""

    directory: Path
    prefix: str  # scenario key, part of every capture id
    workflow: Workflow
    policy: CapturePolicy = field(default_factory=CapturePolicy)
    budget: CaptureBudget | None = None
    _seq: int = 0
    _taken: int = 0
    _recent: deque = field(default_factory=deque)  # monotonic times of the last second
    _tainted: bool = False  # a sensitive value was typed on the current page
    _page: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self._sensitive = sensitive_steps(self.workflow)
        self._origins = list(self.workflow.allowed_origins)
        if self.budget is None:
            self.budget = CaptureBudget(self.policy.max_bytes_per_run)

    def _meta(self, step_id: str | None, phase: str, **extra) -> dict:
        self._seq += 1
        ident = f"{self.prefix}-{self._seq:03d}-{step_id or 'end'}-{phase}"
        if not CAPTURE_ID.fullmatch(ident):
            raise ValueError("Invalid capture identifier")
        return {"id": ident, "step_id": step_id, "phase": phase, "taken_at": datetime.now(UTC).isoformat(), **extra}

    def take(self, page, *, step_id: str | None, phase: str, target=None) -> dict:
        if self._page is None:
            self._page = page
            # Browser lifecycle event: wait_for_url/history.pushState alone is not a new document.
            page.on("domcontentloaded", self._new_document)
        if self._page is not page:
            return self._meta(step_id, phase, skipped="error")
        if step_id in self._sensitive:
            self._tainted = True
            return self._meta(step_id, phase, skipped="sensitive_step")
        if self._tainted:
            return self._meta(step_id, phase, skipped="sensitive_page")
        if self._taken >= self.policy.max_per_scenario:
            return self._meta(step_id, phase, skipped="limit")
        now = time.monotonic()
        while self._recent and now - self._recent[0] >= 1.0:
            self._recent.popleft()
        if len(self._recent) >= self.policy.max_per_second:
            return self._meta(step_id, phase, skipped="rate")
        if self.budget.used >= self.budget.max_bytes:
            return self._meta(step_id, phase, skipped="limit")
        url = page.url
        if not is_capturable_url(url, self._origins):
            return self._meta(step_id, phase, skipped="off_origin")
        try:
            self._recent.append(now)  # failures also consume the rate budget
            return self._shoot(page, url, step_id, phase, target)
        except Exception:  # noqa: BLE001 - a capture never breaks a run
            log.warning("capture failed at %s/%s", step_id, phase)  # browser errors may contain secrets
            return self._meta(step_id, phase, skipped="error")

    def _new_document(self) -> None:
        self._tainted = False

    def _shoot(self, page, url: str, step_id: str | None, phase: str, target) -> dict:
        viewport = page.viewport_size or {"width": self.policy.max_width, "height": self.policy.max_height}
        width = min(int(viewport["width"]), self.policy.max_width)
        height = min(int(viewport["height"]), self.policy.max_height)
        box = None
        if target is not None:
            try:
                raw = target.bounding_box(timeout=self.policy.timeout_ms)
            except Exception:  # noqa: BLE001 - the context box is optional
                raw = None
            if (raw and raw["width"] > 0 and raw["height"] > 0 and raw["x"] < width and raw["y"] < height
                    and raw["x"] + raw["width"] > 0 and raw["y"] + raw["height"] > 0):
                x, y = max(0.0, raw["x"]), max(0.0, raw["y"])
                box = {
                    "x": round(min(x / width, 1.0), 4),
                    "y": round(min(y / height, 1.0), 4),
                    "w": round((min(raw["x"] + raw["width"], width) - x) / width, 4),
                    "h": round((min(raw["y"] + raw["height"], height) - y) / height, 4),
                }
        mask = page.locator(MASK_SELECTOR)
        data = page.screenshot(
            type="jpeg", quality=self.policy.jpeg_quality, timeout=self.policy.timeout_ms,
            clip={"x": 0, "y": 0, "width": width, "height": height}, mask=[mask], mask_color="#d5dce7",
            scale="css", caret="initial",
        )  # fmt: skip
        if len(data) > self.policy.max_bytes_per_image:
            return self._meta(step_id, phase, skipped="too_large")
        meta = self._meta(
            step_id, phase, page=_page_address(url), width=width, height=height, bytes=len(data), box=box,
            masked=_count(mask),
        )  # fmt: skip
        with self.budget.lock:
            if self.budget.used + len(data) > self.budget.max_bytes:
                return self._meta(step_id, phase, skipped="limit")
            _write_atomic(self.directory / f"{meta['id']}.jpg", data)
            self.budget.used += len(data)
            self._taken += 1
        return meta


def _page_address(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"  # no query or fragment


def _count(locator) -> int:
    try:
        return int(locator.count())
    except Exception:  # noqa: BLE001
        return 0


def _write_atomic(path: Path, data: bytes) -> None:
    # The configured evidence directory is trusted; captures/<run> are not symlink aliases.
    with artifact_dir(path.parent.parent.parent, path.parent.parent.name, path.parent.name, create=True) as parent:
        tmp = f".tmp-{uuid.uuid4().hex}.jpg"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            # Unique ids are immutable, including across accidental recorder reuse.
            os.link(tmp, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.unlink(tmp, dir_fd=parent)


@contextmanager
def capture_session(root: Path, name: str):
    """Pin an active run against retention, including another runner process."""
    with ExitStack() as stack:
        try:
            folder = stack.enter_context(artifact_dir(root, name, create=True))
            fd = os.open(".active", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=folder)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("Browser captures disabled: capture directory is unavailable or unsafe")
            ready = False
        else:
            ready = True
        yield ready  # acquisition failure disables presentation, never the automation


def prune_captures(root: Path, keep: int, *, live: set[str] = frozenset()) -> list[str]:
    """Retention: keep the capture folders of the `keep` most recent runs (and any `live` ones)."""
    try:
        return _prune_captures(root, keep, live=live)
    except OSError:
        log.warning("Capture retention could not complete; the run is unaffected")
        return []


def _prune_captures(root: Path, keep: int, *, live: set[str]) -> list[str]:
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        return []
    removed = []
    with artifact_dir(root) as parent:
        folders = []
        for name in os.listdir(parent):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                folders.append((info.st_mtime, name))
        for _, name in sorted(folders, reverse=True)[max(0, keep):]:
            if name in live:
                continue
            try:
                with artifact_dir(root, name) as folder:
                    fd = os.open(".active", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        for item in os.listdir(folder):
                            info = os.stat(item, dir_fd=folder, follow_symlinks=False)
                            if stat.S_ISREG(info.st_mode) and (item.endswith(".jpg") or item.startswith(".tmp-")):
                                os.unlink(item, dir_fd=folder)
                        os.unlink(".active", dir_fd=folder)
                        os.rmdir(name, dir_fd=parent)
                        removed.append(name)
                    finally:
                        os.close(fd)
            except OSError:
                log.debug("capture folder retained (active or contains other files)")
    return removed

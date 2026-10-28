"""Web execution adapter backed by Playwright (Chromium).

Safety properties:
- Only DSL actions are executed; there is no path to page.evaluate or similar.
- Every browser request (documents, XHR, images...) is checked against the workflow's
  origin allowlist, itself a subset of the server allowlist. Others are aborted.
- Sensitive input values are scrubbed from error messages before they are returned.
- Self-healing (execution/recovery.py) only runs when every recorded locator found nothing,
  before the step acted, and can only act on a candidate GhostFlow extracted and validated.
- Browser captures (execution/capture.py) are optional and passive: they never act on the
  page, and a failing capture is ignored like a failing observer.
"""

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Route, WebSocketRoute, sync_playwright
from playwright.async_api import WebSocketRoute as AsyncWebSocketRoute

from ghostflow.dsl.models import (
    CheckStep,
    ClickStep,
    ElementText,
    FillStep,
    NavigateStep,
    PressStep,
    SelectStep,
    Target,
    TextVisible,
    UrlPath,
    WaitForUrlStep,
    Workflow,
)
from ghostflow.dsl.templating import render
from ghostflow.execution.base import RunResult, StepResult, VerificationResult, resolve_inputs
from ghostflow.execution.recovery import (
    SUBMITS_JS,
    ElementNotFound,
    Healer,
    PlaywrightCandidateSource,
    RecoveryPolicy,
    RunState,
    describe_strategy,
)
from ghostflow.security.urls import UrlPolicy

_POLL_INTERVAL_MS = 150
_MAX_ERROR_LEN = 500

log = logging.getLogger("ghostflow.execution")

# Progress callback for live views (the Demo Console): event name and a JSON-ready payload.
# Purely observational: it is called after decisions are made and its errors are ignored.
RunObserver = Callable[[str, dict], None]


class StepCapture(Protocol):
    """Optional browser captures (execution/capture.py). Passive: it may read the page and take a
    screenshot, never act on it; it returns the capture's metadata and its errors are ignored."""

    def take(self, page: Page, *, step_id: str | None, phase: str, target: Locator | None = None) -> dict: ...


class StepFailure(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class PlaywrightWebAdapter:
    kind = "web"

    def __init__(
        self,
        policy: UrlPolicy,
        *,
        headless: bool = True,
        step_timeout_ms: int = 10_000,
        step_delay_ms: int = 0,
        final_hold_ms: int = 0,
        healer: Healer | None = None,
    ):
        self._policy = policy
        self._headless = headless
        self._timeout = step_timeout_ms
        self._delay = step_delay_ms
        self._final_hold = final_hold_ms
        self._healer = healer or Healer(RecoveryPolicy(enabled=False), policy)

    # -- public -------------------------------------------------------------------------

    def execute(
        self, workflow: Workflow, inputs: Mapping[str, str], *, observer: RunObserver | None = None,
        capture: StepCapture | None = None,
    ) -> RunResult:  # fmt: skip
        started = _now()
        values = resolve_inputs(workflow, inputs)
        secrets = [values[i.name] for i in workflow.inputs if i.sensitive and values.get(i.name)]
        policy = self._policy.restricted_to(workflow.allowed_origins)

        steps: list[StepResult] = []
        verifications: list[VerificationResult] = []
        blocked: list[str] = []
        status = "passed"
        error: str | None = None

        def notify(event: str, payload: dict) -> None:
            if observer is None:
                return
            try:
                observer(event, payload)
            except Exception:  # an observer never changes the run
                log.warning("run observer failed on %s", event)

        def snap(page: Page, step_id: str | None, phase: str, target: Locator | None = None) -> None:
            if capture is None:
                return
            try:
                meta = capture.take(page, step_id=step_id, phase=phase, target=target)
            except Exception:  # a capture never changes the run
                log.warning("capture failed on %s/%s", step_id, phase)
                return
            if meta:
                notify("capture", meta)

        def guard(route: Route) -> None:
            url = route.request.url
            if policy.is_allowed(url):
                route.continue_()
            else:
                note_blocked(url)
                route.abort("blockedbyclient")

        def note_blocked(url: str) -> None:
            parts = urlsplit(url)
            if len(blocked) < 50:
                blocked.append(_scrub(f"{parts.scheme}://{parts.hostname or ''}"
                                      f"{':' + str(parts.port) if parts.port else ''}{parts.path}", secrets)[:300])

        def guard_socket(route: WebSocketRoute):
            # HTTP routing does not intercept WebSocket handshakes. Apply the same exact-origin
            # policy to their HTTP(S) counterpart before any upstream connection is made.
            parts = urlsplit(route.url)
            scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
            if policy.is_allowed(urlunsplit(parts._replace(scheme=scheme))):
                route.connect_to_server()
            else:
                note_blocked(route.url)
                # In pinned Playwright 1.63 the socket callback runs on the dispatcher greenlet:
                # Sync close() spins there. The handler supports an awaitable, so bridge the
                # same route to its async API (no second thread, browser or upstream socket).
                return AsyncWebSocketRoute(route._impl_obj).close(  # pinned API bridge
                    code=1008, reason="Origin outside workflow allowlist",
                )

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self._headless)
                try:
                    context = browser.new_context(service_workers="block", accept_downloads=False)
                    context.route("**/*", guard)
                    context.route_web_socket("**/*", guard_socket)
                    page = context.new_page()
                    page.set_default_timeout(self._timeout)
                    state = RunState()

                    for i, step in enumerate(workflow.steps):
                        if i and self._delay:
                            page.wait_for_timeout(self._delay)
                        notify("step_started", {"step_id": step.id, "index": i})
                        result = self._run_step(page, workflow, step, values, policy, secrets, state, notify, snap)
                        steps.append(result)
                        notify("step_finished", result.model_dump(mode="json"))
                        if result.status == "failed":
                            status = "failed"
                            skipped = [
                                StepResult(step_id=s.id, action=s.action, status="skipped")
                                for s in workflow.steps[i + 1 :]
                            ]
                            steps.extend(skipped)
                            for s in skipped:
                                notify("step_finished", s.model_dump(mode="json"))
                            break

                    for idx, check in enumerate(workflow.verify):
                        if status != "passed":
                            verifications.append(VerificationResult(index=idx, type=check.type, status="skipped"))
                            continue
                        verifications.append(self._verify(page, idx, check, secrets))
                    for v in verifications:
                        notify("verification_finished", v.model_dump(mode="json"))
                    snap(page, None, "final")
                    if any(v.status == "failed" for v in verifications):
                        status = "failed"
                    if self._final_hold:
                        notify("holding", {"ms": self._final_hold})
                        page.wait_for_timeout(self._final_hold)  # bounded; the browser closes next
                finally:
                    browser.close()
        except PlaywrightError as exc:
            status, error = "error", _scrub(str(exc), secrets)

        return RunResult(
            adapter=self.kind,
            status=status,
            started_at=started,
            finished_at=_now(),
            steps=steps,
            verifications=verifications,
            blocked_requests=blocked,
            error=error,
        )

    # -- steps --------------------------------------------------------------------------

    def _run_step(
        self, page: Page, workflow: Workflow, step, values: dict[str, str], policy: UrlPolicy, secrets: list[str],
        state: RunState, notify: Callable[[str, dict], None] | None = None, snap: Callable[..., None] | None = None,
    ) -> StepResult:  # fmt: skip
        snap = snap or (lambda *args, **kwargs: None)
        t0 = time.monotonic()
        result = StepResult(step_id=step.id, action=step.action, status="passed")
        try:
            if isinstance(step, NavigateStep):
                policy.check(step.url)
                page.goto(step.url, wait_until="domcontentloaded")
                state.unconfirmed_side_effect = False
                snap(page, step.id, "after")
            elif isinstance(step, WaitForUrlStep):
                page.wait_for_url(lambda url: urlsplit(url).path == step.path, wait_until="domcontentloaded")
                state.unconfirmed_side_effect = False  # the previous action's outcome is now known
                snap(page, step.id, "after")
            else:
                try:
                    locator, index = self._resolve(page, step.target)
                except ElementNotFound as failure:
                    snap(page, step.id, "not_found")
                    if notify is not None and self._healer.policy.enabled:
                        notify("recovery_started", {"step_id": step.id, "exhausted_strategies": failure.tried})
                    self._recover(page, workflow, step, values, secrets, failure, state, result)
                    result.duration_ms = int((time.monotonic() - t0) * 1000)
                    return result
                result.strategy_index = index
                result.strategy_by = step.target.strategies[index].by
                result.resolution = "primary" if index == 0 else "deterministic_fallback"
                result.healed = index > 0
                self._note_side_effect(locator, step, state)
                snap(page, step.id, "before", locator)
                if isinstance(step, ClickStep):
                    locator.click()
                elif isinstance(step, FillStep):
                    locator.fill(render(step.value, values))
                elif isinstance(step, SelectStep):
                    locator.select_option(render(step.value, values))
                elif isinstance(step, CheckStep):
                    locator.set_checked(step.checked)
                elif isinstance(step, PressStep):
                    locator.press(step.key)
                else:  # pragma: no cover - the DSL union is closed
                    raise StepFailure(f"Unsupported action '{step.action}'")
        except (PlaywrightError, StepFailure, ValueError) as exc:
            result.status = "failed"
            result.error = _scrub(str(exc), secrets)
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    def _recover(self, page, workflow, step, values, secrets, failure: ElementNotFound, state: RunState, result):
        """One self-healing attempt: the step either acted once on a validated candidate or failed."""
        value = render(step.value, values) if isinstance(step, (FillStep, SelectStep)) else None
        source = PlaywrightCandidateSource(page, timeout_ms=self._timeout)
        attempt = self._healer.attempt(workflow=workflow, step=step, failure=failure, source=source, state=state, value=value)
        result.recovery = attempt
        if attempt.outcome == "recovered":
            result.resolution = "ai_recovery"
        else:
            result.status = "failed"
            result.error = _scrub(f"{failure} — recovery {attempt.outcome}: {attempt.reason}", secrets)

    @staticmethod
    def _note_side_effect(locator: Locator, step, state: RunState) -> None:
        is_press = isinstance(step, PressStep)
        if not (isinstance(step, ClickStep) or (is_press and step.key == "Enter")):
            return
        try:
            if locator.evaluate(SUBMITS_JS, is_press):
                state.unconfirmed_side_effect = True
        except PlaywrightError:
            state.unconfirmed_side_effect = True  # unknown: assume the conservative case

    def _locator(self, page: Page, strategy) -> Locator:
        match strategy.by:
            case "test_id":
                return page.get_by_test_id(strategy.value)
            case "role":
                if strategy.name:
                    return page.get_by_role(strategy.role, name=strategy.name, exact=True)
                return page.get_by_role(strategy.role)
            case "label":
                return page.get_by_label(strategy.value, exact=True)
            case "placeholder":
                return page.get_by_placeholder(strategy.value, exact=True)
            case "text":
                return page.get_by_text(strategy.value, exact=True)
            case "css":
                return page.locator(f"css={strategy.value}")
        raise StepFailure(f"Unknown locator strategy '{strategy.by}'")  # pragma: no cover

    def _resolve(self, page: Page, target: Target) -> tuple[Locator, int]:
        """First strategy (in priority order) that identifies exactly one element."""
        deadline = time.monotonic() + self._timeout / 1000
        while True:
            ambiguous = False
            for index, strategy in enumerate(target.strategies):
                locator = self._locator(page, strategy)
                try:
                    count = locator.count()
                    if count > 1:
                        locator = locator.filter(visible=True)
                        count = locator.count()
                except PlaywrightError:
                    continue
                if count == 1:
                    return locator, index
                ambiguous = ambiguous or count > 1
            if time.monotonic() >= deadline:
                raise ElementNotFound(
                    f"No locator strategy matched exactly one element for {target.description or 'target'}",
                    tried=[describe_strategy(s) for s in target.strategies],
                    ambiguous=ambiguous,
                )
            page.wait_for_timeout(_POLL_INTERVAL_MS)

    # -- verification -------------------------------------------------------------------

    def _verify(self, page: Page, index: int, check, secrets: list[str]) -> VerificationResult:
        result = VerificationResult(index=index, type=check.type, status="passed")
        try:
            if isinstance(check, TextVisible):
                page.get_by_text(check.text).first.wait_for(state="visible")
                result.detail = "text visible"
            elif isinstance(check, UrlPath):
                page.wait_for_url(lambda url: urlsplit(url).path == check.path)
                result.detail = f"path is {check.path}"
            elif isinstance(check, ElementText):
                locator, _ = self._resolve(page, check.target)
                text = locator.inner_text()
                if check.contains not in text:
                    raise StepFailure("Element text does not contain the expected value")
                result.detail = "element text matches"
        except (PlaywrightError, StepFailure, ElementNotFound) as exc:
            result.status = "failed"
            result.detail = _scrub(str(exc), secrets)
        return result


def _scrub(message: str, secrets: list[str]) -> str:
    for secret in secrets:
        message = message.replace(secret, "***")
    message = message.strip()
    return message if len(message) <= _MAX_ERROR_LEN else message[: _MAX_ERROR_LEN - 1] + "…"

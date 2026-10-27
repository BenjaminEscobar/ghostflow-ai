"""Web execution adapter backed by Playwright (Chromium).

Safety properties:
- Only DSL actions are executed; there is no path to page.evaluate or similar.
- Every browser request (documents, XHR, images...) is checked against the workflow's
  origin allowlist, itself a subset of the server allowlist. Others are aborted.
- Sensitive input values are scrubbed from error messages before they are returned.
"""

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Route, sync_playwright

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
from ghostflow.security.urls import UrlPolicy

_POLL_INTERVAL_MS = 150
_MAX_ERROR_LEN = 500


class StepFailure(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class PlaywrightWebAdapter:
    kind = "web"

    def __init__(self, policy: UrlPolicy, *, headless: bool = True, step_timeout_ms: int = 10_000):
        self._policy = policy
        self._headless = headless
        self._timeout = step_timeout_ms

    # -- public -------------------------------------------------------------------------

    def execute(self, workflow: Workflow, inputs: Mapping[str, str]) -> RunResult:
        started = _now()
        values = resolve_inputs(workflow, inputs)
        secrets = [values[i.name] for i in workflow.inputs if i.sensitive and values.get(i.name)]
        policy = self._policy.restricted_to(workflow.allowed_origins)

        steps: list[StepResult] = []
        verifications: list[VerificationResult] = []
        blocked: list[str] = []
        status = "passed"
        error: str | None = None

        def guard(route: Route) -> None:
            url = route.request.url
            if policy.is_allowed(url):
                route.continue_()
            else:
                parts = urlsplit(url)
                blocked.append(f"{parts.scheme}://{parts.netloc}{parts.path}"[:300])
                route.abort("blockedbyclient")

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self._headless)
                try:
                    context = browser.new_context(service_workers="block", accept_downloads=False)
                    context.route("**/*", guard)
                    page = context.new_page()
                    page.set_default_timeout(self._timeout)

                    for i, step in enumerate(workflow.steps):
                        result = self._run_step(page, step, values, policy, secrets)
                        steps.append(result)
                        if result.status == "failed":
                            status = "failed"
                            steps.extend(
                                StepResult(step_id=s.id, action=s.action, status="skipped")
                                for s in workflow.steps[i + 1 :]
                            )
                            break

                    for idx, check in enumerate(workflow.verify):
                        if status != "passed":
                            verifications.append(VerificationResult(index=idx, type=check.type, status="skipped"))
                            continue
                        verifications.append(self._verify(page, idx, check, secrets))
                    if any(v.status == "failed" for v in verifications):
                        status = "failed"
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

    def _run_step(self, page: Page, step, values: dict[str, str], policy: UrlPolicy, secrets: list[str]) -> StepResult:
        t0 = time.monotonic()
        result = StepResult(step_id=step.id, action=step.action, status="passed")
        try:
            if isinstance(step, NavigateStep):
                policy.check(step.url)
                page.goto(step.url, wait_until="domcontentloaded")
            elif isinstance(step, WaitForUrlStep):
                page.wait_for_url(lambda url: urlsplit(url).path == step.path, wait_until="domcontentloaded")
            else:
                locator, index = self._resolve(page, step.target)
                result.strategy_index = index
                result.strategy_by = step.target.strategies[index].by
                result.healed = index > 0
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
            if time.monotonic() >= deadline:
                raise StepFailure(f"No locator strategy matched exactly one element for {target.description or 'target'}")
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
        except (PlaywrightError, StepFailure) as exc:
            result.status = "failed"
            result.detail = _scrub(str(exc), secrets)
        return result


def _scrub(message: str, secrets: list[str]) -> str:
    for secret in secrets:
        message = message.replace(secret, "***")
    message = message.strip()
    return message if len(message) <= _MAX_ERROR_LEN else message[: _MAX_ERROR_LEN - 1] + "…"

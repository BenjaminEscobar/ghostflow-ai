"""Semantic self-healing (GF-003): recover a step whose recorded locators all find nothing.

    every locator finds 0 elements ─► eligible? ─► collect page candidates (fixed script, capped,
    no values) ─► advisor (AI) picks one id ─► deterministic checks ─► live re-check ─► act once

Authority stays here, in the executor:
- The advisor only ever returns the id of a candidate GhostFlow itself extracted. No selector,
  script or URL from the advisor is used anywhere.
- Every proposal is re-checked against fixed rules: id exists, confidence, action/role
  compatibility, unambiguous, visible and enabled, link destination inside allowed_origins and
  equal to the path the workflow expects next, side-effect policy.
- At most one attempt per step and `max_attempts_per_run` per run. A recovered step is never
  retried, and the workflow document is never modified.

Page content (names, headings, regions) is untrusted data: it is scrubbed before it leaves the
executor and can only influence which of the already-allowed candidates is proposed.
"""

import logging
import time
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ghostflow.dsl.models import RoleStrategy, Workflow
from ghostflow.dsl.templating import template_references
from ghostflow.security.redaction import scrub_text
from ghostflow.security.urls import UrlPolicy

log = logging.getLogger("ghostflow.healing")

Confidence = Literal["high", "medium", "low", "none"]
CandidateKind = Literal["link", "button", "text_field", "select", "toggle", "other"]
Outcome = Literal["recovered", "rejected", "skipped", "error"]

_CONFIDENCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
RECOVERABLE_ACTIONS = {"click", "fill", "select", "check"}
_COMPATIBLE_KINDS: dict[str, set[str]] = {
    "click": {"link", "button", "other"},
    "fill": {"text_field"},
    "select": {"select"},
    "check": {"toggle"},
}
_TEXT_INPUT_TYPES = {"text", "email", "search", "tel", "url", "number", "date", "datetime-local", "month", "time", "week"}


class ElementNotFound(Exception):
    """Every locator strategy of a target was tried until the timeout without a unique match."""

    def __init__(self, message: str, *, tried: list[str], ambiguous: bool):
        super().__init__(message)
        self.tried = tried
        self.ambiguous = ambiguous


# -- candidates ---------------------------------------------------------------------------


class Candidate(BaseModel):
    """An interactive element of the current page. Built by GhostFlow, never by a model."""

    model_config = ConfigDict(extra="forbid")

    id: str  # temporary, valid for one attempt only: c1, c2...
    tag: str
    role: str | None = None
    name: str | None = None
    input_type: str | None = None
    enabled: bool = True
    visible: bool = True
    region: str | None = None
    heading: str | None = None
    href: str | None = None  # absolute; internal only (policy checks), never sent to a model
    new_tab: bool = False
    download: bool = False
    submits: bool = False
    readonly: bool = False

    @property
    def href_path(self) -> str | None:
        return (urlsplit(self.href).path or "/") if self.href else None

    @property
    def kind(self) -> CandidateKind:
        tag, itype, role = self.tag, (self.input_type or "").lower(), (self.role or "").lower()
        if tag == "a" and self.href:
            return "link"
        if tag == "select":
            return "select"
        if itype in {"checkbox", "radio"} or role in {"checkbox", "radio", "switch"}:
            return "toggle"
        if tag == "textarea" or (tag == "input" and (itype or "text") in _TEXT_INPUT_TYPES):
            return "text_field"
        if tag == "button" or itype in {"submit", "button", "reset", "image"} or role == "button":
            return "button"
        return "other"

    @property
    def side_effect(self) -> bool:
        """Could activating it change data or state beyond setting a form field?"""
        kind = self.kind
        if kind in {"text_field", "select", "toggle"}:
            return False
        if kind == "link":
            scheme = urlsplit(self.href or "").scheme
            return self.new_tab or self.download or scheme not in {"http", "https"}
        return True  # buttons, submits, custom widgets: unknown effects

    def signature(self) -> tuple:
        return (self.kind, (self.role or "").lower(), " ".join((self.name or "").lower().split()), self.href_path)

    def public(self) -> dict:
        """What an advisor may see."""
        data = {
            "id": self.id,
            "kind": self.kind,
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
            "input_type": self.input_type,
            "enabled": self.enabled,
            "region": self.region,
            "heading": self.heading,
            "href_path": self.href_path,
        }
        return {k: v for k, v in data.items() if v is not None}


@dataclass(frozen=True)
class LiveState:
    attached: bool
    visible: bool
    enabled: bool
    href: str | None = None


class CandidateSource(Protocol):
    """Access to the current page, implemented over Playwright (and by fakes in tests)."""

    def page_context(self) -> tuple[str, str | None]:
        """(current path, page title)."""
        ...

    def collect(self, limit: int) -> list[Candidate]: ...

    def live_state(self, candidate_id: str) -> LiveState: ...

    def perform(self, candidate_id: str, action: str, *, value: str | None = None, checked: bool | None = None) -> None: ...


# -- advisor contract ---------------------------------------------------------------------


class RecoveryRequest(BaseModel):
    workflow_name: str
    workflow_purpose: str | None = None
    step_id: str
    action: str
    step_description: str | None = None
    step_purpose: str | None = None
    expected_element: str | None = None
    failed_strategies: list[str]
    expected_next_path: str | None = None
    previous_step: str | None = None
    page_path: str
    page_title: str | None = None
    candidates: list[Candidate]


class RecoveryAdvice(BaseModel):
    candidate_id: str | None = None
    confidence: Confidence = "none"
    justification: str | None = None
    rejection_reason: str | None = None
    provider: str
    model: str
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


class RecoveryAdvisorError(RuntimeError):
    def __init__(self, message: str, *, provider: str, model: str, stats: dict | None = None):
        super().__init__(message)
        self.provider, self.model, self.stats = provider, model, stats or {}


class RecoveryAdvisor(Protocol):
    def propose(self, request: RecoveryRequest) -> RecoveryAdvice: ...


# -- evidence -----------------------------------------------------------------------------


class ValidationCheck(BaseModel):
    check: str
    passed: bool
    detail: str | None = None


class ProposedCandidate(BaseModel):
    id: str
    kind: str
    role: str | None = None
    name: str | None = None
    href_path: str | None = None


class RecoveryAttempt(BaseModel):
    """Audit record of one self-healing attempt (stored in the run result, no input values)."""

    trigger: Literal["element_not_found"] = "element_not_found"
    exhausted_strategies: list[str] = Field(default_factory=list)
    consulted_model: bool = False
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    candidates_considered: int = 0
    proposed: ProposedCandidate | None = None
    confidence: Confidence | None = None
    justification: str | None = None
    validation: list[ValidationCheck] = Field(default_factory=list)
    outcome: Outcome
    reason: str | None = None
    duration_ms: int = 0


# -- policy -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryPolicy:
    enabled: bool = False
    min_confidence: Literal["high", "medium"] = "high"
    allow_side_effects: bool = False
    max_candidates: int = 40
    max_attempts_per_run: int = 2


@dataclass
class RunState:
    """Per-run memory the healer needs to stay conservative."""

    attempts: int = 0
    # A submit-like action ran and no later navigation confirmed its outcome yet.
    unconfirmed_side_effect: bool = False


def describe_strategy(strategy) -> str:
    if isinstance(strategy, RoleStrategy):
        text = f'role={strategy.role} "{strategy.name}"' if strategy.name else f"role={strategy.role}"
    else:
        text = f"{strategy.by}={strategy.value}"
    return scrub_text(text, 160) or strategy.by


def _expected_role(step) -> str | None:
    for s in step.target.strategies:
        if isinstance(s, RoleStrategy):
            return s.role
    return None


def _clip(text: str | None, limit: int) -> str | None:
    return scrub_text(text, limit) if text else None


class Healer:
    def __init__(self, policy: RecoveryPolicy, url_policy: UrlPolicy, advisor: RecoveryAdvisor | None = None):
        self.policy = policy
        self._url_policy = url_policy
        self._advisor = advisor

    # -- public ---------------------------------------------------------------------------

    def attempt(
        self,
        *,
        workflow: Workflow,
        step,
        failure: ElementNotFound,
        source: CandidateSource,
        state: RunState,
        value: str | None = None,
    ) -> RecoveryAttempt:
        t0 = time.monotonic()
        attempt = RecoveryAttempt(outcome="skipped", exhausted_strategies=failure.tried)
        try:
            self._attempt(attempt, workflow, step, failure, source, state, value)
        except Exception as exc:  # the executor must always get a controlled result
            attempt.outcome, attempt.reason = "error", f"Recovery error: {type(exc).__name__}"
        attempt.duration_ms = int((time.monotonic() - t0) * 1000)
        failed = next((c.check for c in attempt.validation if not c.passed), None)
        log.info(
            "healing step=%s outcome=%s consulted_model=%s provider=%s model=%s candidate=%s confidence=%s "
            "latency_ms=%s prompt_tokens=%s completion_tokens=%s failed_check=%s reason=%s",
            step.id, attempt.outcome, attempt.consulted_model, attempt.provider, attempt.model,
            attempt.proposed.id if attempt.proposed else None, attempt.confidence, attempt.latency_ms,
            attempt.prompt_tokens, attempt.completion_tokens, failed,
            # Rejection reasons may quote page or model text: logs keep only the rule name.
            None if attempt.outcome == "rejected" else attempt.reason,
        )  # fmt: skip
        return attempt

    # -- internals ------------------------------------------------------------------------

    def _skip(self, attempt: RecoveryAttempt, reason: str) -> None:
        attempt.outcome, attempt.reason = "skipped", reason

    def _reject(self, attempt: RecoveryAttempt, check: str, detail: str) -> None:
        attempt.validation.append(ValidationCheck(check=check, passed=False, detail=detail))
        attempt.outcome, attempt.reason = "rejected", detail

    def _eligibility(self, workflow: Workflow, step, failure: ElementNotFound, state: RunState) -> str | None:
        if not self.policy.enabled or self._advisor is None:
            return "Self-healing disabled (GHOSTFLOW_HEALING_ENABLED=false)"
        if step.action not in RECOVERABLE_ACTIONS:
            return f"Action '{step.action}' is not eligible for recovery"
        if failure.ambiguous:
            return "Recorded locators match several elements; ambiguity is not resolved by AI"
        if step.action in {"fill", "select"}:
            sensitive = {i.name for i in workflow.inputs if i.sensitive}
            if template_references(step.value) & sensitive:
                return "Step types a sensitive input; credentials are never redirected by AI"
        if state.attempts >= self.policy.max_attempts_per_run:
            return f"Recovery budget for this run exhausted ({self.policy.max_attempts_per_run})"
        return None

    def _eligible_candidates(self, step, candidates: list[Candidate]) -> list[Candidate]:
        kinds = _COMPATIBLE_KINDS[step.action]
        keep = [c for c in candidates if c.visible and c.enabled and c.kind in kinds]
        if not self.policy.allow_side_effects:
            keep = [c for c in keep if not c.side_effect]
        return keep

    def _attempt(self, attempt, workflow, step, failure, source, state, value) -> None:
        reason = self._eligibility(workflow, step, failure, state)
        if reason:
            return self._skip(attempt, reason)
        state.attempts += 1

        page_path, page_title = source.page_context()
        candidates = self._eligible_candidates(step, source.collect(self.policy.max_candidates))
        attempt.candidates_considered = len(candidates)
        if not candidates:
            return self._reject(attempt, "candidates_available", "No eligible candidate on the page; model not consulted")
        by_id = {c.id: c for c in candidates}

        index = next(i for i, s in enumerate(workflow.steps) if s.id == step.id)
        nxt = workflow.steps[index + 1] if index + 1 < len(workflow.steps) else None
        expected_next_path = nxt.path if nxt is not None and nxt.action == "wait_for_url" else None
        previous = workflow.steps[index - 1] if index > 0 else None
        insight = None
        if workflow.interpretation:
            insight = next((s for s in workflow.interpretation.steps if s.id == step.id), None)

        request = RecoveryRequest(
            workflow_name=_clip(workflow.name, 200) or "workflow",
            workflow_purpose=_clip(workflow.interpretation.purpose, 600) if workflow.interpretation else None,
            step_id=step.id,
            action=step.action,
            step_description=_clip(step.description, 200),
            step_purpose=_clip(insight.explanation, 200) if insight else None,
            expected_element=_clip(step.target.description, 200),
            failed_strategies=failure.tried,
            expected_next_path=expected_next_path,
            previous_step=_clip(previous.description, 200) if previous is not None else None,
            page_path=page_path,
            page_title=_clip(page_title, 120),
            candidates=candidates,
        )

        attempt.consulted_model = True
        try:
            advice = self._advisor.propose(request)
        except RecoveryAdvisorError as exc:
            attempt.provider, attempt.model = exc.provider, exc.model
            self._apply_stats(attempt, exc.stats)
            attempt.outcome, attempt.reason = "error", str(exc)[:300]
            return
        attempt.provider, attempt.model = advice.provider, advice.model
        self._apply_stats(attempt, advice.model_dump())
        attempt.confidence = advice.confidence
        attempt.justification = _clip(advice.justification, 300)

        chosen = self._validate(attempt, advice, by_id, candidates, step, expected_next_path, state)
        if chosen is None:
            return

        live = source.live_state(chosen.id)
        if not (live.attached and live.visible and live.enabled):
            return self._reject(attempt, "live_recheck", "Candidate is no longer attached, visible and enabled")
        if chosen.kind == "link" and (live.href != chosen.href or not self._url_policy.is_allowed(live.href or "")):
            return self._reject(attempt, "live_recheck", "Link destination changed since inspection")
        attempt.validation.append(ValidationCheck(check="live_recheck", passed=True))

        if chosen.side_effect:
            state.unconfirmed_side_effect = True
        source.perform(chosen.id, step.action, value=value, checked=getattr(step, "checked", None))
        attempt.outcome, attempt.reason = "recovered", None

    @staticmethod
    def _apply_stats(attempt: RecoveryAttempt, stats: dict) -> None:
        for key in ("latency_ms", "prompt_tokens", "completion_tokens", "reasoning_tokens"):
            if isinstance(stats.get(key), int):
                setattr(attempt, key, stats[key])

    def _validate(self, attempt, advice, by_id, candidates, step, expected_next_path, state) -> Candidate | None:
        def ok(check: str, detail: str | None = None) -> None:
            attempt.validation.append(ValidationCheck(check=check, passed=True, detail=detail))

        if not advice.candidate_id:
            reason = _clip(advice.rejection_reason, 300) or "no reason given"
            self._reject(attempt, "candidate_selected", f"Model found no equivalent element: {reason}")
            return None
        chosen = by_id.get(advice.candidate_id)
        if chosen is None:
            self._reject(attempt, "candidate_exists", "Model chose an id that is not in the candidate list")
            return None
        ok("candidate_exists")
        attempt.proposed = ProposedCandidate(
            id=chosen.id, kind=chosen.kind, role=chosen.role, name=chosen.name, href_path=chosen.href_path
        )

        needed = _CONFIDENCE_RANK[self.policy.min_confidence]
        if _CONFIDENCE_RANK[advice.confidence] < needed:
            self._reject(attempt, "confidence", f"Confidence '{advice.confidence}' below required '{self.policy.min_confidence}'")
            return None
        ok("confidence", advice.confidence)

        if chosen.kind not in _COMPATIBLE_KINDS[step.action]:
            self._reject(attempt, "action_compatible", f"A {chosen.kind} cannot receive '{step.action}'")
            return None
        if chosen.kind == "text_field" and chosen.readonly:
            self._reject(attempt, "action_compatible", "Field is read-only")
            return None
        ok("action_compatible", chosen.kind)

        expected_role = _expected_role(step)
        if expected_role and (chosen.role or "") != expected_role:
            self._reject(attempt, "role_compatible", f"Expected role '{expected_role}', candidate is '{chosen.role}'")
            return None
        ok("role_compatible", expected_role)

        if not (chosen.visible and chosen.enabled):
            self._reject(attempt, "visible_enabled", "Candidate is hidden or disabled")
            return None
        ok("visible_enabled")

        if sum(1 for c in candidates if c.signature() == chosen.signature()) > 1:
            self._reject(attempt, "unambiguous", "Several candidates are indistinguishable from the proposed one")
            return None
        ok("unambiguous")

        if chosen.kind == "link":
            if chosen.new_tab or chosen.download:
                self._reject(attempt, "allowed_destination", "Link opens a new tab or a download")
                return None
            if not self._url_policy.is_allowed(chosen.href or ""):
                self._reject(attempt, "allowed_destination", "Link leaves the workflow's allowed origins")
                return None
            ok("allowed_destination", chosen.href_path)
            if expected_next_path is not None:
                if chosen.href_path != expected_next_path:
                    self._reject(
                        attempt, "expected_destination",
                        f"Link goes to {chosen.href_path}, the workflow expects {expected_next_path}",
                    )  # fmt: skip
                    return None
                ok("expected_destination", expected_next_path)

        if chosen.side_effect:
            if not self.policy.allow_side_effects:
                self._reject(attempt, "side_effects", "Controls with side effects are not recovered by policy")
                return None
            if state.unconfirmed_side_effect:
                self._reject(
                    attempt, "side_effects",
                    "An earlier submit-like action has an unconfirmed outcome; not risking a duplicate operation",
                )  # fmt: skip
                return None
            ok("side_effects", "allowed by policy")
        else:
            ok("side_effects", "none")
        return chosen


# -- Playwright implementation ------------------------------------------------------------

# Fixed inspection scripts. They are constants of GhostFlow's source code, take only a numeric
# limit, never read form values (except the label of input[type=submit|button]) and skip
# password and hidden fields. Nothing a model returns is ever evaluated.
COLLECT_JS = """
(max) => {
  const SEL = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"],'
    + '[role="menuitem"], [role="checkbox"], [role="radio"], [role="switch"], [role="combobox"], [role="textbox"]';
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= max) break;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (el.tagName === 'INPUT' && (type === 'hidden' || type === 'password')) continue;
    if (el.closest('[hidden], [aria-hidden="true"]')) continue;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || s.visibility === 'hidden' || s.display === 'none') continue;
    out.push(el);
  }
  return out;
}
"""

DESCRIBE_JS = """
(els) => {
  const clip = (t, n) => { const v = (t || '').replace(/\\s+/g, ' ').trim().slice(0, n); return v || null; };
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.split(/\\s+/)[0];
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return (el.multiple || el.size > 1) ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (['submit', 'button', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'search') return 'searchbox';
      if (type === 'number') return 'spinbutton';
      if (type === 'range') return 'slider';
      return 'textbox';
    }
    return null;
  };
  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return clip(aria, 80);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.textContent).join(' ');
      if (t.trim()) return clip(t, 80);
    }
    if (el.labels && el.labels.length) return clip(Array.from(el.labels).map((l) => l.textContent).join(' '), 80);
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && ['submit', 'button', 'reset'].includes(type)) return clip(el.getAttribute('value'), 80);
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      return clip(el.getAttribute('placeholder') || el.getAttribute('title'), 80);
    }
    const text = clip(el.innerText || el.textContent, 80);
    if (text) return text;
    const img = el.querySelector('img[alt]');
    return clip((img && img.getAttribute('alt')) || el.getAttribute('title'), 80);
  };
  const regionOf = (el) => {
    const c = el.closest('nav, header, footer, aside, form, dialog, main, section, [role="navigation"], [role="toolbar"],'
      + '[role="menu"], [role="menubar"], [role="tablist"], [role="dialog"], [role="search"]');
    if (!c) return null;
    const kind = c.getAttribute('role') || c.tagName.toLowerCase();
    const label = c.getAttribute('aria-label');
    return clip(label ? kind + ': ' + label : kind, 80);
  };
  const headingOf = (el) => {
    const scope = el.closest('section, main, form, article, dialog, aside') || document.body;
    const h = scope.querySelector('h1, h2, h3');
    return h ? clip(h.textContent, 80) : null;
  };
  return els.map((el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const isA = tag === 'a';
    const submits = !!el.form && ((tag === 'button' && (type || 'submit') === 'submit') || (tag === 'input' && ['submit', 'image'].includes(type)));
    return {
      tag,
      role: roleOf(el),
      name: nameOf(el),
      input_type: tag === 'input' ? (type || 'text') : null,
      enabled: !(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      region: regionOf(el),
      heading: headingOf(el),
      href: isA ? el.href : null,
      new_tab: isA && !!el.target && el.target !== '_self',
      download: isA && el.hasAttribute('download'),
      submits,
      readonly: !!el.readOnly,
    };
  });
}
"""

LIVE_JS = "(el) => ({ attached: el.isConnected, href: el.tagName === 'A' ? el.href : null })"

# Evaluated before a deterministic click (press=false) or Enter press (press=true) to remember
# that a submit-like action ran, so the healer never risks repeating it.
SUBMITS_JS = """
(el, press) => {
  const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
  if (tag === 'a' || tag === 'select') return false;
  if (tag === 'button' || el.getAttribute('role') === 'button') return true;
  if (tag === 'input' && ['submit', 'image', 'button'].includes(type)) return true;
  return press && !!el.closest('form');
}
"""


class PlaywrightCandidateSource:
    def __init__(self, page, *, timeout_ms: int):
        self._page = page
        self._timeout = timeout_ms
        self._handles: dict = {}

    def page_context(self) -> tuple[str, str | None]:
        try:
            title = self._page.title()
        except Exception:  # noqa: BLE001 - a title is optional context
            title = None
        return urlsplit(self._page.url).path or "/", title

    def collect(self, limit: int) -> list[Candidate]:
        array = self._page.evaluate_handle(COLLECT_JS, limit)
        try:
            facts = self._page.evaluate(DESCRIBE_JS, array)
            props = array.get_properties()
            elements = [props[str(i)].as_element() for i in range(len(facts))]
        finally:
            array.dispose()
        candidates: list[Candidate] = []
        self._handles = {}
        for i, (fact, element) in enumerate(zip(facts, elements, strict=True), start=1):
            if element is None:
                continue
            cid = f"c{i}"
            self._handles[cid] = element
            candidates.append(
                Candidate(
                    id=cid,
                    tag=str(fact.get("tag") or "")[:32],
                    role=_clip(fact.get("role"), 32),
                    name=_clip(fact.get("name"), 80),
                    input_type=_clip(fact.get("input_type"), 32),
                    enabled=bool(fact.get("enabled")),
                    visible=True,
                    region=_clip(fact.get("region"), 80),
                    heading=_clip(fact.get("heading"), 80),
                    href=(fact.get("href") or None) and str(fact["href"])[:2048],
                    new_tab=bool(fact.get("new_tab")),
                    download=bool(fact.get("download")),
                    submits=bool(fact.get("submits")),
                    readonly=bool(fact.get("readonly")),
                )
            )
        return candidates

    def live_state(self, candidate_id: str) -> LiveState:
        handle = self._handles[candidate_id]
        try:
            info = handle.evaluate(LIVE_JS)
            return LiveState(
                attached=bool(info.get("attached")),
                visible=handle.is_visible(),
                enabled=handle.is_enabled(),
                href=info.get("href"),
            )
        except Exception:  # noqa: BLE001 - detached or navigated away
            return LiveState(attached=False, visible=False, enabled=False)

    def perform(self, candidate_id: str, action: str, *, value: str | None = None, checked: bool | None = None) -> None:
        handle = self._handles[candidate_id]
        if action == "click":
            handle.click(timeout=self._timeout)
        elif action == "fill":
            handle.fill(value or "", timeout=self._timeout)
        elif action == "select":
            handle.select_option(value or "", timeout=self._timeout)
        elif action == "check":
            handle.set_checked(True if checked is None else checked, timeout=self._timeout)
        else:  # pragma: no cover - filtered by eligibility
            raise ValueError(f"Action '{action}' is not recoverable")

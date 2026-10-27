"""Deterministic compilation of a recording into a Workflow DSL document.

No AI is involved here: the compiler only maps recorded events onto the closed set of DSL
actions. The AI layer may later annotate the result (names, descriptions), never change it.
"""

import re
import unicodedata
from typing import get_args
from urllib.parse import urlsplit

from ghostflow.dsl.models import (
    AriaRole,
    CheckStep,
    ClickStep,
    CssStrategy,
    FillStep,
    LabelStrategy,
    NavigateStep,
    PlaceholderStrategy,
    PressStep,
    RoleStrategy,
    SelectStep,
    Target,
    TestIdStrategy,
    TextStrategy,
    TextVisible,
    UrlPath,
    WaitForUrlStep,
    Workflow,
    WorkflowInput,
)
from ghostflow.recording.models import ElementDescriptor, RecordedEvent, RecordingCreate
from ghostflow.security.urls import display_origin

# A navigation this close to a user action is treated as caused by it (link, submit...).
NAV_WINDOW_MS = 5_000

_SUPPORTED_ROLES = set(get_args(AriaRole))
_SIMPLE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_TEXT_ROLES = {"button", "link", "tab", "menuitem"}


class CompilationError(ValueError):
    pass


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _clip(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_target(d: ElementDescriptor) -> Target:
    strategies: list = []
    if d.test_id:
        strategies.append(TestIdStrategy(by="test_id", value=d.test_id))
    if d.role in _SUPPORTED_ROLES and d.accessible_name:
        strategies.append(RoleStrategy(by="role", role=d.role, name=d.accessible_name))
    if d.label:
        strategies.append(LabelStrategy(by="label", value=d.label))
    if d.placeholder:
        strategies.append(PlaceholderStrategy(by="placeholder", value=d.placeholder))
    if d.element_id:
        if _SIMPLE_ID.match(d.element_id):
            strategies.append(CssStrategy(by="css", value=f"#{d.element_id}"))
        else:
            strategies.append(CssStrategy(by="css", value=f'[id="{_css_attr(d.element_id)}"]'))
    if d.name_attr:
        selector = f'{d.tag}[name="{_css_attr(d.name_attr)}"]'
        if d.value_attr is not None:
            selector += f'[value="{_css_attr(d.value_attr)}"]'
        strategies.append(CssStrategy(by="css", value=selector))
    if d.text and (d.tag in {"a", "button"} or d.role in _TEXT_ROLES):
        strategies.append(TextStrategy(by="text", value=d.text))
    if d.css:
        strategies.append(CssStrategy(by="css", value=d.css))
    if not strategies:
        raise CompilationError(f"Element <{d.tag}> has no usable locator")

    unique = list({s.model_dump_json(): s for s in strategies}.values())[:8]
    name = d.accessible_name or d.label or d.text or d.placeholder or d.name_attr or d.element_id
    description = f'{d.role or d.tag} "{_clip(name, 150)}"' if name else d.role or d.tag
    return Target(description=description, strategies=unique)


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    if not slug:
        return "field"
    if not slug[0].isalpha():
        slug = f"f_{slug}"
    return slug[:36].rstrip("_")


class _Builder:
    def __init__(self) -> None:
        self.steps: list = []
        self.inputs: list[WorkflowInput] = []
        self._input_names: set[str] = set()

    def next_id(self) -> str:
        return f"s{len(self.steps) + 1}"

    def add_input(self, d: ElementDescriptor, value: str | None, sensitive: bool) -> str:
        base = _slug(d.label or d.accessible_name or d.name_attr or d.element_id or d.placeholder or "field")
        name, n = base, 2
        while name in self._input_names:
            name, n = f"{base}_{n}", n + 1
        self._input_names.add(name)
        label = d.label or d.accessible_name or d.placeholder
        self.inputs.append(
            WorkflowInput(
                name=name,
                label=_clip(label) if label else None,
                sensitive=sensitive,
                default=None if sensitive else (value or ""),
            )
        )
        return name

    def update_input_default(self, name: str, value: str | None, sensitive: bool) -> None:
        for i, inp in enumerate(self.inputs):
            if inp.name == name:
                self.inputs[i] = inp.model_copy(
                    update={"sensitive": sensitive, "default": None if sensitive else (value or "")}
                )


def _input_name_of(step: FillStep | SelectStep) -> str:
    return step.value.removeprefix("{{inputs.").removesuffix("}}")


def compile_recording(recording: RecordingCreate, *, name: str | None = None) -> Workflow:
    events: list[RecordedEvent] = sorted(recording.events, key=lambda e: e.seq)
    if not any(e.type == "navigation" for e in events):
        raise CompilationError("The recording has no navigation event, so it has no starting page")

    b = _Builder()
    origins: list[str] = []
    current_path: str | None = None
    last_action: RecordedEvent | None = None
    last_observation: RecordedEvent | None = None

    for ev in events:
        origin = display_origin(ev.url)
        if origin not in origins:
            origins.append(origin)

        if ev.type == "navigation":
            path = urlsplit(ev.url).path or "/"
            if path == current_path:
                continue
            caused_by_action = last_action is not None and 0 <= ev.ts - last_action.ts <= NAV_WINDOW_MS
            if b.steps and caused_by_action:
                b.steps.append(WaitForUrlStep(id=b.next_id(), action="wait_for_url", path=path))
            else:
                b.steps.append(NavigateStep(id=b.next_id(), action="navigate", url=ev.url))
            current_path = path
            continue

        if ev.type == "observation":
            last_observation = ev
            continue

        if not b.steps:
            # Actions before the first page load cannot be replayed from a known state.
            continue

        target = build_target(ev.target)
        previous = b.steps[-1]
        if ev.type in {"input", "select"}:
            step_cls, action = (FillStep, "fill") if ev.type == "input" else (SelectStep, "select")
            if isinstance(previous, step_cls) and previous.target == target:
                # The user edited the same field again: keep only the final value.
                b.update_input_default(_input_name_of(previous), ev.value, ev.sensitive)
            else:
                input_name = b.add_input(ev.target, ev.value, ev.sensitive)
                b.steps.append(
                    step_cls(id=b.next_id(), action=action, target=target, value=f"{{{{inputs.{input_name}}}}}")
                )
        elif ev.type == "check":
            if isinstance(previous, CheckStep) and previous.target == target:
                b.steps[-1] = previous.model_copy(update={"checked": ev.checked})
            else:
                b.steps.append(CheckStep(id=b.next_id(), action="check", target=target, checked=ev.checked))
        elif ev.type == "click":
            b.steps.append(ClickStep(id=b.next_id(), action="click", target=target))
        elif ev.type == "press":
            b.steps.append(PressStep(id=b.next_id(), action="press", target=target, key=ev.key))
        last_action = ev

    verify: list = []
    if last_observation is not None and (last_action is None or last_observation.seq > last_action.seq):
        # Plain truncation (no ellipsis): text_visible is a substring match.
        expected = " ".join(last_observation.text.split())[:200]
        if expected:
            verify.append(TextVisible(type="text_visible", text=expected))
    if current_path is not None:
        verify.append(UrlPath(type="url_path", path=current_path))

    return Workflow(
        name=name or recording.name,
        description=None,
        adapter="web",
        allowed_origins=origins,
        inputs=b.inputs,
        steps=b.steps,
        verify=verify,
    )

"""GhostFlow Workflow DSL v0.1.

A workflow is pure data: a closed set of actions, each with validated fields. There is no
"evaluate", "script" or free-form selector-engine escape hatch, so neither a user nor a
model can make the executor run arbitrary code.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ghostflow.dsl.templating import TemplateError, template_references
from ghostflow.security.urls import UrlNotAllowedError, UrlPolicy, canonical_origin

DSL_VERSION = "0.1"

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ValueText = Annotated[str, StringConstraints(max_length=2000)]
StepId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")]
InputName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
UrlText = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
PathText = Annotated[str, StringConstraints(pattern=r"^/[^\s?#]{0,1023}$")]

# ARIA roles the executor knows how to resolve with get_by_role.
AriaRole = Literal[
    "button", "link", "textbox", "searchbox", "combobox", "listbox", "option",
    "checkbox", "radio", "switch", "tab", "menuitem", "spinbutton", "slider", "row", "cell",
]
Key = Literal["Enter", "Tab", "Escape"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Locators ----------------------------------------------------------------------------


class TestIdStrategy(_Strict):
    __test__ = False  # not a pytest class
    by: Literal["test_id"]
    value: ShortText


class RoleStrategy(_Strict):
    by: Literal["role"]
    role: AriaRole
    name: ShortText | None = None


class LabelStrategy(_Strict):
    by: Literal["label"]
    value: ShortText


class PlaceholderStrategy(_Strict):
    by: Literal["placeholder"]
    value: ShortText


class TextStrategy(_Strict):
    by: Literal["text"]
    value: ShortText


class CssStrategy(_Strict):
    """A plain CSS selector. The executor always prefixes it with `css=`, so Playwright's
    other selector engines (xpath, internal:*, etc.) cannot be reached through it."""

    by: Literal["css"]
    value: Annotated[str, StringConstraints(min_length=1, max_length=500)]


LocatorStrategy = Annotated[
    Union[TestIdStrategy, RoleStrategy, LabelStrategy, PlaceholderStrategy, TextStrategy, CssStrategy],
    Field(discriminator="by"),
]


class Target(_Strict):
    """An element, described by several locator strategies in priority order.

    The executor uses the first strategy that matches exactly one element. Falling back to
    a later strategy is how a workflow survives some UI changes (renamed ids, removed
    test ids, restructured markup)."""

    description: ShortText | None = None
    strategies: list[LocatorStrategy] = Field(min_length=1, max_length=8)


# --- Steps -------------------------------------------------------------------------------


class _Step(_Strict):
    id: StepId
    description: ShortText | None = None


class NavigateStep(_Step):
    action: Literal["navigate"]
    url: UrlText


class ClickStep(_Step):
    action: Literal["click"]
    target: Target


class FillStep(_Step):
    action: Literal["fill"]
    target: Target
    value: ValueText


class SelectStep(_Step):
    action: Literal["select"]
    target: Target
    value: ValueText


class CheckStep(_Step):
    action: Literal["check"]
    target: Target
    checked: bool = True


class PressStep(_Step):
    action: Literal["press"]
    target: Target
    key: Key


class WaitForUrlStep(_Step):
    """Wait until the page URL path equals `path` (query string and fragment ignored)."""

    action: Literal["wait_for_url"]
    path: PathText


Step = Annotated[
    Union[NavigateStep, ClickStep, FillStep, SelectStep, CheckStep, PressStep, WaitForUrlStep],
    Field(discriminator="action"),
]

ACTIONS_WITH_TEMPLATES = (FillStep, SelectStep)


# --- Verification ------------------------------------------------------------------------


class TextVisible(_Strict):
    type: Literal["text_visible"]
    text: ShortText


class UrlPath(_Strict):
    type: Literal["url_path"]
    path: PathText


class ElementText(_Strict):
    type: Literal["element_text"]
    target: Target
    contains: ShortText


Verification = Annotated[Union[TextVisible, UrlPath, ElementText], Field(discriminator="type")]


# --- Workflow ----------------------------------------------------------------------------


class WorkflowInput(_Strict):
    name: InputName
    label: ShortText | None = None
    sensitive: bool = False
    default: ValueText | None = None

    @model_validator(mode="after")
    def _sensitive_inputs_have_no_default(self) -> "WorkflowInput":
        if self.sensitive and self.default is not None:
            raise ValueError(f"Sensitive input '{self.name}' cannot have a default value")
        return self

    @property
    def required(self) -> bool:
        return self.default is None


class Workflow(_Strict):
    dsl_version: Literal["0.1"] = DSL_VERSION
    name: ShortText
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    # Execution adapter that runs this workflow. Only "web" exists today; "windows" and
    # "api" adapters plug in behind the same contract (see execution/base.py).
    adapter: Literal["web"] = "web"
    allowed_origins: list[UrlText] = Field(min_length=1, max_length=10)
    inputs: list[WorkflowInput] = Field(default_factory=list, max_length=50)
    steps: list[Step] = Field(min_length=1, max_length=200)
    verify: list[Verification] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _check_consistency(self) -> "Workflow":
        step_ids = [s.id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Step ids must be unique")

        input_names = [i.name for i in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("Input names must be unique")

        try:
            policy = UrlPolicy(self.allowed_origins)
        except (UrlNotAllowedError, ValueError) as exc:
            raise ValueError(f"Invalid allowed_origins: {exc}") from None

        declared = set(input_names)
        for step in self.steps:
            if isinstance(step, NavigateStep):
                if "{{" in step.url:
                    raise ValueError(f"Step '{step.id}': templates are not allowed in URLs")
                if not policy.is_allowed(step.url):
                    raise ValueError(f"Step '{step.id}': URL is outside the workflow's allowed_origins")
            if isinstance(step, ACTIONS_WITH_TEMPLATES):
                try:
                    refs = template_references(step.value)
                except TemplateError as exc:
                    raise ValueError(f"Step '{step.id}': {exc}") from None
                unknown = refs - declared
                if unknown:
                    raise ValueError(f"Step '{step.id}' references undeclared inputs: {sorted(unknown)}")
        return self

    def canonical_origins(self) -> list[str]:
        return sorted({canonical_origin(o) for o in self.allowed_origins})

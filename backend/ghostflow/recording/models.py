"""Structured events produced by the Chrome extension recorder."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Short = Annotated[str, StringConstraints(max_length=200)]

EventType = Literal["navigation", "click", "input", "select", "check", "press", "observation"]
_NEEDS_TARGET = {"click", "input", "select", "check", "press"}


class ElementDescriptor(BaseModel):
    """Semantic description of the element a user interacted with.

    Locator strategies are derived from this server-side (see recording/compiler.py), so the
    priority order lives in one place."""

    model_config = ConfigDict(extra="forbid")

    tag: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    role: Annotated[str, StringConstraints(max_length=64)] | None = None
    accessible_name: Short | None = None
    label: Short | None = None
    test_id: Short | None = None
    element_id: Short | None = None
    name_attr: Short | None = None
    placeholder: Short | None = None
    text: Short | None = None
    input_type: Annotated[str, StringConstraints(max_length=32)] | None = None
    autocomplete: Annotated[str, StringConstraints(max_length=100)] | None = None
    value_attr: Short | None = None  # radio/checkbox `value` attribute (not user data)
    css: Annotated[str, StringConstraints(max_length=500)] | None = None


class RecordedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    ts: int = Field(ge=0, description="Epoch milliseconds")
    type: EventType
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    title: Short | None = None
    target: ElementDescriptor | None = None
    value: Annotated[str, StringConstraints(max_length=2000)] | None = None
    checked: bool | None = None
    key: Literal["Enter", "Tab", "Escape"] | None = None
    text: Annotated[str, StringConstraints(max_length=500)] | None = None
    sensitive: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "RecordedEvent":
        if self.type in _NEEDS_TARGET and self.target is None:
            raise ValueError(f"'{self.type}' events need a target")
        if self.type == "press" and self.key is None:
            raise ValueError("'press' events need a key")
        if self.type == "check" and self.checked is None:
            raise ValueError("'check' events need 'checked'")
        if self.type == "observation" and not self.text:
            raise ValueError("'observation' events need text")
        if self.value is not None and self.type not in {"input", "select"}:
            raise ValueError(f"'{self.type}' events cannot carry a value")
        return self


class RecordingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    source: Literal["chrome-extension", "fixture", "api"] = "chrome-extension"
    extension_version: Annotated[str, StringConstraints(max_length=32)] | None = None
    events: list[RecordedEvent] = Field(min_length=1, max_length=2000)

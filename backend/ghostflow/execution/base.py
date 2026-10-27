"""Execution adapter contract.

An adapter turns a validated Workflow into actions on a concrete surface. "web" is the only
adapter today; a Windows desktop or HTTP API adapter implements the same Protocol and is
registered under the workflow's `adapter` value, without touching the DSL core or the API.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ghostflow.dsl.models import Workflow
from ghostflow.execution.recovery import RecoveryAttempt


class InputError(ValueError):
    pass


class StepResult(BaseModel):
    step_id: str
    action: str
    status: Literal["passed", "failed", "skipped"]
    duration_ms: int = 0
    strategy_index: int | None = None
    strategy_by: str | None = None
    # How the element was found: the recorded primary locator, a later recorded locator
    # (deterministic fallback) or a candidate proposed by the AI and validated (ai_recovery).
    resolution: Literal["primary", "deterministic_fallback", "ai_recovery"] | None = None
    # GF-001 meaning, kept for compatibility: a later *recorded* locator was used. Never set by
    # AI recovery; see `resolution` and `recovery`.
    healed: bool = False
    recovery: RecoveryAttempt | None = None
    error: str | None = None


class VerificationResult(BaseModel):
    index: int
    type: str
    status: Literal["passed", "failed", "skipped"]
    detail: str | None = None


class RunResult(BaseModel):
    adapter: str
    status: Literal["passed", "failed", "error"]
    started_at: datetime
    finished_at: datetime
    steps: list[StepResult] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)
    blocked_requests: list[str] = Field(default_factory=list)
    error: str | None = None


class ExecutionAdapter(Protocol):
    kind: str

    def execute(self, workflow: Workflow, inputs: Mapping[str, str]) -> RunResult: ...


def resolve_inputs(workflow: Workflow, provided: Mapping[str, str]) -> dict[str, str]:
    """Merge provided values with defaults; reject unknown or missing inputs."""
    declared = {i.name: i for i in workflow.inputs}
    unknown = set(provided) - set(declared)
    if unknown:
        raise InputError(f"Unknown inputs: {sorted(unknown)}")
    resolved: dict[str, str] = {}
    missing = []
    for name, spec in declared.items():
        if name in provided:
            resolved[name] = provided[name]
        elif spec.default is not None:
            resolved[name] = spec.default
        else:
            missing.append(name)
    if missing:
        raise InputError(f"Missing required inputs: {sorted(missing)}")
    return resolved


def redact_inputs(workflow: Workflow, provided: Mapping[str, str]) -> dict[str, str]:
    sensitive = {i.name for i in workflow.inputs if i.sensitive}
    return {k: ("***" if k in sensitive else v) for k, v in provided.items()}

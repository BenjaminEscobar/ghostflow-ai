"""Workflow annotation: the first AI task.

The model sees a privacy-reduced view of the workflow (no input values, no URLs' query
strings) and may only propose human-readable metadata. Actions, targets, URLs and
verifications are never taken from the model.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ghostflow.ai.base import ModelProvider, ModelProviderError
from ghostflow.dsl.models import Workflow

Text200 = Annotated[str, StringConstraints(min_length=1, max_length=200)]

TASK = "annotate_workflow"
INSTRUCTION = (
    "You document business process automations. Given a recorded web workflow, return JSON with a "
    "short imperative name, a one-paragraph description, a one-line description per step id and a "
    "human-friendly label per input name. Only use step ids and input names that appear in the payload."
)


class StepNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[str, StringConstraints(max_length=40)]
    description: Text200


class InputNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(max_length=40)]
    label: Text200


class WorkflowAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text200
    description: Annotated[str, StringConstraints(max_length=1000)]
    steps: list[StepNote] = Field(default_factory=list, max_length=200)
    inputs: list[InputNote] = Field(default_factory=list, max_length=50)


def annotation_payload(workflow: Workflow) -> dict:
    """What the model is allowed to see. Input values are deliberately excluded."""
    steps = []
    for s in workflow.steps:
        item = {"id": s.id, "action": s.action}
        target = getattr(s, "target", None)
        if target is not None and target.description:
            item["target"] = target.description
        if s.action == "wait_for_url":
            item["path"] = s.path
        if s.action == "navigate":
            item["url"] = s.url.split("?", 1)[0]
        steps.append(item)
    return {
        "name": workflow.name,
        "steps": steps,
        "inputs": [{"name": i.name, "label": i.label, "sensitive": i.sensitive} for i in workflow.inputs],
        "verify": [v.model_dump(exclude={"target"}) for v in workflow.verify],
    }


def apply_annotation(workflow: Workflow, note: WorkflowAnnotation) -> Workflow:
    step_notes = {n.id: n.description for n in note.steps}
    input_notes = {n.name: n.label for n in note.inputs}
    steps = [s.model_copy(update={"description": step_notes[s.id]}) if s.id in step_notes else s for s in workflow.steps]
    inputs = [i.model_copy(update={"label": input_notes[i.name]}) if i.name in input_notes else i for i in workflow.inputs]
    data = workflow.model_dump()
    data.update(
        name=note.name,
        description=note.description or workflow.description,
        steps=[s.model_dump() for s in steps],
        inputs=[i.model_dump() for i in inputs],
    )
    # Re-validate the whole document: an annotation can never produce an invalid workflow.
    return Workflow.model_validate(data)


def annotate_workflow(workflow: Workflow, provider: ModelProvider) -> Workflow:
    try:
        note = provider.generate_structured(
            task=TASK, instruction=INSTRUCTION, payload=annotation_payload(workflow), response_model=WorkflowAnnotation
        )
        return apply_annotation(workflow, note)
    except ValidationError as exc:
        raise ModelProviderError(f"Model output rejected by contract: {exc.error_count()} validation error(s)") from None

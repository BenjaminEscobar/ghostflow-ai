import copy
import json

import pytest
from conftest import SCHEMA_FILE
from pydantic import ValidationError

from ghostflow.dsl import Workflow
from ghostflow.dsl.schema import workflow_json_schema
from ghostflow.dsl.templating import TemplateError, render, template_references

BASE = {
    "name": "Demo",
    "allowed_origins": ["http://localhost:8080"],
    "inputs": [
        {"name": "customer", "default": "Ana"},
        {"name": "password", "sensitive": True},
    ],
    "steps": [
        {"id": "s1", "action": "navigate", "url": "http://localhost:8080/index.html"},
        {
            "id": "s2",
            "action": "fill",
            "target": {"strategies": [{"by": "label", "value": "Usuario"}]},
            "value": "{{inputs.customer}}",
        },
        {
            "id": "s3",
            "action": "fill",
            "target": {"strategies": [{"by": "css", "value": "#password"}]},
            "value": "{{ inputs.password }}",
        },
        {"id": "s4", "action": "click", "target": {"strategies": [{"by": "role", "role": "button", "name": "Ingresar"}]}},
        {"id": "s5", "action": "wait_for_url", "path": "/dashboard.html"},
    ],
    "verify": [{"type": "url_path", "path": "/dashboard.html"}, {"type": "text_visible", "text": "Panel"}],
}


def mutate(fn) -> dict:
    doc = copy.deepcopy(BASE)
    fn(doc)
    return doc


def test_valid_workflow_round_trips():
    wf = Workflow.model_validate(BASE)
    assert wf.dsl_version == "0.1" and wf.adapter == "web"
    assert Workflow.model_validate_json(wf.model_dump_json()) == wf


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda d: d["steps"].append({"id": "s9", "action": "evaluate", "script": "alert(1)"}), id="unknown-action"),
        pytest.param(lambda d: d["steps"][3].update(script="alert(1)"), id="extra-field"),
        pytest.param(lambda d: d["steps"][3]["target"]["strategies"].append({"by": "xpath", "value": "//a"}), id="xpath"),
        pytest.param(lambda d: d["steps"][0].update(url="http://evil.test/"), id="url-outside-origins"),
        pytest.param(lambda d: d["steps"][0].update(url="javascript:alert(1)"), id="javascript-url"),
        pytest.param(lambda d: d["steps"][0].update(url="http://localhost:8080/{{inputs.customer}}"), id="templated-url"),
        pytest.param(lambda d: d.update(allowed_origins=["http://169.254.169.254"]), id="metadata-origin"),
        pytest.param(lambda d: d["steps"][1].update(value="{{inputs.nope}}"), id="undeclared-input"),
        pytest.param(lambda d: d["steps"][1].update(value="{{ 7*7 }}"), id="expression-template"),
        pytest.param(lambda d: d["steps"][4].update(id="s1"), id="duplicate-step-id"),
        pytest.param(lambda d: d["inputs"][1].update(default="hunter2"), id="sensitive-default"),
        pytest.param(lambda d: d["steps"][3]["target"].update(strategies=[]), id="no-strategies"),
        pytest.param(lambda d: d.update(adapter="windows"), id="adapter-not-implemented"),
        pytest.param(lambda d: d.update(steps=[]), id="no-steps"),
        pytest.param(lambda d: d["steps"][4].update(path="dashboard.html"), id="relative-path"),
    ],
)
def test_invalid_workflows_are_rejected(change):
    with pytest.raises(ValidationError):
        Workflow.model_validate(mutate(change))


def test_too_many_steps_rejected():
    def many(d):
        d["steps"] = [{"id": f"s{i}", "action": "wait_for_url", "path": "/"} for i in range(201)]

    with pytest.raises(ValidationError):
        Workflow.model_validate(mutate(many))


def test_templating():
    assert template_references("Hola {{inputs.a}} y {{ inputs.b }}") == {"a", "b"}
    assert render("Hola {{inputs.a}}", {"a": "Ana"}) == "Hola Ana"
    # Rendered values are inserted literally, never re-evaluated.
    assert render("{{inputs.a}}", {"a": "{{inputs.b}}"}) == "{{inputs.b}}"
    with pytest.raises(TemplateError):
        template_references("{{ __import__('os') }}")


def test_committed_json_schema_is_up_to_date():
    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert committed == workflow_json_schema(), "Run `make schema` to regenerate schemas/workflow.schema.json"

import json

import httpx
import pytest
from pydantic import SecretStr
from scenario import CUSTOMER, demo_recording

from ghostflow.ai import MetaModelProvider, MockModelProvider, ModelProviderError, annotate_workflow, provider_from_settings
from ghostflow.ai.annotate import WorkflowAnnotation, annotation_payload
from ghostflow.config import Settings
from ghostflow.recording import RecordingCreate, compile_recording

BASE = "http://localhost:8080"


@pytest.fixture
def workflow():
    return compile_recording(RecordingCreate.model_validate(demo_recording(BASE)))


def test_payload_sent_to_model_has_no_input_values(workflow):
    payload = json.dumps(annotation_payload(workflow), ensure_ascii=False)
    assert CUSTOMER not in payload
    assert "facturacion" not in payload


def test_mock_annotation_only_changes_metadata(workflow):
    annotated = annotate_workflow(workflow, MockModelProvider())
    assert annotated.description and all(s.description for s in annotated.steps)
    strip = lambda wf: [s.model_dump(exclude={"description"}) for s in wf.steps]  # noqa: E731
    assert strip(annotated) == strip(workflow)
    assert annotated.verify == workflow.verify and annotated.allowed_origins == workflow.allowed_origins


def test_model_cannot_inject_actions_or_unknown_steps(workflow):
    def hostile(payload):
        return {
            "name": "x",
            "description": "y",
            "steps": [{"id": "s999", "description": "ignored"}],
            "inputs": [],
            "action": "evaluate",  # not part of the contract
        }

    with pytest.raises(ModelProviderError):
        annotate_workflow(workflow, MockModelProvider({"annotate_workflow": hostile}))

    def unknown_step(payload):
        return {"name": "x", "description": "y", "steps": [{"id": "s999", "description": "ignored"}]}

    annotated = annotate_workflow(workflow, MockModelProvider({"annotate_workflow": unknown_step}))
    assert [s.id for s in annotated.steps] == [s.id for s in workflow.steps]


def _meta(handler) -> MetaModelProvider:
    return MetaModelProvider(
        api_key=SecretStr("test-key"),
        base_url="https://api.llama.example.test/compat/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


ANNOTATION = {"name": "Registrar solicitud", "description": "d", "steps": [], "inputs": []}


@pytest.mark.parametrize(
    "body",
    [
        {"choices": [{"message": {"role": "assistant", "content": json.dumps(ANNOTATION)}}]},
        {"completion_message": {"content": {"type": "text", "text": json.dumps(ANNOTATION)}}},
    ],
    ids=["openai-compatible", "llama-native"],
)
def test_meta_provider_request_and_response_contract(body):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=body)

    out = _meta(handler).generate_structured(
        task="annotate_workflow", instruction="sys", payload={"a": 1}, response_model=WorkflowAnnotation
    )
    assert out.name == "Registrar solicitud"
    assert seen["url"] == "https://api.llama.example.test/compat/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "bad key"}),
        httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"name": "x"})}}]}),
        httpx.Response(200, json={"unexpected": True}),
    ],
    ids=["http-error", "non-json", "schema-mismatch", "unknown-shape"],
)
def test_meta_provider_errors_are_contained(response):
    with pytest.raises(ModelProviderError) as exc:
        _meta(lambda request: response).generate_structured(
            task="t", instruction="i", payload={}, response_model=WorkflowAnnotation
        )
    assert "test-key" not in str(exc.value)


def test_meta_provider_requires_https():
    with pytest.raises(ValueError):
        MetaModelProvider(api_key=SecretStr("k"), base_url="http://api.example.test", model="m")


def test_provider_selection_defaults_to_mock():
    assert provider_from_settings(Settings()).name == "mock"
    with pytest.raises(ValueError):
        provider_from_settings(Settings(ai_provider="meta", meta_api_key=None))
    assert provider_from_settings(Settings(ai_provider="meta", meta_api_key="k")).name == "meta"

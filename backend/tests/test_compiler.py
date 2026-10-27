import pytest
from scenario import CUSTOMER, DEMO_PASSWORD, SUCCESS_TEXT, demo_recording

from ghostflow.dsl import CheckStep, ClickStep, FillStep, NavigateStep, SelectStep, WaitForUrlStep
from ghostflow.recording import RecordingCreate, RecordingRejectedError, compile_recording, sanitize_recording
from ghostflow.recording.compiler import CompilationError
from ghostflow.security import UrlPolicy

BASE = "http://localhost:8080"
POLICY = UrlPolicy([BASE])


def compiled(**kwargs):
    rec = sanitize_recording(RecordingCreate.model_validate(demo_recording(BASE, **kwargs)), POLICY)
    return compile_recording(rec)


def test_demo_recording_compiles_to_expected_steps():
    wf = compiled()
    assert [type(s) for s in wf.steps] == [
        NavigateStep, FillStep, FillStep, ClickStep, WaitForUrlStep,
        ClickStep, WaitForUrlStep,
        FillStep, FillStep, SelectStep, CheckStep, FillStep, CheckStep, ClickStep, WaitForUrlStep,
    ]
    assert wf.steps[0].url == f"{BASE}/index.html"
    assert [s.path for s in wf.steps if isinstance(s, WaitForUrlStep)] == [
        "/dashboard.html", "/new-request.html", "/confirmation.html",
    ]
    assert wf.allowed_origins == [BASE]
    assert [v.model_dump() for v in wf.verify] == [
        {"type": "text_visible", "text": SUCCESS_TEXT},
        {"type": "url_path", "path": "/confirmation.html"},
    ]


def test_values_become_inputs_and_passwords_are_runtime_only():
    wf = compiled()
    inputs = {i.name: i for i in wf.inputs}
    assert inputs["nombre_del_cliente"].default == CUSTOMER
    assert inputs["categoria"].default == "facturacion"
    assert inputs["contrasena"].sensitive and inputs["contrasena"].default is None
    assert DEMO_PASSWORD not in wf.model_dump_json()


def test_targets_carry_fallback_strategies_in_priority_order():
    wf = compiled()
    submit = wf.steps[13].target
    assert [s.by for s in submit.strategies] == ["test_id", "role", "css", "text"]
    radio = wf.steps[10].target
    assert [s.model_dump(exclude_none=True) for s in radio.strategies][:3] == [
        {"by": "role", "role": "radio", "name": "Alta"},
        {"by": "label", "value": "Alta"},
        {"by": "css", "value": 'input[name="priority"][value="alta"]'},
    ]


def test_sanitizer_strips_leaked_password_server_side():
    rec = sanitize_recording(RecordingCreate.model_validate(demo_recording(BASE, leak_password=True)), POLICY)
    password_event = rec.events[2]
    assert password_event.sensitive and password_event.value is None
    assert DEMO_PASSWORD not in rec.model_dump_json()


def test_sanitizer_rejects_events_outside_allowlist():
    data = demo_recording(BASE)
    data["events"][3]["url"] = "http://intranet.local/admin"
    with pytest.raises(RecordingRejectedError):
        sanitize_recording(RecordingCreate.model_validate(data), POLICY)


def test_repeated_edits_of_one_field_keep_last_value():
    data = demo_recording(BASE)
    again = dict(data["events"][7], seq=7, value="Ana María Torres")
    data["events"].insert(8, again)
    for i, e in enumerate(data["events"]):
        e["seq"] = i
    wf = compile_recording(RecordingCreate.model_validate(data))
    assert len([s for s in wf.steps if isinstance(s, FillStep)]) == len([s for s in compiled().steps if isinstance(s, FillStep)])
    assert {i.name: i.default for i in wf.inputs}["nombre_del_cliente"] == "Ana María Torres"


def test_navigation_without_prior_action_is_explicit_navigate():
    data = demo_recording(BASE)
    # Make the dashboard load happen long after the login click (e.g. typed URL).
    for e in data["events"][4:]:
        e["ts"] += 60_000
    wf = compile_recording(RecordingCreate.model_validate(data))
    assert isinstance(wf.steps[4], NavigateStep) and wf.steps[4].url.endswith("/dashboard.html")


def test_recording_without_navigation_cannot_compile():
    data = demo_recording(BASE)
    data["events"] = [e for e in data["events"] if e["type"] != "navigation"]
    with pytest.raises(CompilationError):
        compile_recording(RecordingCreate.model_validate(data))


def test_event_shape_is_validated():
    data = demo_recording(BASE)
    data["events"][1].pop("target")
    with pytest.raises(ValueError):
        RecordingCreate.model_validate(data)

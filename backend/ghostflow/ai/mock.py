"""Deterministic offline provider used by default and in tests. Costs nothing, sends nothing.

Its interpretation is mechanical on purpose (it restates the UI: "Completar textbox ..."): it
is the baseline against which the real model's business-level interpretation is compared.
"""

from collections.abc import Callable

from ghostflow.ai.base import ModelProviderError, T

_VERBS = {
    "navigate": "Abrir",
    "click": "Hacer clic en",
    "fill": "Completar",
    "select": "Seleccionar en",
    "check": "Marcar",
    "press": "Presionar tecla en",
    "wait_for_url": "Esperar la página",
}
_KINDS = {"email": "email", "date": "date", "number": "number", "option": "choice", "code": "identifier", "withheld": "credential"}


def _annotate(payload: dict) -> dict:
    steps = payload.get("steps", [])
    notes = []
    for s in steps:
        subject = s.get("target") or s.get("path") or s.get("url") or ""
        notes.append(
            {
                "id": s["id"],
                "description": f"{_VERBS.get(s['action'], s['action'])} {subject}".strip()[:200],
                "relevance": "navigation" if s["action"] in {"navigate", "wait_for_url"} else "supporting",
            }
        )
    inputs = [
        {
            "name": i["name"],
            "label": (i.get("label") or i["name"].replace("_", " ").capitalize())[:200],
            "kind": _KINDS.get(i.get("value_shape", ""), "free_text"),
            "role": "credential" if i.get("sensitive") else "parameter",
        }
        for i in payload.get("inputs", [])
    ]
    fills = sum(1 for s in steps if s["action"] in {"fill", "select", "check"})
    checks = payload.get("success_checks", payload.get("verify", []))
    description = (
        f"Workflow aprendido de una demostración: {len(steps)} pasos, {fills} campos de formulario "
        f"y {len(checks)} verificaciones automáticas."
    )
    outcome = next((c.get("text") for c in checks if c.get("type") == "text_visible" and c.get("text")), "")
    return {
        "name": payload.get("process_name") or payload.get("name") or "Workflow grabado",
        "description": description,
        "purpose": description,
        "expected_outcome": outcome,
        "steps": notes,
        "inputs": inputs,
    }


class MockModelProvider:
    name = "mock"
    model = "mock-deterministic"

    def __init__(self, responders: dict[str, Callable[[dict], dict]] | None = None):
        self._responders = {"annotate_workflow": _annotate, **(responders or {})}
        self.calls: list[tuple[str, dict]] = []

    def generate_structured(self, *, task: str, instruction: str, payload: dict, response_model: type[T]) -> T:
        self.calls.append((task, payload))
        responder = self._responders.get(task)
        if responder is None:
            raise ModelProviderError(f"Mock provider has no responder for task '{task}'")
        return response_model.model_validate(responder(payload))

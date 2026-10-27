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


def _recover(payload: dict) -> dict:
    """Offline stand-in for recovery: no semantics, only the one deterministic clue it can use
    (a single link whose path is the page the workflow expects next). Anything else: no answer."""
    expected = payload.get("failed_step", {}).get("expected_next_path")
    links = [c for c in payload.get("candidates", []) if c.get("kind") == "link" and expected and c.get("href_path") == expected]
    if len(links) == 1:
        return {
            "candidate_id": links[0]["id"],
            "confidence": "high",
            "justification": f"[mock] Único enlace hacia {expected}, la página que el workflow espera a continuación.",
            "rejection_reason": "",
        }
    return {
        "candidate_id": "",
        "confidence": "none",
        "justification": "",
        "rejection_reason": "[mock] El proveedor offline no razona semánticamente y no hay un único enlace al destino esperado.",
    }


class MockModelProvider:
    name = "mock"
    model = "mock-deterministic"

    def __init__(self, responders: dict[str, Callable[[dict], dict]] | None = None):
        self._responders = {"annotate_workflow": _annotate, "recover_element": _recover, **(responders or {})}
        self.calls: list[tuple[str, dict]] = []

    def generate_structured(
        self, *, task: str, instruction: str, payload: dict, response_model: type[T], stats: dict | None = None
    ) -> T:
        self.calls.append((task, payload))
        if stats is not None:
            stats.update(latency_ms=0, requests=0)
        responder = self._responders.get(task)
        if responder is None:
            raise ModelProviderError(f"Mock provider has no responder for task '{task}'")
        return response_model.model_validate(responder(payload))

"""Contract between GhostFlow and any language-model provider.

Providers only ever return data validated against a Pydantic model. Nothing a model returns
is executed: callers decide which validated fields to apply (see ai/annotate.py).
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ModelProviderError(RuntimeError):
    pass


class ModelProvider(Protocol):
    name: str  # provider id, e.g. "meta"
    model: str  # model id used for every call, recorded as provenance

    def generate_structured(
        self, *, task: str, instruction: str, payload: dict, response_model: type[T], stats: dict | None = None
    ) -> T:
        """Ask the model for a JSON object matching `response_model` and return it validated.

        When `stats` is given, the provider fills it with what it knows about the call
        (latency_ms, prompt_tokens, completion_tokens, reasoning_tokens, requests), also on failure."""
        ...

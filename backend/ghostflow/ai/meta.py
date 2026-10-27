"""Meta Model API (Llama API) provider.

Prepared for integration, disabled by default (GHOSTFLOW_AI_PROVIDER=mock). Uses the
OpenAI-compatible chat completions endpoint with a JSON-schema response format. The parser
also accepts the native Llama API response shape (`completion_message`).

Before enabling it, confirm the endpoint, model id and structured-output support against
the current Llama API documentation.
"""

import json

import httpx
from pydantic import SecretStr, ValidationError

from ghostflow.ai.base import ModelProviderError, T


def _extract_text(body: dict) -> str:
    choices = body.get("choices")
    if choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str):
            return content
    completion = body.get("completion_message")
    if completion:
        content = completion.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content, str):
            return content
    raise ModelProviderError("Unrecognised response shape from Meta Model API")


class MetaModelProvider:
    name = "meta"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not base_url.startswith("https://"):
            raise ValueError("Meta Model API base URL must use https")
        self._api_key = api_key
        self._model = model
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)

    def generate_structured(self, *, task: str, instruction: str, payload: dict, response_model: type[T]) -> T:
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": task, "schema": response_model.model_json_schema()},
            },
        }
        try:
            response = self._client.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            )
        except httpx.HTTPError as exc:
            raise ModelProviderError(f"Meta Model API request failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise ModelProviderError(f"Meta Model API returned HTTP {response.status_code}")
        try:
            return response_model.model_validate_json(_extract_text(response.json()))
        except (ValueError, ValidationError):
            raise ModelProviderError("Meta Model API output did not match the expected contract") from None

    def close(self) -> None:
        self._client.close()

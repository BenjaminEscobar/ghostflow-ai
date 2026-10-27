"""Meta Model API provider (Muse Spark).

Calls `POST {base_url}/chat/completions` (https://dev.meta.ai/docs/protocols/chat-completions)
with a strict JSON-schema `response_format` (https://dev.meta.ai/docs/structured-output) and
validates the answer again with the Pydantic model before returning it.

Safety and cost controls:
- The API key lives only in the backend settings and is sent only in the Authorization header.
- `max_completion_tokens` caps reasoning + output; the request has a timeout and a payload size cap.
- Only 429/500/503 are retried, at most `max_retries` times, with a short bounded backoff.
- A per-process request cap makes unbounded spend impossible.
- "-contributor" models (Meta may train on the content) are refused unless explicitly allowed.
- Logs carry provider, model, task, status, latency and token counts. Never prompts, outputs,
  error bodies or the key.

The parser also accepts the native Llama API shape (`completion_message`), so the provider
keeps working if `base_url` points at a compatible Meta endpoint.
"""

import json
import logging
import threading
import time
from typing import Literal

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from ghostflow.ai.base import ModelProviderError, T
from ghostflow.security.urls import UrlNotAllowedError, UrlPolicy

log = logging.getLogger("ghostflow.ai")

MAX_PAYLOAD_CHARS = 60_000
_RETRYABLE = {429, 500, 503}
_MAX_BACKOFF_S = 5.0

ReasoningEffort = Literal["minimal", "low", "medium", "high"]


def is_contributor_model(model: str) -> bool:
    return "contributor" in model.lower()


def strict_json_schema(model: type[BaseModel]) -> dict:
    """JSON schema accepted by strict structured output: every object lists all its properties
    as required and forbids extra ones. Fields with defaults stay optional when *we* validate."""

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            node.pop("default", None)
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)
        return node

    return visit(model.model_json_schema())


def _extract(body: dict) -> tuple[str, str | None]:
    """Return (text, finish_reason) from an OpenAI-compatible or native Llama API response."""
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content, choices[0].get("finish_reason")
    completion = body.get("completion_message")
    if isinstance(completion, dict):
        content = completion.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            return content["text"], completion.get("stop_reason")
        if isinstance(content, str):
            return content, completion.get("stop_reason")
    raise ModelProviderError("Unrecognised response shape from Meta Model API")


def _error_kind(response: httpx.Response) -> str:
    """Meta's error `type`/`code` (short identifiers, e.g. rate_limit_exceeded). Never the message."""
    try:
        err = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return ""
    kind = err.get("code") or err.get("type") if isinstance(err, dict) else None
    return f" ({str(kind)[:60]})" if kind else ""


def _usage(body: dict) -> dict:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
    }


class MetaModelProvider:
    name = "meta"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str = "https://api.meta.ai/v1",
        model: str = "muse-spark-1.3",
        timeout_s: float = 60.0,
        max_output_tokens: int = 4000,
        reasoning_effort: ReasoningEffort | None = "low",
        max_retries: int = 1,
        max_requests: int = 50,
        allow_contributor_mode: bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        if not base_url.startswith("https://"):
            raise ValueError("Meta Model API base URL must use https")
        try:
            UrlPolicy([base_url])  # rejects cloud-metadata and link-local hosts
        except UrlNotAllowedError as exc:
            raise ValueError(f"Meta Model API base URL rejected: {exc}") from None
        if not api_key.get_secret_value().strip():
            raise ValueError("Meta Model API key is empty")
        if is_contributor_model(model) and not allow_contributor_mode:
            raise ValueError(
                f"Model '{model}' runs in Contributor mode (Meta may train on the data sent). "
                "Set GHOSTFLOW_META_ALLOW_CONTRIBUTOR_MODE=true only with explicit authorisation."
            )
        self.model = model
        self._api_key = api_key
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._max_retries = max_retries
        self._max_requests = max_requests
        self._sleep = sleep
        self._lock = threading.Lock()
        self.requests_sent = 0
        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)

    # -- public ---------------------------------------------------------------------------

    def generate_structured(self, *, task: str, instruction: str, payload: dict, response_model: type[T]) -> T:
        content = json.dumps(payload, ensure_ascii=False)
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ModelProviderError(f"Payload too large for the model ({len(content)} > {MAX_PAYLOAD_CHARS} chars)")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": content},
            ],
            "max_completion_tokens": self._max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": task, "strict": True, "schema": strict_json_schema(response_model)},
            },
        }
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort

        response, latency_ms = self._post_with_retries(task, body)
        try:
            data = response.json()
        except ValueError:
            raise ModelProviderError("Meta Model API returned a non-JSON body") from None
        usage = _usage(data)
        self._account(usage)
        log.info(
            "ai.call provider=meta model=%s task=%s status=%s latency_ms=%d prompt_tokens=%s completion_tokens=%s "
            "reasoning_tokens=%s",
            self.model, task, response.status_code, latency_ms,
            usage["prompt_tokens"], usage["completion_tokens"], usage["reasoning_tokens"],
        )  # fmt: skip

        text, finish_reason = _extract(data)
        if finish_reason == "length":
            raise ModelProviderError("Meta Model API output was truncated (raise GHOSTFLOW_META_MAX_OUTPUT_TOKENS)")
        try:
            return response_model.model_validate_json(text)
        except (ValueError, ValidationError):
            raise ModelProviderError("Meta Model API output did not match the expected contract") from None

    def close(self) -> None:
        self._client.close()

    # -- internals ------------------------------------------------------------------------

    def _reserve_request(self) -> None:
        with self._lock:
            if self.requests_sent >= self._max_requests:
                raise ModelProviderError(
                    f"Meta request cap reached ({self._max_requests}); raise GHOSTFLOW_META_MAX_REQUESTS deliberately"
                )
            self.requests_sent += 1

    def _account(self, usage: dict) -> None:
        with self._lock:
            for k, v in usage.items():
                if isinstance(v, int):
                    self.usage_totals[k] += v

    def _post_with_retries(self, task: str, body: dict) -> tuple[httpx.Response, int]:
        attempt = 0
        while True:
            self._reserve_request()
            started = time.monotonic()
            try:
                response = self._client.post(
                    "/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                )
            except httpx.TimeoutException:
                log.warning("ai.call provider=meta model=%s task=%s error=timeout", self.model, task)
                raise ModelProviderError("Meta Model API request timed out") from None
            except httpx.HTTPError as exc:
                log.warning("ai.call provider=meta model=%s task=%s error=%s", self.model, task, type(exc).__name__)
                raise ModelProviderError(f"Meta Model API request failed: {type(exc).__name__}") from None
            latency_ms = int((time.monotonic() - started) * 1000)
            if response.status_code == 200:
                return response, latency_ms

            kind = _error_kind(response)
            log.warning(
                "ai.call provider=meta model=%s task=%s status=%s%s latency_ms=%d attempt=%d",
                self.model, task, response.status_code, kind, latency_ms, attempt + 1,
            )  # fmt: skip
            delay = self._retry_delay(response, attempt)
            if delay is None:
                raise ModelProviderError(f"Meta Model API returned HTTP {response.status_code}{kind}")
            self._sleep(delay)
            attempt += 1

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float | None:
        if response.status_code not in _RETRYABLE or attempt >= self._max_retries:
            return None
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                return None
            # A long Retry-After (e.g. 60 s on overload) would block the request: give up instead.
            return seconds if 0 <= seconds <= _MAX_BACKOFF_S else None
        return min(_MAX_BACKOFF_S, 1.0 * 2**attempt)

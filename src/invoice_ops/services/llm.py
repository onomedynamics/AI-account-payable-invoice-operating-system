"""Minimal OpenRouter chat client for structured extraction.

One HTTP call per invocation. It does not retry or validate -- the extraction
service owns the validate-and-retry loop so that logic is testable without a
network. Callers depend on the :class:`StructuredLLM` protocol, not this class,
so a fake can be injected in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from invoice_ops.config import Settings, get_settings


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    raw: dict[str, Any]


class StructuredLLM(Protocol):
    def complete_json(
        self, *, system: str, user: str, json_schema: dict[str, Any]
    ) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if not self._settings.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        self._http = http_client or httpx.Client(timeout=self._settings.llm_timeout_seconds)

    def complete_json(self, *, system: str, user: str, json_schema: dict[str, Any]) -> LLMResponse:
        payload = {
            "model": self._settings.llm_model,
            "temperature": self._settings.llm_temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction_result",
                    # strict:false -> strong guidance, not a rigid all-required
                    # contract. Our Pydantic validation is the real guarantee.
                    "strict": False,
                    "schema": json_schema,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self._settings.openrouter_api_key}"}
        try:
            response = self._http.post(
                f"{self._settings.openrouter_base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}") from exc

        body: dict[str, Any] = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected OpenRouter response shape: {body}") from exc

        return LLMResponse(content=content, model=body.get("model", ""), raw=body)

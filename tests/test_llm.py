from __future__ import annotations

import json

import httpx
import pytest

from invoice_ops.config import get_settings
from invoice_ops.services.llm import LLMError, OpenRouterClient


def _client(handler, monkeypatch) -> OpenRouterClient:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    transport = httpx.MockTransport(handler)
    return OpenRouterClient(http_client=httpx.Client(transport=transport))


def test_happy_path_returns_message_content(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4o-mini",
                "choices": [{"message": {"content": '{"ok": true}'}}],
            },
        )

    client = _client(handler, monkeypatch)
    resp = client.complete_json(system="s", user="u", json_schema={"type": "object"})
    assert resp.content == '{"ok": true}'
    assert resp.model == "openai/gpt-4o-mini"


def test_http_error_becomes_llm_error(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    client = _client(handler, monkeypatch)
    with pytest.raises(LLMError, match="request failed"):
        client.complete_json(system="s", user="u", json_schema={})


def test_unexpected_shape_becomes_llm_error(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _client(handler, monkeypatch)
    with pytest.raises(LLMError, match="unexpected"):
        client.complete_json(system="s", user="u", json_schema={})


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterClient()

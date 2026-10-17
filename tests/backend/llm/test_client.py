import json

import httpx
import pytest
from pydantic import BaseModel

from backend.llm.client import LlmError, complete_structured


class _Thing(BaseModel):
    name: str
    count: int = 0


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _openrouter_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER", "test-key")


def test_complete_structured_parses_valid_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return _openrouter_response(json.dumps({"name": "nvidia", "count": 3}))

    result = complete_structured(
        "system prompt", "user query", _Thing, http_client=_client_with(handler)
    )

    assert result == _Thing(name="nvidia", count=3)


def test_complete_structured_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER", raising=False)
    with pytest.raises(LlmError, match="OPENROUTER"):
        complete_structured("system", "query", _Thing)


def test_complete_structured_raises_on_invalid_json_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return _openrouter_response("not valid json for this schema")

    with pytest.raises(LlmError, match="didn't match"):
        complete_structured("system", "query", _Thing, http_client=_client_with(handler))


def test_complete_structured_raises_on_missing_required_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return _openrouter_response(json.dumps({"count": 3}))  # missing required "name"

    with pytest.raises(LlmError, match="didn't match"):
        complete_structured("system", "query", _Thing, http_client=_client_with(handler))


def test_complete_structured_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    with pytest.raises(LlmError, match="LLM request failed"):
        complete_structured("system", "query", _Thing, http_client=_client_with(handler))


def test_complete_structured_raises_on_unexpected_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(LlmError, match="Unexpected LLM response shape"):
        complete_structured("system", "query", _Thing, http_client=_client_with(handler))


def test_gemini_provider_uses_googles_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("OPENROUTER", raising=False)
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["Authorization"]
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"name": "x", "count": 1}'}}]})

    complete_structured("s", "u", _Thing, model="anthropic/claude-haiku-5.5",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["url"].startswith("https://generativelanguage.googleapis.com/")
    assert seen["auth"] == "Bearer g-key"
    assert seen["model"] == "gemini-2.5-flash"  # an OpenRouter model name is replaced, not sent to Google


def test_gemini_provider_without_key_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LlmError, match="GEMINI_API_KEY"):
        complete_structured("s", "u", _Thing)


def test_nicrron_is_the_default_when_its_key_is_set(monkeypatch):
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["Authorization"]
        seen["model"] = json.loads(request.content)["model"]
        return _openrouter_response(json.dumps({"name": "x"}))

    complete_structured("s", "u", _Thing, http_client=_client_with(handler))
    assert seen["url"] == "https://api.nicrron.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer n-key"
    assert seen["model"] == "anthropic/claude-haiku-5-5"  # OpenRouter-style dots rewritten for Nicrron


def test_nicrron_model_setting_overrides_the_callers_model(monkeypatch):
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")
    monkeypatch.setenv("NICRRON_MODEL", "nicrron/auto")
    seen = {}

    def handler(request):
        seen["model"] = json.loads(request.content)["model"]
        return _openrouter_response(json.dumps({"name": "x"}))

    complete_structured("s", "u", _Thing, model="anthropic/claude-haiku-5.5", http_client=_client_with(handler))
    assert seen["model"] == "nicrron/auto"


def test_fenced_json_is_accepted():
    def handler(request):
        return _openrouter_response('```json\n{"name": "nvidia", "count": 2}\n```')

    assert complete_structured("s", "u", _Thing, http_client=_client_with(handler)) == _Thing(name="nvidia", count=2)

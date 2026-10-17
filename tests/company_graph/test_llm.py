from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from backend.llm.client import LlmError
from company_graph import llm


class Reply(BaseModel):
    answer: str


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def use_anthropic(monkeypatch, response=None, error=None, model=None):
    monkeypatch.setenv("GRAPH_LLM_PROVIDER", "anthropic")
    if model:
        monkeypatch.setenv("GRAPH_ANTHROPIC_MODEL", model)
    else:
        monkeypatch.delenv("GRAPH_ANTHROPIC_MODEL", raising=False)
    messages = FakeMessages(response, error)
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def ok(parsed=None, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)


def test_anthropic_provider_parses_into_the_response_model(monkeypatch):
    messages = use_anthropic(monkeypatch, ok(Reply(answer="supplier")))
    assert llm.complete("sys", "user", Reply) == Reply(answer="supplier")
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_format"] is Reply
    assert call["system"] == "sys" and call["messages"] == [{"role": "user", "content": "user"}]
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]


def test_models_without_server_side_fallback_get_none(monkeypatch):
    messages = use_anthropic(monkeypatch, ok(Reply(answer="x")), model="claude-haiku-5-5")
    llm.complete("s", "u", Reply)
    assert messages.calls[0]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in messages.calls[0] and "betas" not in messages.calls[0]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_replies_raise_llm_error(monkeypatch, stop_reason):
    use_anthropic(monkeypatch, ok(None, stop_reason))
    with pytest.raises(LlmError):
        llm.complete("s", "u", Reply)


def test_api_errors_become_llm_error(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
    use_anthropic(monkeypatch, error=error)
    with pytest.raises(LlmError, match="ANTHROPIC_API_KEY"):
        llm.complete("s", "u", Reply)


def test_openrouter_is_still_the_default(monkeypatch):
    monkeypatch.delenv("GRAPH_LLM_PROVIDER", raising=False)
    seen = {}

    def fake(system, user, response_model, model):
        seen["model"] = model
        return response_model(answer="via openrouter")

    monkeypatch.setattr(llm._client, "complete_structured", fake)
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: pytest.fail("Anthropic must not be called"))
    assert llm.complete("s", "u", Reply).answer == "via openrouter"


def test_unknown_provider_stops_with_a_clear_message(monkeypatch):
    monkeypatch.setenv("GRAPH_LLM_PROVIDER", "gemini")
    with pytest.raises(SystemExit, match="GRAPH_LLM_PROVIDER"):
        llm.complete("s", "u", Reply)

import os

import pytest

import backend.classification.classifier as jev_classifier
from company_graph import llm

# backend.main calls load_dotenv(), so a developer's .env can leak into tests once any test
# imports it. Model settings must never decide what a test calls.
_MODEL_SETTINGS = ("GRAPH_LLM_PROVIDER", "GRAPH_LLM_MODEL", "GRAPH_ANTHROPIC_MODEL")
# Keys for outside services: cleared so no test can reach NewsAPI, Alpaca, OpenRouter or Anthropic
# or X with the developer's real credentials.
_SERVICE_KEYS = ("NEWSAPI_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "OPENROUTER", "ANTHROPIC_API_KEY",
                 "X_BEARER_TOKEN", "TYPESAFE_API_KEY")


@pytest.fixture(autouse=True)
def no_real_model_calls(monkeypatch):
    """Default provider (OpenRouter, faked by each test), no service keys, and no real model client."""
    if os.environ.get("GRAPH_LIVE_EVAL") == "1":  # opt-in live checks use the real settings
        return
    for name in _MODEL_SETTINGS + _SERVICE_KEYS:
        monkeypatch.delenv(name, raising=False)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to call a real model (Anthropic, OpenRouter or Jev)")

    monkeypatch.setattr(llm, "_get_anthropic_client", refuse)
    # Tests that exercise these patch them again themselves; the later patch wins.
    monkeypatch.setattr(llm._client, "complete_structured", refuse)
    monkeypatch.setattr(jev_classifier, "call_jev", refuse)

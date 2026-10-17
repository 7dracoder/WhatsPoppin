"""The one place this feature calls a free-form model (F4 extractor, F8 highlight builder).

Two providers, picked by GRAPH_LLM_PROVIDER:
- "openrouter" (default): the shared backend.llm.client.complete_structured, with the model
  from GRAPH_LLM_MODEL. That client goes through the Nicrron gateway when NICRRON_API_KEY is
  set, otherwise OpenRouter (OPENROUTER key).
- "anthropic": the Anthropic API directly through the official SDK, with the model from
  GRAPH_ANTHROPIC_MODEL. Uses ANTHROPIC_API_KEY. The reply is parsed into the Pydantic model
  with structured outputs, so it is always schema-valid JSON.

Synchronous either way: call it from async code through asyncio.to_thread. Every failure is
raised as backend.llm.client.LlmError, so callers handle both providers the same way.
"""

from typing import TypeVar

import anthropic
from pydantic import BaseModel

from backend.llm import client as _client
from backend.llm.client import LlmError
from company_graph import config

__all__ = ["complete", "LlmError"]

T = TypeVar("T", bound=BaseModel)

MAX_TOKENS = 16000  # thinking is always on for current Claude models and counts toward this
EFFORT = "low"      # reading one passage for one relationship is a simple extraction
# Models that accept server-side fallbacks (a declined request is re-run on another model).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_anthropic_client: anthropic.Anthropic | None = None


def complete(system: str, user: str, response_model: type[T], model: str | None = None) -> T:
    """Ask the model and parse its reply into `response_model`.

    `model` overrides the configured model for the chosen provider. The shared client and the
    Anthropic client are looked up at call time, so tests can monkeypatch either.
    """
    cfg = config.load()
    if cfg.graph_llm_provider == "anthropic":
        return _complete_anthropic(system, user, response_model, model or cfg.graph_anthropic_model)
    return _client.complete_structured(system, user, response_model, model=model or cfg.graph_llm_model)


def _get_anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY; retries 429 and 5xx itself
    return _anthropic_client


def _complete_anthropic(system: str, user: str, response_model: type[T], model: str) -> T:
    extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if model in FALLBACK_MODELS else {}
    try:
        response = _get_anthropic_client().beta.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=response_model,
            output_config={"effort": EFFORT},
            **extra,
        )
    except anthropic.AuthenticationError as exc:
        raise LlmError(f"Anthropic API key rejected (check ANTHROPIC_API_KEY): {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise LlmError(f"Anthropic request failed ({exc.status_code}): {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise LlmError(f"Anthropic connection failed: {exc}") from exc
    except anthropic.AnthropicError as exc:  # e.g. no API key configured
        raise LlmError(f"Anthropic client error: {exc}") from exc

    if response.stop_reason == "refusal":
        raise LlmError("the model declined this passage")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise LlmError(f"no parsable reply (stop_reason={response.stop_reason})")
    return response.parsed_output

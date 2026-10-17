import json

import httpx
import pytest

from backend.classification.jev_client import JevError, call_jev
from backend.classification.primitives import ChoiceAnswer, ChoiceQuestion, NoulAnswer, NoulQuestion


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _jev_response(answers: dict) -> httpx.Response:
    return httpx.Response(200, json={"answers": answers})


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    # TYPESAFE_API_KEY takes priority when set, so clear it explicitly — otherwise a real
    # value leaking in via backend.main's load_dotenv() (any test importing it in this same
    # pytest session) would silently win. These wire-contract tests run on the native Jev path.
    monkeypatch.delenv("NICRRON_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")


def test_call_jev_parses_noul_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["state"] == "some text"
        assert body["questions"]["main"]["type"] == "noul"
        return _jev_response({"main": {"type": "noul", "noul": 0.87}})

    result = call_jev(
        "some text",
        {"main": NoulQuestion(instructions="is this bullish?")},
        http_client=_client_with(handler),
    )

    assert result == {"main": NoulAnswer(noul=0.87)}


def test_call_jev_parses_choice_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        return _jev_response(
            {
                "main": {
                    "type": "choice",
                    "choice": "positive",
                    "probabilities": {"positive": 0.7, "negative": 0.3},
                    "confidence": 0.9,
                }
            }
        )

    result = call_jev(
        "some text",
        {"main": ChoiceQuestion(instructions="sentiment?", criteria={"positive": None, "negative": None})},
        http_client=_client_with(handler),
    )

    assert result["main"] == ChoiceAnswer(
        choice="positive", probabilities={"positive": 0.7, "negative": 0.3}, confidence=0.9
    )


def test_call_jev_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(JevError, match="NICRRON_API_KEY"):
        call_jev("text", {"main": NoulQuestion(instructions="q")})


def test_call_jev_prefers_typesafe_when_its_key_is_set(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer ts-test-key"
        body = json.loads(request.content)
        assert body["model"] == "jev-latest"
        return _jev_response({"main": {"type": "noul", "noul": 0.42}})

    result = call_jev(
        "some text",
        {"main": NoulQuestion(instructions="is this bullish?")},
        http_client=_client_with(handler),
    )

    assert result == {"main": NoulAnswer(noul=0.42)}


def test_call_jev_raises_on_unknown_answer_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return _jev_response({"main": {"type": "mystery"}})

    with pytest.raises(JevError, match="Unknown answer type"):
        call_jev("text", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler))


def test_call_jev_raises_on_invalid_answer_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return _jev_response({"main": {"type": "noul"}})  # missing required "noul" field

    with pytest.raises(JevError, match="didn't match"):
        call_jev("text", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler))


def test_call_jev_raises_on_missing_answer_for_question():
    def handler(request: httpx.Request) -> httpx.Response:
        return _jev_response({})  # no answer for "main" at all

    with pytest.raises(JevError, match="missing answers"):
        call_jev("text", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler))


def test_call_jev_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    with pytest.raises(JevError, match="Jev request failed"):
        call_jev("text", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler))


def test_call_jev_raises_on_unexpected_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(JevError, match="Unexpected Jev response shape"):
        call_jev("text", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler))


def _chat(content) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_call_jev_uses_nicrron_chat_and_returns_jev_shapes(monkeypatch):
    from backend.classification.primitives import ScoreAnswer, ScoreQuestion

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["Authorization"]
        body = json.loads(request.content)
        seen["model"], seen["user"] = body["model"], json.loads(body["messages"][1]["content"])
        return _chat({"answers": {
            "yes": {"type": "noul", "noul": 1.4},  # clipped to 1
            "pick": {"type": "choice", "probabilities": {"positive": 3, "negative": 1}},  # normalized
            "rate": {"type": "score", "probabilities": {"0": 0, "1": 1, "2": 1}},
        }})

    result = call_jev(
        "NVDA beats estimates",
        {
            "yes": NoulQuestion(instructions="bullish?"),
            "pick": ChoiceQuestion(instructions="sentiment?", criteria={"positive": None, "negative": None}),
            "rate": ScoreQuestion(instructions="impact?", criteria=["low", "mid", "high"]),
        },
        http_client=_client_with(handler),
    )

    assert seen["url"] == "https://api.nicrron.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer n-key" and seen["model"] == "nicrron/auto"
    assert seen["user"]["state"] == "NVDA beats estimates"
    assert result["yes"] == NoulAnswer(noul=1.0)
    assert result["pick"] == ChoiceAnswer(choice="positive", probabilities={"positive": 0.75, "negative": 0.25}, confidence=0.75)
    assert result["rate"] == ScoreAnswer(score=1.5, legend={"0": "low", "1": "mid", "2": "high"},
                                         probabilities={"0": 0.0, "1": 0.5, "2": 0.5}, confidence=0.5)


def test_call_jev_via_nicrron_raises_on_missing_answer(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"answers": {}})

    with pytest.raises(JevError, match="missing answers"):
        call_jev("text", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler))


def test_typesafe_still_wins_over_nicrron(monkeypatch):
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        return _jev_response({"main": {"type": "noul", "noul": 0.3}})

    assert call_jev("t", {"main": NoulQuestion(instructions="q")}, http_client=_client_with(handler)) == {"main": NoulAnswer(noul=0.3)}


def test_call_jev_via_nicrron_accepts_a_bare_number_for_yes_no(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"answers": {"a": 0.9, "b": {"probability": 0.2}}})

    result = call_jev("t", {"a": NoulQuestion(instructions="q"), "b": NoulQuestion(instructions="q")},
                      http_client=_client_with(handler))
    assert result == {"a": NoulAnswer(noul=0.9), "b": NoulAnswer(noul=0.2)}

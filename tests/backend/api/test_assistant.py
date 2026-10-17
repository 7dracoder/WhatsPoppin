import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api import assistant
from backend.llm.client import LlmError
from backend.main import app
from backend.observability import prism


@pytest.fixture(autouse=True)
def local_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(prism, "LOCAL_LOG", tmp_path / "runs.jsonl")
    monkeypatch.delenv("PRISM_INGEST_URL", raising=False)
    return tmp_path / "runs.jsonl"


client = TestClient(app)


def test_ask_returns_reply_and_actions_and_traces_the_run(monkeypatch, local_trace):
    seen = {}

    def fake_complete(system, user, model):
        seen["user"] = user
        return assistant.AssistantReply(reply="Pulling up Nvidia.", actions=[{"type": "search_company", "value": "NVDA"}])

    monkeypatch.setattr(assistant, "complete_structured", fake_complete)
    body = client.post("/assistant/ask", json={"message": "show me nvidia", "context": {"stage": "empty"},
                                                 "history": [{"role": "user", "text": "hi"}]}).json()
    assert body == {"reply": "Pulling up Nvidia.", "actions": [{"type": "search_company", "value": "NVDA"}]}
    assert '"stage": "empty"' in seen["user"] and "USER: show me nvidia" in seen["user"]
    import json
    run = json.loads(local_trace.read_text().splitlines()[-1])
    assert run["name"] == "assistant.turn" and run["status"] == "ok"
    assert [s["name"] for s in run["steps"]] == ["context.snapshot", "llm.complete_structured", "action.search_company"]


def test_unknown_action_types_are_rejected_by_the_schema():
    with pytest.raises(ValueError):
        assistant.AssistantReply(reply="x", actions=[{"type": "delete_everything"}])


def test_model_failure_is_a_502_and_a_failed_run(monkeypatch, local_trace):
    def boom(*args):
        raise LlmError("gateway down")

    monkeypatch.setattr(assistant, "complete_structured", boom)
    assert client.post("/assistant/ask", json={"message": "hi"}).status_code == 502
    import json
    run = json.loads(local_trace.read_text().splitlines()[-1])
    assert run["status"] == "error" and run["error"]["type"] == "LlmError"


def test_voice_routes_need_the_elevenlabs_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert client.post("/assistant/speak", json={"text": "hi"}).status_code == 503
    assert client.post("/assistant/transcribe", content=b"abc", headers={"content-type": "audio/webm"}).status_code == 503


def test_transcribe_rejects_empty_audio(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert client.post("/assistant/transcribe", content=b"", headers={"content-type": "audio/webm"}).status_code == 400


def test_speak_proxies_elevenlabs_without_exposing_the_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    seen = {}
    real = httpx.AsyncClient

    def handler(request):
        seen["key"], seen["url"] = request.headers["xi-api-key"], str(request.url)
        return httpx.Response(200, content=b"ID3mp3bytes", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = client.post("/assistant/speak", json={"text": "Nvidia is ready"})
    assert r.status_code == 200 and r.content == b"ID3mp3bytes" and r.headers["content-type"] == "audio/mpeg"
    assert seen["key"] == "secret-key" and "/text-to-speech/" in seen["url"]
    assert "secret-key" not in r.text

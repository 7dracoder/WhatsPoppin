import pytest

from backend.observability import prism


@pytest.fixture
def sent(tmp_path, monkeypatch):
    """Capture what would be POSTed to PRISM, synchronously."""
    monkeypatch.setattr(prism, "LOCAL_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setenv("PRISM_API_KEY", "pt-sk-test")
    monkeypatch.setenv("PRISM_PROJECT_ID", "proj-1")
    monkeypatch.setattr(prism.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    calls = []

    class Client:
        def __init__(self, base_url, headers, timeout):
            self.base_url, self.headers = base_url, headers

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, path, json):
            calls.append({"url": self.base_url + path, "headers": self.headers, "json": json})
            return type("R", (), {"raise_for_status": lambda self: None})()

    monkeypatch.setattr(prism.httpx, "Client", Client)
    return calls


def test_run_is_sent_as_trace_then_spans(sent):
    with prism.trace_run("assistant.turn", inputs={"message": "show me nvidia"}) as run:
        with run.step("context.snapshot", kind="retrieval") as step:
            step.output = {"context_chars": 10}
        with run.step("llm.complete_structured", kind="llm", model="nicrron/auto") as step:
            step.output = {"reply": "Opening Nvidia."}
        run.output = {"reply": "Opening Nvidia.", "actions": []}

    trace, spans = sent
    assert trace["url"] == "https://prism.blockconvey.com/api/traces"
    assert trace["headers"] == {"X-PRISMtrace-Key": "pt-sk-test"}
    body = trace["json"]
    assert body["project_id"] == "proj-1" and body["trace_id"] == run.id and body["session_id"] == run.id
    assert body["model"] == "nicrron/auto"
    assert body["input_messages"] == [{"role": "user", "content": "show me nvidia"}]
    assert body["output_message"] == "Opening Nvidia."
    assert body["agent_id"] == "whatspoppin.assistant.turn"

    assert spans["url"] == "https://prism.blockconvey.com/api/spans/ingest"
    assert spans["json"]["trace_id"] == run.id
    assert [(s["name"], s["span_type"]) for s in spans["json"]["spans"]] == [
        ("context.snapshot", "tool"), ("llm.complete_structured", "llm")]


def test_failed_run_has_no_output_and_an_errored_span(sent):
    with pytest.raises(ValueError):
        with prism.trace_run("markets.search", inputs={"q": "oil"}, model="voyage/voyage-finance-2") as run:
            with run.step("embeddings.search", kind="retrieval"):
                raise ValueError("gateway down")

    trace, spans = sent
    assert trace["json"]["output_message"] == ""
    assert trace["json"]["model"] == "voyage/voyage-finance-2"
    assert trace["json"]["metadata"]["error"]["type"] == "ValueError"
    [span] = spans["json"]["spans"]
    assert span["status"] == "error" and span["error_message"] == "gateway down"


def test_nothing_sent_without_project_id(sent, monkeypatch):
    monkeypatch.delenv("PRISM_PROJECT_ID")
    with prism.trace_run("markets.search", inputs={"q": "oil"}):
        pass
    assert sent == []

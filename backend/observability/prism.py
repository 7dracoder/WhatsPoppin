"""Run tracing for PRISM: every agent run as a list of timed steps (tool calls, model calls,
retrieval), so a run can be opened step by step, clustered by failure and scored by an evaluator.

    with trace_run("assistant.turn", inputs={"message": msg}) as run:
        with run.step("llm.complete", kind="llm", model="nicrron/auto") as step:
            reply = complete_structured(...)
            step.output = reply.model_dump()
        run.output = {...}

Each finished run is appended to `.cache/prism/runs.jsonl` (always, for local inspection), and,
when PRISM_API_KEY and PRISM_PROJECT_ID are set, sent to PRISM on a background thread, so
tracing never slows or breaks the request it describes: the run as one trace (`/api/traces`)
and its steps as spans of that trace (`/api/spans/ingest`), authenticated with the
`X-PRISMtrace-Key` header (PRISM rejects a Bearer token for API keys). A failed step marks its
run failed and records the error type and message, which is what PRISM's Root Cause clusters
group on.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

LOCAL_LOG = Path(".cache/prism/runs.jsonl")
DEFAULT_HOST = "https://prism.blockconvey.com"
MAX_FIELD_CHARS = 4000
_write_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(value: Any) -> Any:
    """JSON-safe and bounded: long strings and big structures are cut, never dropped."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps(str(value))
    if len(text) <= MAX_FIELD_CHARS:
        return json.loads(text)
    return {"truncated": True, "preview": text[:MAX_FIELD_CHARS]}


class Step:
    def __init__(self, name: str, kind: str, attrs: dict) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.name, self.kind, self.attrs = name, kind, attrs
        self.input: Any = None
        self.output: Any = None
        self.status = "ok"
        self.error: dict | None = None
        self.started_at = _now()
        self._t0 = time.perf_counter()
        self.duration_ms = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "status": self.status,
                "started_at": self.started_at, "duration_ms": round(self.duration_ms, 1),
                "attributes": _clip(self.attrs), "input": _clip(self.input), "output": _clip(self.output),
                "error": self.error}


class Run:
    def __init__(self, name: str, inputs: Any, attrs: dict) -> None:
        self.id = uuid.uuid4().hex
        self.name, self.inputs, self.attrs = name, inputs, attrs
        self.output: Any = None
        self.status = "ok"
        self.error: dict | None = None
        self.steps: list[Step] = []
        self.started_at = _now()
        self._t0 = time.perf_counter()

    @contextmanager
    def step(self, name: str, kind: str = "tool", **attrs) -> Iterator[Step]:
        """One step. An exception inside marks the step and the run failed, then propagates."""
        s = Step(name, kind, attrs)
        self.steps.append(s)
        try:
            yield s
        except BaseException as exc:
            s.status = "error"
            s.error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            self.status = "error"
            self.error = self.error or {**s.error, "step": name}
            raise
        finally:
            s.duration_ms = (time.perf_counter() - s._t0) * 1000

    def to_dict(self) -> dict:
        return {"run_id": self.id, "name": self.name, "status": self.status, "started_at": self.started_at,
                "duration_ms": round((time.perf_counter() - self._t0) * 1000, 1),
                "attributes": _clip(self.attrs), "input": _clip(self.inputs), "output": _clip(self.output),
                "error": self.error, "steps": [s.to_dict() for s in self.steps],
                "service": "whatspoppin"}


@contextmanager
def trace_run(name: str, inputs: Any = None, **attrs) -> Iterator[Run]:
    """Trace one agent run. Never raises because of tracing itself."""
    run = Run(name, inputs, attrs)
    try:
        yield run
    except BaseException as exc:
        if run.status == "ok":
            run.status = "error"
            run.error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        raise
    finally:
        record = run.to_dict()
        _write_local(record)
        _export(record)


def _write_local(record: dict) -> None:
    try:
        LOCAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock, LOCAL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("PRISM local log unavailable: %s", exc)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _to_trace(record: dict, project_id: str) -> dict:
    """A run as one PRISM trace. A run that crashed carries no output, which PRISM counts as an error;
    one that answered but failed a check keeps its answer, with the error in its metadata."""
    inputs, output = record["input"], record["output"]
    message = inputs.get("message") if isinstance(inputs, dict) else None
    reply = output.get("reply") if isinstance(output, dict) else None
    model = next((s["attributes"].get("model") for s in record["steps"] if s["attributes"].get("model")),
                 record["attributes"].get("model") or "none")
    return {
        "project_id": project_id,
        "trace_id": record["run_id"],
        "session_id": record["run_id"],
        "agent_id": record["attributes"].get("agent_id") or f"{record['service']}.{record['name']}",
        "agent_name": record["attributes"].get("agent_name") or record["name"],
        "model": model,
        "input_messages": [{"role": "user", "content": _text(message or inputs)}],
        "output_message": _text(reply or output),
        "latency_ms": int(record["duration_ms"]),
        "metadata": {"service": record["service"], "status": record["status"], "error": record["error"],
                     **record["attributes"]},
    }


def _end(started_at: str, duration_ms: float) -> str:
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    return datetime.fromtimestamp(start.timestamp() + duration_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _to_spans(record: dict, project_id: str) -> dict:
    """The run as a root agent span with its steps nested under it. PRISM shows the root span's input
    and output as the trace's conversation. Its span types are llm, tool and agent, so retrieval is a tool."""
    trace = _to_trace(record, project_id)
    root_id = record["run_id"][:16]
    spans = [{
        "span_id": root_id,
        "name": record["name"],
        "span_type": "agent",
        "start_time": record["started_at"],
        "end_time": _end(record["started_at"], record["duration_ms"]),
        "duration_ms": record["duration_ms"],
        "status": record["status"],
        "error_message": f"{record['error']['type']}: {record['error']['message']}" if record["error"] else None,
        "input_text": trace["input_messages"][0]["content"],
        "output_text": trace["output_message"],
    }]
    for s in record["steps"]:
        spans.append({
            "span_id": s["id"],
            "parent_span_id": root_id,
            "name": s["name"],
            "span_type": "llm" if s["kind"] == "llm" else "tool",
            "start_time": s["started_at"],
            "end_time": _end(s["started_at"], s["duration_ms"]),
            "duration_ms": s["duration_ms"],
            "status": s["status"],
            "error_message": s["error"]["message"] if s["error"] else None,
            "input_text": _text(s["input"]),
            "output_text": _text(s["output"]),
            "model": s["attributes"].get("model"),
        })
    return {"trace_id": record["run_id"], "project_id": project_id, "session_id": record["run_id"],
            "spans": spans}


def _export(record: dict) -> None:
    key = os.environ.get("PRISM_API_KEY")
    project_id = os.environ.get("PRISM_PROJECT_ID")
    if not (key and project_id):
        return
    host = os.environ.get("PRISM_HOST", DEFAULT_HOST).rstrip("/")

    def send() -> None:
        try:
            with httpx.Client(base_url=host, headers={"X-PRISMtrace-Key": key}, timeout=10) as client:
                client.post("/api/traces", json=_to_trace(record, project_id)).raise_for_status()
                if record["steps"]:
                    client.post("/api/spans/ingest", json=_to_spans(record, project_id)).raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("PRISM export failed for run %s: %s", record["run_id"], exc)

    threading.Thread(target=send, daemon=True).start()

"""Run tracing for PRISM: every agent run as a list of timed steps (tool calls, model calls,
retrieval), so a run can be opened step by step, clustered by failure and scored by an evaluator.

    with trace_run("assistant.turn", inputs={"message": msg}) as run:
        with run.step("llm.complete", kind="llm", model="nicrron/auto") as step:
            reply = complete_structured(...)
            step.output = reply.model_dump()
        run.output = {...}

Each finished run is appended to `.cache/prism/runs.jsonl` (always, for local inspection), and,
when PRISM_INGEST_URL is set, POSTed there as JSON with `Authorization: Bearer PRISM_API_KEY`
on a background thread, so tracing never slows or breaks the request it describes. A failed
step marks its run failed and records the error type and message, which is what PRISM's
Root Cause clusters group on.
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


def _export(record: dict) -> None:
    url = os.environ.get("PRISM_INGEST_URL")
    key = os.environ.get("PRISM_API_KEY")
    if not (url and key):
        return

    def send() -> None:
        try:
            httpx.post(url, json=record, headers={"Authorization": f"Bearer {key}"}, timeout=10).raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("PRISM export failed for run %s: %s", record["run_id"], exc)

    threading.Thread(target=send, daemon=True).start()

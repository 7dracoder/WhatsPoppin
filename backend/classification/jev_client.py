"""Transport for TypeSafe's Jev question contract (a `state` plus a map of
typed `questions`, answered in one call), documented at
docs.typesafe.ai/introduction. This file is the one place that knows how
to speak that contract; modes.py stays in terms of JevQuestion/JevAnswer
only.

Provider choice:
1. TYPESAFE_API_KEY (optional): TypeSafe's native Jev model (api.typesafe.ai).
2. NICRRON_API_KEY (default): the Nicrron gateway. Nicrron serves chat models,
   not Jev, so the same questions are put to a chat model (NICRRON_JEV_MODEL,
   else NICRRON_MODEL, else nicrron/auto) that answers with probabilities,
   and the reply is normalized into exactly the answer shapes Jev returns.
Both return the same {question_id: JevAnswer} map.
"""

import json
import os

import httpx
from pydantic import ValidationError

from backend.llm.client import NICRRON_DEFAULT_MODEL, NICRRON_URL, post_with_retry, strip_json_fences

from .primitives import ANSWER_TYPES, ChoiceQuestion, JevAnswer, JevQuestion, NoulQuestion, ScoreQuestion

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_DEFAULT_MODEL = "jev-latest"

NICRRON_JEV_SYSTEM = """You are a calibrated text classifier. You get a STATE (the text to judge) and
QUESTIONS, each with an id and a type. Answer every question about the STATE only, with honest
probabilities (not just 0 or 1 unless the text is unambiguous).

Answer shape per type:
- "noul" (yes/no): {"type": "noul", "noul": <probability the answer is yes, 0-1>}
- "choice" (pick one option): {"type": "choice", "probabilities": {<every option exactly as given>: <0-1>}}
- "score" (ordered levels, index 0 = lowest): {"type": "score", "probabilities": {"0": <0-1>, "1": <0-1>, ...}}

Respond with ONLY a JSON object {"answers": {<question id>: <answer>}}, no prose, no code fences."""


class JevError(Exception):
    """Raised when a Jev call fails, or succeeds but returns an answers
    map that doesn't match the questions asked. Callers decide the
    fallback — this function doesn't retry on its own.
    """


def call_jev(
    state: str,
    questions: dict[str, JevQuestion],
    model: str | None = None,
    http_client: httpx.Client | None = None,
) -> dict[str, JevAnswer]:
    """Ask one or more typed questions against the same `state` in a
    single Jev call. Returns answers keyed the same as `questions`.

    http_client is injectable for tests (pass a fake/mock transport);
    defaults to a real httpx.Client otherwise.
    """
    typesafe_key = os.environ.get("TYPESAFE_API_KEY")
    if not typesafe_key:
        nicrron_key = os.environ.get("NICRRON_API_KEY")
        if not nicrron_key:
            raise JevError("NICRRON_API_KEY is not set in environment (or TYPESAFE_API_KEY for native Jev)")
        return _call_via_nicrron(state, questions, nicrron_key, http_client)

    url, api_key, model = TYPESAFE_URL, typesafe_key, model or TYPESAFE_DEFAULT_MODEL
    body = {
        "model": model,
        "state": state,
        "questions": {qid: q.model_dump() for qid, q in questions.items()},
    }
    response = _post(url, api_key, body, http_client)
    try:
        raw_answers = response.json()["answers"]
    except (KeyError, ValueError) as exc:
        raise JevError(f"Unexpected Jev response shape: {exc}") from exc
    return _validate(raw_answers, questions)


def _post(url: str, api_key: str, body: dict, http_client: httpx.Client | None, timeout: float = 20) -> httpx.Response:
    client = http_client or httpx.Client()
    try:
        response = post_with_retry(client, url, headers={"Authorization": f"Bearer {api_key}"}, json=body,
                                   timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise JevError(f"Jev request failed: {exc}") from exc
    finally:
        if http_client is None:
            client.close()
    return response


def _validate(raw_answers: dict, questions: dict[str, JevQuestion]) -> dict[str, JevAnswer]:
    answers: dict[str, JevAnswer] = {}
    for qid, raw in raw_answers.items():
        if qid not in questions:
            continue  # an extra answer is harmless; only the asked ones are returned
        answer_type = ANSWER_TYPES.get(raw.get("type"))
        if answer_type is None:
            raise JevError(f"Unknown answer type for question {qid!r}: {raw.get('type')!r}")
        try:
            answers[qid] = answer_type.model_validate(raw)
        except ValidationError as exc:
            raise JevError(f"Jev answer for {qid!r} didn't match {answer_type.__name__}: {exc}") from exc

    missing = questions.keys() - answers.keys()
    if missing:
        raise JevError(f"Jev response missing answers for: {sorted(missing)}")
    return answers


# --- Nicrron: the Jev contract over chat completions ---------------------------------------


def _describe(qid: str, q: JevQuestion) -> dict:
    if isinstance(q, ChoiceQuestion):
        return {"id": qid, "type": "choice", "instructions": q.instructions, "options": q.criteria}
    if isinstance(q, ScoreQuestion):
        levels = {str(i): level for i, level in enumerate(q.criteria)}
        return {"id": qid, "type": "score", "instructions": q.instructions, "levels": levels}
    return {"id": qid, "type": "noul", "instructions": q.instructions}


def _normalized(probs: dict, keys: list[str]) -> dict[str, float]:
    """Probabilities for exactly `keys`, negatives dropped, rescaled to sum to 1 (uniform when all zero).
    Not clipped at 1 first: a reply like {"a": 3, "b": 1} is relative weights, so it becomes 0.75/0.25."""
    values = {}
    for k in keys:
        try:
            values[k] = max(float(probs.get(k, 0.0)), 0.0)
        except (TypeError, ValueError):
            values[k] = 0.0
    total = sum(values.values())
    if total <= 0:
        return {k: 1 / len(keys) for k in keys}
    return {k: v / total for k, v in values.items()}


def _to_jev_answer(q: JevQuestion, raw: dict) -> dict:
    """The model's reply for one question, in Jev's exact answer shape. Tolerates the common
    slips of a chat model: a yes/no answered with a bare number, or under "probability"."""
    if isinstance(q, NoulQuestion):
        value = raw.get("noul", raw.get("probability")) if isinstance(raw, dict) else raw
        try:
            if isinstance(value, bool):
                raise TypeError("a boolean is not a probability")
            p = float(value)
        except (TypeError, ValueError) as exc:
            raise JevError(f"Jev noul answer has no probability: {raw!r}") from exc
        return {"type": "noul", "noul": min(max(p, 0.0), 1.0)}
    if not isinstance(raw, dict):
        raise JevError(f"Jev answer is not an object: {raw!r}")
    probs_in = raw.get("probabilities")
    if not isinstance(probs_in, dict):
        raise JevError(f"Jev {q.type} answer has no probabilities: {raw!r}")
    if isinstance(q, ChoiceQuestion):
        probs = _normalized(probs_in, list(q.criteria))
        best = max(probs, key=probs.get)
        return {"type": "choice", "choice": best, "probabilities": probs, "confidence": probs[best]}
    keys = [str(i) for i in range(len(q.criteria))]
    probs = _normalized(probs_in, keys)
    return {
        "type": "score",
        "score": sum(int(k) * p for k, p in probs.items()),  # probability-weighted level, like Jev
        "legend": dict(zip(keys, q.criteria)),
        "probabilities": probs,
        "confidence": max(probs.values()),
    }


def _call_via_nicrron(state: str, questions: dict[str, JevQuestion], api_key: str,
                      http_client: httpx.Client | None) -> dict[str, JevAnswer]:
    model = os.environ.get("NICRRON_JEV_MODEL") or os.environ.get("NICRRON_MODEL") or NICRRON_DEFAULT_MODEL
    user = json.dumps({"state": state, "questions": [_describe(qid, q) for qid, q in questions.items()]})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": NICRRON_JEV_SYSTEM}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,  # deterministic, and lets the gateway serve repeats from its cache
    }
    response = _post(NICRRON_URL, api_key, body, http_client, timeout=30)
    try:
        content = response.json()["choices"][0]["message"]["content"]
        raw_answers = json.loads(strip_json_fences(content))["answers"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise JevError(f"Unexpected Jev response shape: {exc}") from exc
    if not isinstance(raw_answers, dict):
        raise JevError("Unexpected Jev response shape: answers is not an object")
    converted = {qid: _to_jev_answer(questions[qid], raw) for qid, raw in raw_answers.items() if qid in questions}
    return _validate(converted, questions)

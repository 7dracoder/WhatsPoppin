"""Jev reranking of a company's candidate markets (new_specs/top-markets-helper.md).

The tag query finds candidates cheaply but ranks coarsely (tier, then close time). This asks
Jev, per candidate, whether the market's outcome would matter to an investor in the company,
and ranks by that probability. Candidates are one market per event, so one question each,
asked in batches (one Jev call per batch, batches in parallel).

Synchronous, like classify(); call it through asyncio.to_thread from async code.
"""

from concurrent.futures import ThreadPoolExecutor

from backend.classification.jev_client import call_jev
from backend.classification.primitives import NoulQuestion

BATCH = 50  # questions per Jev call; 50 is what enrichment's entity pass already asks
THRESHOLD = 0.5


def _market_text(m: dict) -> str:
    parts = [m.get("event_title"), m.get("title")]
    return " / ".join(dict.fromkeys(p for p in parts if p)) or m["market_id"]


def rerank(company: str, related: list[str], candidates: list[dict], threshold: float = THRESHOLD,
           call_jev_fn=call_jev) -> list[tuple[dict, float]]:
    """(market, relevance) for candidates at or above `threshold`, most relevant first.
    `company` is a display name; `related` names what it is tied to (tags, linked companies).
    Raises JevError if a call fails.
    """
    if not candidates:
        return []
    state = (f"An investor follows {company}. Things closely tied to it: {', '.join(related)}." if related
             else f"An investor follows {company}.")

    def ask(batch: list[dict]) -> list[float]:
        questions = {
            f"m{i}": NoulQuestion(instructions=(
                f"Prediction market: {_market_text(m)!r}. Would this market's outcome matter to an investor in "
                f"{company}: is it about {company}, or about something closely tied to it?"))
            for i, m in enumerate(batch)
        }
        answers = call_jev_fn(state, questions)
        return [answers[f"m{i}"].noul for i in range(len(batch))]

    batches = [candidates[i:i + BATCH] for i in range(0, len(candidates), BATCH)]
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        scores = [s for batch_scores in pool.map(ask, batches) for s in batch_scores]
    ranked = sorted(zip(candidates, scores), key=lambda ms: -ms[1])  # stable: ties keep the tag order
    return [(m, s) for m, s in ranked if s >= threshold]

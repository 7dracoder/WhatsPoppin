"""Tier 3: ask Jev which companies an alert's market would affect.

Tiers 1-2 only reach companies the market is tagged with and their graph links, so a market
about a thing (gas prices, crude oil, a CPI print) reaches no company. This asks Jev, for every
company in `entities`, whether the market's outcome would matter to it: one yes/no question per
company, in batches of 50 per call, batches in parallel (~121 companies = 3 calls, 1-2 s).

Synchronous, like classify(); call it through asyncio.to_thread from async code.
"""

from concurrent.futures import ThreadPoolExecutor

from backend.classification.jev_client import call_jev
from backend.classification.primitives import NoulQuestion

BATCH = 50
THRESHOLD = 0.5
RULES_MAX_CHARS = 500


def market_state(market: dict) -> str:
    parts = [("Market", market.get("title")), ("Outcome", market.get("outcome_label")),
             ("Event", market.get("event_title")), ("Rules", (market.get("rules_primary") or "")[:RULES_MAX_CHARS])]
    return "\n".join(f"{name}: {value}" for name, value in parts if value) or market["market_id"]


def affected_companies(market: dict, companies: list[tuple[str, str]], threshold: float = THRESHOLD,
                       call_jev_fn=call_jev) -> list[tuple[str, float]]:
    """(symbol, probability) for companies at or above `threshold`, most likely first.
    `companies` is (symbol, name). Raises JevError if a call fails.
    """
    if not companies:
        return []
    state = "A prediction market just moved sharply.\n" + market_state(market)

    def ask(batch: list[tuple[str, str]]) -> list[float]:
        questions = {f"c{i}": NoulQuestion(instructions=(
            f"Would this market's outcome matter to an investor in {name} ({symbol}): is it about {name}, "
            f"or would it directly move its sales, costs, prices or regulation?"))
            for i, (symbol, name) in enumerate(batch)}
        answers = call_jev_fn(state, questions)
        return [answers[f"c{i}"].noul for i in range(len(batch))]

    batches = [companies[i:i + BATCH] for i in range(0, len(companies), BATCH)]
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        scores = [s for batch_scores in pool.map(ask, batches) for s in batch_scores]
    hits = [(symbol, score) for (symbol, _), score in zip(companies, scores) if score >= threshold]
    return sorted(hits, key=lambda h: -h[1])

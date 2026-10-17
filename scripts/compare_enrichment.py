"""Compare the two enrichment strategies on the same live markets.

    uv run python scripts/compare_enrichment.py --events 20 --shortlist 30

v1 = two-pass Jev walk (categories, then every entity of each chosen category).
v2 = same category pass, then an embedding shortlist (voyage-finance-2 via Nicrron) of the k closest
     entities in each chosen category, asked about in ONE Jev call (2 calls per event in total).
Prints per-event tags from both, how far v2's tags agree with v1's, Jev calls and wall time.
Both runs call the model, so this costs a little. Nothing is written except event embeddings.
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.classification import classify  # noqa: E402
from backend.enrichment.enrich import enrich_event, enrich_event_shortlist, event_text  # noqa: E402
from backend.enrichment.vectors import EntityIndex, active_events, ensure_event_embeddings  # noqa: E402
from backend.entities import load_entity_map  # noqa: E402
from backend.ingestion.common.db import make_engine  # noqa: E402


class CountingClassify:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        return classify(*args)


async def main(n_events: int, k: int, concurrency: int) -> None:
    load_dotenv()
    engine = make_engine(os.environ["DATABASE_URL"])
    entity_map = load_entity_map()
    events = dict(list((await active_events(engine)).items())[:n_events])
    t0 = time.time()
    vectors = await ensure_event_embeddings(engine, events)
    index = await asyncio.to_thread(EntityIndex, entity_map)
    embed_s = time.time() - t0
    await engine.dispose()

    slots = asyncio.Semaphore(concurrency)
    v1_calls, v2_calls = CountingClassify(), CountingClassify()

    async def run(fn, *args):
        async with slots:
            started = time.time()
            try:
                return await asyncio.to_thread(fn, *args), time.time() - started
            except Exception as exc:  # noqa: BLE001 - report and keep comparing
                return exc, time.time() - started

    keys = list(events)
    t1 = time.time()
    v1 = await asyncio.gather(*(run(enrich_event, events[key], entity_map, 0.5, 0.3, v1_calls) for key in keys))
    v1_s = time.time() - t1
    t2 = time.time()
    def bound(key):
        return lambda categories, n: index.shortlist(vectors[key], n, categories)

    v2 = await asyncio.gather(*(run(enrich_event_shortlist, events[key], bound(key), k, 0.5, 0.3, v2_calls)
                                for key in keys))
    v2_s = time.time() - t2

    agree = found = total = errors = 0
    for key, (a, _), (b, _) in zip(keys, v1, v2):
        title, _ = event_text(events[key])
        if isinstance(a, Exception) or isinstance(b, Exception):
            errors += 1
            print(f"! {title[:70]}: v1={a!r} v2={b!r}")
            continue
        a, b = set(a), set(b)
        total += len(a)
        found += len(a & b)
        agree += a == b
        mark = "=" if a == b else "~"
        print(f"{mark} {title[:70]}\n    v1: {sorted(a)}\n    v2: {sorted(b)}")

    ok = len(keys) - errors
    print("\n--- summary ---")
    print(f"events: {len(keys)} ({errors} errored)   shortlist k={k} per category")
    print(f"identical tag sets: {agree}/{ok}")
    print(f"v1 tags also found by v2 (recall vs v1): {found}/{total} = {found / total:.0%}" if total else "v1 found no tags")
    print(f"Jev calls  v1: {v1_calls.calls}   v2: {v2_calls.calls}")
    print(f"wall time  v1: {v1_s:.1f}s   v2: {v2_s:.1f}s (+{embed_s:.1f}s one-off embedding)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=int, default=20)
    p.add_argument("--shortlist", type=int, default=12, help="candidates per chosen category")
    p.add_argument("--concurrency", type=int, default=4)
    a = p.parse_args()
    asyncio.run(main(a.events, a.shortlist, a.concurrency))

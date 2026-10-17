"""Enrichment worker: tag polled markets with entities from the map, using Jev.

Runs next to the pollers and never blocks them: pollers only write `markets`, this reads them.
Run from the repo root:

    uv run python -m backend.enrichment          # keep running: new markets within a sweep
    uv run python -m backend.enrichment --once   # one batch of ENRICH_BATCH events, then exit
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from backend.classification import JevError
from backend.enrichment import db
from backend.enrichment.enrich import enrich_event, enrich_event_shortlist
from backend.enrichment.vectors import EntityIndex, ensure_event_embeddings
from backend.entities import EntityMap, load_entity_map
from backend.llm.embeddings import EmbeddingError

log = logging.getLogger("enrichment")


async def sweep(engine, entity_map: EntityMap, batch: int, concurrency: int, threshold: float,
                category_threshold: float, index: EntityIndex | None = None, shortlist_k: int = 0) -> int:
    """Enrich up to `batch` events. Returns how many were picked up.

    With an `index` and `shortlist_k` > 0, each event's embedding (stored in market_embeddings, one
    batched call per sweep) picks the k closest entities per chosen category, and Jev is asked about
    them in one call: 2 calls per event instead of 1 + one per category. If embedding fails, the
    sweep falls back to the full two-pass walk.
    """
    events = await db.events_to_enrich(engine, entity_map.version, batch)
    if not events:
        return 0
    await db.mark_pending(engine, [m for markets in events for m in markets])
    vectors = {}
    if index is not None and shortlist_k > 0:
        by_key = {(m[0]["source"], m[0]["event_id"] or m[0]["market_id"]): m for m in events}
        try:
            vectors = await ensure_event_embeddings(engine, by_key)
        except EmbeddingError as e:
            log.warning("Event embedding failed, using the two-pass walk this sweep: %s", e)
    started = time.time()
    jev, copied, failed = Counter(), Counter(), Counter()
    slots = asyncio.Semaphore(concurrency)

    async def one(markets: list[dict]) -> None:
        source, event_id = markets[0]["source"], markets[0]["event_id"]
        market_ids = [m["market_id"] for m in markets]
        symbols = await db.sibling_tags(engine, source, event_id, entity_map) if event_id else None
        if symbols is not None:
            copied[source] += len(markets)
        else:
            vector = vectors.get((source, event_id or market_ids[0]))
            async with slots:
                try:
                    if vector is not None:
                        def shortlist(categories, k, v=vector):
                            return index.shortlist(v, k, categories)
                        symbols = await asyncio.to_thread(enrich_event_shortlist, markets, shortlist, shortlist_k,
                                                          threshold, category_threshold)
                    else:
                        symbols = await asyncio.to_thread(enrich_event, markets, entity_map, threshold,
                                                          category_threshold)
                except (JevError, ValueError) as e:
                    failed[source] += len(markets)
                    log.warning("Enrichment failed for %s event %s: %s", source, event_id or market_ids[0], e)
                    await db.save_failure(engine, source, market_ids, str(e), entity_map.version)
                    return
            jev[source] += len(markets)
        await db.save_result(engine, source, market_ids, symbols, entity_map)

    n_markets = sum(len(markets) for markets in events)
    log.info("Enriching %d events, %d markets (%s)", len(events), n_markets,
             ", ".join(f"{s} {n}" for s, n in sorted(Counter(m[0]["source"] for m in events).items())))
    await asyncio.gather(*(one(markets) for markets in events))
    log.info("Markets tagged by Jev: %s; copied from their event: %s; failed: %s; took %.0fs",
             dict(jev) or 0, dict(copied) or 0, dict(failed) or 0, time.time() - started)
    return len(events)


async def main(once: bool) -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not (url and any(os.environ.get(k) for k in ("NICRRON_API_KEY", "TYPESAFE_API_KEY"))):
        sys.exit("Set DATABASE_URL and NICRRON_API_KEY in .env")
    batch = int(os.environ.get("ENRICH_BATCH") or 100)
    concurrency = int(os.environ.get("ENRICH_CONCURRENCY") or 4)
    threshold = float(os.environ.get("ENRICH_THRESHOLD") or 0.5)
    category_threshold = float(os.environ.get("ENRICH_CATEGORY_THRESHOLD") or 0.3)
    sweep_s = float(os.environ.get("ENRICH_SWEEP_S") or 60)
    entity_map = load_entity_map()
    try:
        engine = await db.connect(url)
        await db.sync_entity_map(engine, entity_map)
    except (OSError, SQLAlchemyError, asyncio.TimeoutError) as e:
        sys.exit(f"Cannot connect to the database: {e}")
    log.info("Entity map v%d: %d entities", entity_map.version, len(entity_map.entities))
    shortlist_k = int(os.environ.get("ENRICH_SHORTLIST") or 12)
    index = None
    if shortlist_k > 0:
        try:
            index = await asyncio.to_thread(EntityIndex, entity_map)
            log.info("Embedding shortlist on: %d candidates per category", shortlist_k)
        except EmbeddingError as e:
            log.warning("Entity embedding failed, using the two-pass walk: %s", e)
    try:
        while True:
            try:
                picked = await sweep(engine, entity_map, batch, concurrency, threshold, category_threshold,
                                     index, shortlist_k)
            except (SQLAlchemyError, OSError) as e:
                log.warning("Sweep failed, will retry: %s", e)
                picked = 0
            if once:
                break
            if picked < batch:  # caught up; a full batch means more are waiting
                await asyncio.sleep(sweep_s)
    except asyncio.CancelledError:
        pass
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m backend.enrichment")
    parser.add_argument("--once", action="store_true", help="one batch, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main(args.once))
    except KeyboardInterrupt:
        pass

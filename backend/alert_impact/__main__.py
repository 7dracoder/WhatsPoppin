"""Alert impact worker: match each new alert's market backwards to the companies it may affect.

Spec: new_specs/alert-company-impact.md. Listens on the alert detector's `alerts` channel, with
a catch-up sweep for alerts written while it was down and retries for failures. Run from the
repo root next to the alert detector:

    uv run python -m backend.alert_impact                               # keep running
    uv run python -m backend.alert_impact --once                        # catch up, then exit
    uv run python -m backend.alert_impact --simulate kalshi:KXFED-27JAN-T4.00   # print, write nothing
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.alert_impact import db
from backend.alert_impact.jev_match import affected_companies
from backend.alert_impact.match import Impact, match_companies
from backend.classification import JevError
from backend.enrichment import db as enrichment_db
from backend.enrichment.db import MARKET_COLUMNS
from backend.enrichment.enrich import enrich_event
from backend.entities import EntityMap, load_entity_map
from backend.models import Market

log = logging.getLogger("alert_impact")
USE_JEV = os.environ.get("ALERT_IMPACT_JEV", "1") != "0"  # tier 3; read again in main() after load_dotenv


async def impacts_for(engine, alert: dict, entity_map: EntityMap, limit: int, save_tags: bool,
                      use_jev: bool = True) -> tuple[list[Impact], str | None]:
    """The companies this alert's market may affect, and why tier 3 was skipped (or None).
    Enriches the market's event first if it isn't tagged yet (saving the tags, so search sees
    them too, unless `save_tags` is False). A failed tier 3 keeps tiers 1-2.
    """
    tags = await db.market_tags(engine, alert, entity_map)
    if tags is None:
        markets = await db.event_markets(engine, alert) or [alert]
        symbols = await asyncio.to_thread(enrich_event, markets, entity_map)
        if save_tags:
            await enrichment_db.save_result(engine, alert["source"], [m["market_id"] for m in markets], symbols,
                                            entity_map)
        types = {e.symbol: e.category for e in entity_map.entities}
        tags = [(s, types[s]) for s in symbols]
    tier1 = [s for s, kind in tags if kind == "company"]
    links = await db.graph_links(engine, tier1)
    hits, skipped = [], None if use_jev else "disabled"
    if use_jev:
        try:
            hits = await asyncio.to_thread(affected_companies, alert, await db.all_companies(engine))
        except JevError as e:
            skipped = f"tier 3 skipped: {e}"
            log.warning("Alert %s: %s", alert.get("alert_id", alert["market_id"]), skipped)
    return match_companies(tags, links, limit, hits), skipped


async def process(engine, alert_id: int, entity_map: EntityMap, limit: int) -> None:
    alert = await db.load_alert(engine, alert_id)
    if alert is None:
        return
    try:
        impacts, skipped = await impacts_for(engine, alert, entity_map, limit, save_tags=True, use_jev=USE_JEV)
        await db.save_impacts(engine, alert, impacts)
    except (JevError, ValueError, SQLAlchemyError) as e:
        log.warning("Alert %d (%s %s) failed: %s", alert_id, alert["source"], alert["market_id"], e)
        await db.save_run(engine, alert_id, "failed", error=str(e))
        return
    await db.save_run(engine, alert_id, "done", companies=len(impacts), error=skipped)
    log.info("Alert %d %s %r -> %s", alert_id, alert["market_id"], alert.get("event_title") or alert.get("title"),
             ", ".join(f"{i.symbol}(t{i.tier})" for i in impacts) or "no companies")


async def catch_up(engine, entity_map: EntityMap, lookback: timedelta, limit: int) -> int:
    ids = await db.alerts_to_process(engine, datetime.now(timezone.utc) - lookback)
    for alert_id in ids:
        await process(engine, alert_id, entity_map, limit)
    return len(ids)


async def listen(engine, entity_map: EntityMap, lookback: timedelta, limit: int, sweep_s: float) -> None:
    """Process each alert id announced on the `alerts` channel; sweep for missed ones every `sweep_s`."""
    queue: asyncio.Queue[int] = asyncio.Queue()
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection  # the asyncpg connection
        await raw.add_listener("alerts", lambda *args: queue.put_nowait(int(args[-1])))
        log.info("Listening for alerts")
        while True:
            try:
                alert_id = await asyncio.wait_for(queue.get(), timeout=sweep_s)
            except asyncio.TimeoutError:
                await catch_up(engine, entity_map, lookback, limit)
                continue
            await process(engine, alert_id, entity_map, limit)


async def simulate(engine, target: str, entity_map: EntityMap, limit: int) -> None:
    """Match one market as if it had just alerted. Writes nothing."""
    source, _, market_id = target.partition(":")
    async with engine.connect() as conn:
        row = (await conn.execute(select(*MARKET_COLUMNS, Market.url).where(
            Market.source == source, Market.market_id == market_id))).first()
    if row is None:
        sys.exit(f"No market {target}")
    market = dict(row._mapping)
    impacts, skipped = await impacts_for(engine, market, entity_map, limit, save_tags=False, use_jev=USE_JEV)
    if skipped:
        print(f"  ({skipped})")
    print(f"{source} {market_id}: {market.get('event_title') or market.get('title')}")
    for i in impacts:
        print(f"  tier {i.tier}  {i.symbol:8}  matched {i.matched}" + (f"  confidence {i.confidence:.2f}" if i.tier > 1 else ""))
    if not impacts:
        print("  no companies")


async def main(args) -> None:
    global USE_JEV
    load_dotenv()
    USE_JEV = os.environ.get("ALERT_IMPACT_JEV", "1") != "0"
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL in .env")
    limit = int(os.environ.get("ALERT_IMPACT_MAX_COMPANIES") or 10)
    lookback = timedelta(minutes=float(os.environ.get("ALERT_IMPACT_LOOKBACK_MIN") or 60))
    entity_map = load_entity_map()
    engine = await db.connect(url)
    await db.add_columns(engine)
    try:
        if args.simulate:
            await simulate(engine, args.simulate, entity_map, limit)
        else:
            log.info("Caught up on %d alerts", await catch_up(engine, entity_map, lookback, limit))
            if not args.once:
                await listen(engine, entity_map, lookback, limit, sweep_s=60)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m backend.alert_impact")
    parser.add_argument("--once", action="store_true", help="process missed alerts, then exit")
    parser.add_argument("--simulate", metavar="SOURCE:MARKET_ID", help="match one market, print, write nothing")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main(parser.parse_args()))
    except KeyboardInterrupt:
        pass

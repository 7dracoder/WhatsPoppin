"""Alert detector. Run from the repo root: uv run python -m alert_detector [--explain [--source S]]"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from collections import Counter

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from alert_detector import config, db
from alert_detector.detector import Detector, Evaluation
from alert_detector.state import State

log = logging.getLogger("alert_detector")

MARKETS_RELOAD_S = 60
STATS_INTERVAL_S = 60
PRUNE_INTERVAL_S = 60
EXPLAIN_TOP = 15
ACTIVE_RECENTLY_S = 600


async def main(explain: bool, source: str | None = None) -> None:
    load_dotenv()
    cfg = config.load()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("Set DATABASE_URL in .env")
    try:
        engine = await db.connect(database_url)
    except (OSError, SQLAlchemyError, asyncio.TimeoutError) as e:
        sys.exit(f"Cannot connect to the database: {e}")

    state = State(cfg)
    detector = Detector(cfg, state)
    await db.load_initial(engine, state, time.time() - cfg.history_s)
    detector.markets = await db.load_markets(engine)
    detector.baselines = await db.load_baselines(engine)

    if explain:
        print_explain(detector, time.time(), source)
        await engine.dispose()
        return

    for key, t, score in await db.recent_alerts(engine, cfg.cooldown_s):
        detector.cooldown[key] = (t, score)
    log.info("Loaded %d markets with data, %d active, %d with baselines; watching for new rows",
             len(state.markets), len(detector.markets), len(detector.baselines))
    if not detector.baselines:
        log.warning("No baselines yet: run `python -m baselines` or every signal will say 'no baseline yet'")

    written = 0
    was_stale = False

    async def poll() -> None:
        nonlocal written, was_stale
        changed = await db.poll_new(engine, state)
        alerts = detector.process(changed, time.time())
        if detector.stale and not was_stale:
            log.warning("No new Kalshi data for %ds: ingestion looks down, not alerting", cfg.stale_s)
        elif was_stale and not detector.stale:
            log.info("Data is flowing again; alerting resumed")
        was_stale = detector.stale
        for alert in alerts:
            alert_id = await db.insert_alert(engine, alert)
            written += 1
            log.info("Alert %d (score %s, %s): %s", alert_id, alert["score"],
                     ",".join(alert["reasons"]), alert["summary"])

    async def reload_markets() -> None:
        detector.markets = await db.load_markets(engine)
        detector.baselines = await db.load_baselines(engine)
    detector.baselines = await db.load_baselines(engine)

    async def prune() -> None:
        detector.prune(time.time())

    async def log_stats() -> None:
        nonlocal written
        log.info("Last minute: %d market evaluations, %d candidates, %d alerts written",
                 detector.evaluated, detector.candidates, written)
        detector.evaluated = detector.candidates = written = 0

    async def every(seconds: float, fn) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await fn()
            except (SQLAlchemyError, OSError) as e:
                log.warning("%s failed, will retry: %s", fn.__name__, e)

    # Ctrl+C: signal.signal works on Windows, loop.add_signal_handler doesn't.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))

    tasks = [asyncio.create_task(every(s, fn)) for s, fn in (
        (cfg.poll_s, poll), (MARKETS_RELOAD_S, reload_markets),
        (PRUNE_INTERVAL_S, prune), (STATS_INTERVAL_S, log_stats))]
    await stop.wait()
    log.info("Shutting down...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await engine.dispose()
    log.info("Stopped cleanly")


# --- --explain -------------------------------------------------------------------
def print_explain(detector: Detector, now: float, source: str | None = None) -> None:
    """One evaluation of recently active markets (optionally one source); prints why each signal
    did or didn't fire."""
    state = detector.state
    if now - state.latest_ts > detector.cfg.stale_s:
        print(f"WARNING: newest data is {now - state.latest_ts:.0f}s old; ingestion looks down.\n")
    evals = [detector.evaluate(key, now) for key, buf in state.markets.items()
             if buf.last_seen >= now - ACTIVE_RECENTLY_S]
    evals = [e for e in evals if e]
    per_source = ", ".join(f"{s} {n}" for s, n in sorted(Counter(e.source for e in evals).items()))
    if source:
        evals = [e for e in evals if e.source == source]
    evals.sort(key=lambda e: e.score, reverse=True)
    shown = f"top {min(EXPLAIN_TOP, len(evals))}" + (f" {source}" if source else "")
    print(f"Markets active in the last {ACTIVE_RECENTLY_S // 60} min: {per_source or 'none'}. "
          f"Showing the {shown} by score (nothing is written):\n")
    for e in evals[:EXPLAIN_TOP]:
        print(format_evaluation(e, detector.cfg))


def format_evaluation(e: Evaluation, cfg) -> str:
    name = e.meta.get("title") or ""
    if e.meta.get("outcome_label"):
        name += f" ({e.meta['outcome_label']})"
    head = f"[{e.source}] {e.market_id}  score {e.score:.2f}\n  {name[:110]}\n"
    if e.skip:
        return f"{head}  SKIPPED: {e.skip}\n"
    b = e.baseline
    if b is None:
        head += "  baseline:  none yet (python -m baselines computes it)\n"
    else:
        age_h = (time.time() - b.computed_at.timestamp()) / 3600
        head += f"  baseline:  {age_h:.1f} h old, {float(b.history_minutes) / 60:.0f} h of history ({b.method})\n"
    pm, vb, wh, im = e.price, e.volume, e.whale, e.imbalance
    flag = lambda fired: "FIRES" if fired else "no"
    lines = [f"{head}  {'CANDIDATE ' + ','.join(e.reasons) if e.candidate else 'not a candidate'}"]
    if pm.z is not None:
        lines.append(f"  price:     {flag(pm.fired)}  z={pm.z:+.2f} ({pm.change * 100:+.1f} pts, "
                     f"sigma {pm.sigma * 100:.2f} pts from {pm.samples} samples; need |z|>={cfg.z_min:g})")
    else:
        lines.append(f"  price:     skipped: {pm.skip}")
    if vb.ratio is not None:
        lines.append(f"  volume:    {flag(vb.fired)}  ${vb.window_notional:.0f} = {vb.ratio:.1f}x normal "
                     f"(need {cfg.burst_ratio:g}x and ${cfg.min_burst_notional:.0f})")
    else:
        lines.append(f"  volume:    skipped: {vb.skip} (${vb.window_notional:.0f} in window)")
    if wh.notional is not None:
        lines.append(f"  whale:     {flag(wh.fired)}  largest order ${wh.notional:.0f} {wh.side} "
                     f"vs threshold ${wh.threshold:.0f}")
    else:
        lines.append(f"  whale:     skipped: {wh.skip}")
    if im.value is not None:
        lines.append(f"  imbalance: {im.value:.2f} toward {im.side} (counts at >={cfg.imb_min:g} "
                     f"with a price or volume move)")
    else:
        lines.append(f"  imbalance: skipped: {im.skip}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m alert_detector")
    parser.add_argument("--explain", action="store_true", help="evaluate once, print signal details, write nothing")
    parser.add_argument("--source", choices=["kalshi", "polymarket", "polymarket_us"],
                        help="with --explain: only show markets from this source")
    args = parser.parse_args()
    if args.source and not args.explain:
        parser.error("--source only works with --explain; the detector always watches every source")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main(args.explain, args.source))

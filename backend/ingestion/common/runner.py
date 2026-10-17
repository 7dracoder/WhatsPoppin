"""The parts every source worker shares: DB setup, reconnect loop, batched writes, retention,
stats log and Ctrl+C shutdown.

A source worker provides `make_session(engine, writer)`, returning an object with:
- `async run()`: discover, connect, subscribe and read until the connection ends (or raise);
  it calls writer.add_price(row) / writer.add_trade(row)
- `ws`: set once the WebSocket connected, so the backoff can start over
"""

import asyncio
import logging
import random
import signal
import sys

import httpx
import websockets
from sqlalchemy.exc import SQLAlchemyError

from backend.ingestion.common import db

FLUSH_INTERVAL_S = 0.5
RETENTION_INTERVAL_S = 60
STATS_INTERVAL_S = 60
BACKOFF_START_S, BACKOFF_MAX_S = 1, 30


async def every(seconds: float, fn) -> None:
    while True:
        await asyncio.sleep(seconds)
        await fn()


async def run_worker(log: logging.Logger, database_url: str, make_session, cleanup=None) -> None:
    try:
        engine = await db.connect(database_url)
    except (OSError, SQLAlchemyError, asyncio.TimeoutError) as e:
        sys.exit(f"Cannot connect to the database: {e}")
    writer = db.RowWriter(engine)

    async def stream_forever() -> None:
        delay = BACKOFF_START_S
        while True:
            session = make_session(engine, writer)
            try:
                await session.run()
                log.warning("WebSocket closed")
            except (OSError, websockets.exceptions.WebSocketException, httpx.HTTPError,
                    SQLAlchemyError, ValueError, KeyError) as e:
                log.warning("Connection error: %s", e)
            if session.ws is not None:
                delay = BACKOFF_START_S  # we did connect; start backoff over
            wait = delay + random.uniform(0, delay / 4)
            log.info("Reconnecting in %.1fs", wait)
            await asyncio.sleep(wait)
            delay = min(delay * 2, BACKOFF_MAX_S)

    async def log_stats() -> None:
        p, t = writer.prices, writer.trades
        log.info("Rows written in the last minute: %d prices, %d trades (queued: %d)",
                 p.written, t.written, len(p.rows) + len(t.rows))
        p.written = t.written = 0

    async def retention() -> None:
        try:
            await db.delete_old_rows(engine)
        except (SQLAlchemyError, OSError) as e:
            log.warning("Retention delete failed: %s", e)

    # Ctrl+C: signal.signal works on Windows, loop.add_signal_handler doesn't.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))

    tasks = [
        asyncio.create_task(stream_forever()),
        asyncio.create_task(every(FLUSH_INTERVAL_S, writer.flush)),
        asyncio.create_task(every(RETENTION_INTERVAL_S, retention)),
        asyncio.create_task(every(STATS_INTERVAL_S, log_stats)),
    ]
    await stop.wait()

    log.info("Shutting down...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await writer.flush()
    if cleanup is not None:
        await cleanup()
    await engine.dispose()
    log.info("Stopped cleanly")


def start(main) -> None:
    """Configure logging and run the worker's async main()."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise
    asyncio.run(main())

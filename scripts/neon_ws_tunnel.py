"""Local TCP -> WebSocket tunnel to Neon, for networks that block TLS on port 5432.

Neon's proxy accepts the raw Postgres wire protocol over a WebSocket on port 443
(wss://<endpoint-host>/v2) -- this is what Neon's own serverless JS driver uses.
This script listens on localhost and pipes each connection into that WebSocket,
so any Postgres client (asyncpg, psql, SQLAlchemy) can reach Neon via port 443.

Usage, from the repo root (reads NEON_DATABASE_URL from .env for the host):
    uv run python scripts/neon_ws_tunnel.py [--port 5433]
Then point DATABASE_URL at postgresql://<user>:<pass>@127.0.0.1:5433/<db>.

TLS is terminated at the WebSocket layer, so the local leg carries plain Postgres
protocol over loopback; the client must NOT request SSL (no sslmode=require).
"""

import argparse
import asyncio
import logging
import os
from urllib.parse import urlparse

import websockets
from dotenv import load_dotenv

log = logging.getLogger("neon_ws_tunnel")

SSL_REQUEST = (8).to_bytes(4, "big") + (80877103).to_bytes(4, "big")  # libpq's "may I start TLS?"


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str) -> None:
    peer = writer.get_extra_info("peername")
    try:
        # Clients default to "prefer" SSL and open with an SSLRequest. The WebSocket is
        # already encrypted and Neon's proxy only expects the StartupMessage, so answer
        # "N" (no SSL) ourselves; the client then continues in plaintext over loopback.
        first = await reader.readexactly(8)
        log.debug("%s first 8 bytes %r", peer, first)
        if first == SSL_REQUEST:
            writer.write(b"N")
            await writer.drain()
            first = b""
        async with websockets.connect(f"wss://{host}/v2", max_size=None, open_timeout=15) as ws:
            log.info("%s -> %s connected", peer, host)

            async def client_to_neon() -> None:
                if first:
                    await ws.send(first)
                while data := await reader.read(65536):
                    log.debug("%s -> neon %d bytes %r", peer, len(data), data[:1])
                    await ws.send(data)
                await ws.close()

            async def neon_to_client() -> None:
                async for msg in ws:
                    log.debug("%s <- neon %d bytes %r", peer, len(msg), msg[:1])
                    writer.write(msg)
                    await writer.drain()

            await asyncio.gather(client_to_neon(), neon_to_client())
    except Exception as exc:  # noqa: BLE001 - one bad connection must not kill the tunnel
        log.info("%s closed: %s", peer, exc)
    finally:
        writer.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--host", help="Neon endpoint host; defaults to the host in NEON_DATABASE_URL")
    args = ap.parse_args()

    load_dotenv()
    host = args.host or urlparse(os.environ.get("NEON_DATABASE_URL", "")).hostname
    if not host:
        raise SystemExit("Pass --host or set NEON_DATABASE_URL in .env")

    logging.basicConfig(level=logging.DEBUG if os.environ.get("TUNNEL_DEBUG") else logging.INFO, format="%(asctime)s %(message)s")
    server = await asyncio.start_server(lambda r, w: pipe(r, w, host), "127.0.0.1", args.port)
    log.info("listening on 127.0.0.1:%d -> wss://%s/v2", args.port, host)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

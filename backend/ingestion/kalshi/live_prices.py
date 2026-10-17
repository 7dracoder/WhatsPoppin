"""Connect to Kalshi's WebSocket and print live ticker prices."""

import asyncio
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import find_dotenv, load_dotenv

from price_move import PriceMoveTracker

WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
RECONNECT_DELAY_SECONDS = 5

# --- 1. Load settings from .env ---------------------------------------------
env_file = find_dotenv()  # searches upward from this file for .env
load_dotenv(env_file)
API_KEY_ID = os.environ["KALSHI_API_KEY_ID"]

key_path = Path(env_file).parent / os.environ["KALSHI_PRIVATE_KEY_PATH"]
PRIVATE_KEY = serialization.load_pem_private_key(key_path.read_bytes(), password=None)

# Only compute price moves for these series (e.g. KXBTCD); empty = every market.
SERIES = [s.strip() for s in os.environ.get("KALSHI_SERIES", "").split(",") if s.strip()]
price_moves = PriceMoveTracker(SERIES)


# --- 2. Build the signed auth headers ---------------------------------------
def auth_headers() -> dict:
    timestamp = str(int(time.time() * 1000))  # milliseconds
    message = (timestamp + "GET" + WS_PATH).encode("utf-8")
    signature = PRIVATE_KEY.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


# --- 4. Handle each message -------------------------------------------------
def handle_message(raw: str) -> None:
    data = json.loads(raw)
    kind, msg = data.get("type"), data.get("msg", {})

    if kind == "market_lifecycle_v2":
        price_moves.on_lifecycle(msg)  # pauses, close-time changes, settlement
        return
    if kind != "ticker":
        return  # 5. ignore anything we don't recognize

    move = price_moves.on_ticker(msg)
    if move is None:
        return  # market not in KALSHI_SERIES
    line = f"{datetime.fromtimestamp(move.ts):%H:%M:%S}  {move.ticker:<34}"
    line += f"  mid ${move.mid:.4f}" if move.mid is not None else "  mid   --   "
    if move.z is not None:
        line += f"  5m {move.change:+.4f}  z {move.z:+.2f}"
    else:
        line += f"  ({move.skip})"
    print(line)


# --- 3. Connect, subscribe, and listen ---------------------------------------
async def stream_once() -> None:
    # Fresh headers every connect: the signature includes the current timestamp.
    async with websockets.connect(WS_URL, additional_headers=auth_headers()) as ws:
        print(f"Connected. Tracking series: {SERIES or 'all'}")
        price_moves.on_connect()
        # ticker = bid/ask/price updates; market_lifecycle_v2 = pause/resume/close/settle
        await ws.send(json.dumps({"id": 1, "cmd": "subscribe",
                                  "params": {"channels": ["ticker", "market_lifecycle_v2"]}}))
        async for raw in ws:
            try:
                handle_message(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # skip malformed messages instead of crashing


# --- 6. Reconnect forever if the connection drops ----------------------------
async def main() -> None:
    while True:
        try:
            await stream_once()
            print("Connection closed by server.")
        except (OSError, websockets.exceptions.WebSocketException) as e:
            print(f"Connection error: {e}")
        print(f"Reconnecting in {RECONNECT_DELAY_SECONDS}s...")
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")

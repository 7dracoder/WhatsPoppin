"""Thin gateway over Alpaca's historical bars REST endpoint
(GET /v2/stocks/{symbol}/bars), free Basic (IEX) feed.

Translates non-2xx responses into our own AlpacaApiError so callers never
need to know about httpx's exception types directly — if the HTTP client
is ever swapped, only this file should need to change.
"""

from datetime import datetime

import httpx

from .models import AlpacaApiError

BASE_URL = "https://data.alpaca.markets/v2"
FEED = "iex"  # free-tier feed
PAGE_LIMIT = 1000

# 429/5xx are worth a retry upstream; anything else (bad symbol, bad auth,
# bad params) is a caller bug or a dead credential.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AlpacaApiGateway:
    def __init__(self, key_id: str, secret_key: str, http_client: httpx.Client | None = None):
        """http_client is injectable for tests (pass a fake implementing
        .get(path, params=...) -> a fake response with .status_code/.json());
        defaults to a real httpx.Client otherwise.
        """
        self._client = http_client or httpx.Client(
            base_url=BASE_URL,
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key},
            timeout=15,
        )

    def fetch_bars(self, ticker: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
        """All bars for [start, end] at `timeframe`, following pagination
        (`next_page_token`) until exhausted.
        """
        bars: list[dict] = []
        page_token = None
        while True:
            params = {
                "timeframe": timeframe,
                "start": _rfc3339(start),
                "end": _rfc3339(end),
                "feed": FEED,
                "limit": PAGE_LIMIT,
            }
            if page_token:
                params["page_token"] = page_token
            body = self._raise_for_status(self._client.get(f"/stocks/{ticker}/bars", params=params))
            bars += body.get("bars") or []
            page_token = body.get("next_page_token")
            if not page_token:
                return bars

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            raise AlpacaApiError(
                code=str(resp.status_code),
                message=body.get("message") or resp.text[:200],
                retryable=resp.status_code in _RETRYABLE_STATUS,
            )
        return resp.json()


def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

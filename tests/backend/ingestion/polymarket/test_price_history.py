import asyncio

import httpx

from backend.ingestion.polymarket import polymarket as pm

DAY = 86400


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_hourly_history_is_fetched_in_windows_and_merged_in_order():
    seen = []

    def handler(request):
        q = request.url.params
        seen.append((int(q["startTs"]), int(q["endTs"]), q["fidelity"]))
        # Each window answers with its two edges, so neighbours repeat the shared one.
        return httpx.Response(200, json={"history": [{"t": int(q["endTs"]), "p": 0.5},
                                                     {"t": int(q["startTs"]), "p": 0.4}]})

    async def run():
        async with _client(handler) as client:
            return await pm.fetch_price_history(client, "111", 0, 30 * DAY, 60)

    points = asyncio.run(run())
    assert seen == [(0, 14 * DAY, "60"), (14 * DAY, 28 * DAY, "60"), (28 * DAY, 30 * DAY, "60")]
    assert [p["t"] for p in points] == [0, 14 * DAY, 28 * DAY, 30 * DAY]  # sorted, shared edges once


def test_daily_history_asks_for_the_whole_life_and_keeps_the_window():
    def handler(request):
        assert request.url.params["interval"] == "max" and "startTs" not in request.url.params
        return httpx.Response(200, json={"history": [{"t": 5, "p": 0.1}, {"t": 50, "p": 0.2}, {"t": 500, "p": 0.3}]})

    async def run():
        async with _client(handler) as client:
            return await pm.fetch_price_history(client, "111", 10, 100, 1440)

    assert asyncio.run(run()) == [{"t": 50, "p": 0.2}]


def test_fetch_market_retries_for_a_closed_market():
    def handler(request):
        closed = request.url.params.get("closed") == "true"
        return httpx.Response(200, json=[{"conditionId": "0xabc", "clobTokenIds": '["1", "2"]'}] if closed else [])

    async def run():
        async with _client(handler) as client:
            return await pm.fetch_market(client, "0xabc")

    assert pm.yes_token(asyncio.run(run())) == "1"

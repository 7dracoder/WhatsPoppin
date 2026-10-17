"""Tests for company_graph.sec. No network: every request goes to an httpx.MockTransport."""

import asyncio
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from company_graph.sec import (
    BROWSE_URL,
    RateLimiter,
    SecClient,
    SecConfigError,
    SecRequestError,
    html_to_text,
)

SUBMISSIONS = {
    "cik": "0001318605",
    "name": "Tesla, Inc.",
    "sic": "3711",
    "sicDescription": "Motor Vehicles & Passenger Car Bodies",
    "tickers": ["TSLA"],
    "exchanges": ["Nasdaq"],
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001628280-26-010000",
                "0001628280-26-003952",
                "0001628280-25-000001",
                "0000950170-25-000002",
            ],
            "filingDate": ["2026-04-23", "2026-01-29", "2025-01-30", "2024-10-24"],
            "reportDate": ["2026-03-31", "2025-12-31", "2024-12-31", "2024-09-30"],
            "form": ["10-Q", "10-K", "10-K", "10-Q"],
            "primaryDocument": [
                "tsla-20260331.htm",
                "tsla-20251231.htm",
                "tsla-20241231.htm",
                "tsla-20240930.htm",
            ],
        },
        "files": [],
    },
}


def efts_hit(adsh, doc, cik, display, form="10-K", file_date="2025-03-05", sics=("8071",)):
    return {
        "_id": f"{adsh}:{doc}",
        "_source": {
            "ciks": [cik],
            "display_names": [display],
            "root_forms": [form],
            "file_date": file_date,
            "sics": list(sics),
            "form": form,
            "adsh": adsh,
            "file_type": form,
        },
    }


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)


def make_client(handler, tmp_path, clock=None, **kwargs):
    clock = clock or FakeClock()
    return SecClient(
        "test@example.com",
        cache_dir=tmp_path / "cache",
        limiter=RateLimiter(clock=clock.time, sleep=clock.sleep),
        sleep=clock.sleep,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def run(coro):
    return asyncio.run(coro)


# ---- config ----


def test_missing_contact_email_fails_before_any_request(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=SUBMISSIONS)

    with pytest.raises(SecConfigError, match="SEC_CONTACT_EMAIL"):
        SecClient(cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    with pytest.raises(SecConfigError):
        SecClient("   ", cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    assert calls == []


def test_contact_email_read_from_environment_and_sent_in_user_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "env@example.com")
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, json=SUBMISSIONS)

    async def go():
        async with SecClient(cache_dir=None, transport=httpx.MockTransport(handler)) as sec:
            await sec.list_filings(1318605)

    run(go())
    assert len(seen) == 1 and "env@example.com" in seen[0]


# ---- list_filings ----


def test_list_filings_returns_latest_10k_and_profile(tmp_path):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=SUBMISSIONS)

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.list_filings("1318605", forms=["10-K"])

    result = run(go())
    assert urls == ["https://data.sec.gov/submissions/CIK0001318605.json"]
    assert result.name == "Tesla, Inc."
    assert result.sic_code == "3711"
    assert result.sic_description == "Motor Vehicles & Passenger Car Bodies"
    assert result.listing_venue == "Nasdaq"
    assert result.tickers == ["TSLA"]
    assert [f.form for f in result.filings] == ["10-K", "10-K"]
    latest = result.filings[0]
    assert latest.accession_number == "0001628280-26-003952"
    assert latest.filing_date == date(2026, 1, 29)
    assert latest.report_date == date(2025, 12, 31)
    assert latest.url == (
        "https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/tsla-20251231.htm"
    )


def test_list_filings_filters_by_since_and_multiple_forms(tmp_path):
    async def go():
        async with make_client(lambda r: httpx.Response(200, json=SUBMISSIONS), tmp_path) as sec:
            return await sec.list_filings(1318605, forms=["10-K", "10-q"], since=date(2025, 1, 1))

    result = run(go())
    assert [f.accession_number for f in result.filings] == [
        "0001628280-26-010000",
        "0001628280-26-003952",
        "0001628280-25-000001",
    ]


def test_list_filings_tolerates_missing_sic(tmp_path):
    body = {"name": "Shell Co", "filings": {"recent": {}}}

    async def go():
        async with make_client(lambda r: httpx.Response(200, json=body), tmp_path) as sec:
            return await sec.list_filings(1)

    result = run(go())
    assert result.sic_code is None and result.sic_description is None
    assert result.listing_venue is None and result.filings == []


# ---- rate limit ----


def assert_within_rate(stamps, rate=5, period=1.0):
    stamps = sorted(stamps)
    for i in range(len(stamps) - rate):
        assert stamps[i + rate] - stamps[i] >= period - 1e-9, (i, stamps[i : i + rate + 1])


def test_rate_limiter_burst_of_50_never_exceeds_5_per_second():
    clock = FakeClock()
    limiter = RateLimiter(rate=5, period=1.0, clock=clock.time, sleep=clock.sleep)
    stamps = []

    async def one():
        await limiter.acquire()
        stamps.append(clock.time())

    async def go():
        await asyncio.gather(*(one() for _ in range(50)))

    run(go())
    assert len(stamps) == 50
    assert_within_rate(stamps)
    assert stamps[-1] - stamps[0] >= 9.0  # 50 requests need at least 9 seconds


def test_client_burst_of_50_requests_is_rate_limited(tmp_path):
    clock = FakeClock()
    stamps = []

    def handler(request):
        stamps.append(clock.time())
        return httpx.Response(200, text="<p>ok</p>")

    async def go():
        async with make_client(handler, tmp_path, clock=clock) as sec:
            urls = [f"https://www.sec.gov/Archives/edgar/data/1/{i}/doc.htm" for i in range(50)]
            await asyncio.gather(*(sec.fetch_text(u) for u in urls))

    run(go())
    assert len(stamps) == 50
    assert_within_rate(stamps)


def test_real_clock_limiter_spaces_requests():
    limiter = RateLimiter(rate=5, period=0.2)
    import time

    stamps = []

    async def go():
        for _ in range(11):
            await limiter.acquire()
            stamps.append(time.monotonic())

    run(go())
    assert_within_rate(stamps, rate=5, period=0.2)


# ---- retry ----


def test_retries_429_and_503_with_backoff(tmp_path):
    clock = FakeClock()
    responses = [
        httpx.Response(429),
        httpx.Response(503, headers={"Retry-After": "7"}),
        httpx.Response(200, json=SUBMISSIONS),
    ]

    def handler(request):
        return responses.pop(0)

    async def go():
        async with make_client(handler, tmp_path, clock=clock, backoff_s=1.0) as sec:
            return await sec.list_filings(1318605)

    result = run(go())
    assert result.name == "Tesla, Inc."
    assert responses == []
    assert 1.0 in clock.sleeps  # exponential backoff on the first retry
    assert 7.0 in clock.sleeps  # Retry-After honoured on the second


def test_gives_up_after_max_retries(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    async def go():
        async with make_client(handler, tmp_path, max_retries=2) as sec:
            await sec.list_filings(1318605)

    with pytest.raises(SecRequestError, match="503"):
        run(go())
    assert len(calls) == 3


def test_retries_sec_server_errors(tmp_path):
    clock = FakeClock()
    responses = [httpx.Response(500), httpx.Response(502), httpx.Response(504), httpx.Response(200, json=SUBMISSIONS)]

    def handler(request):
        return responses.pop(0)

    async def go():
        async with make_client(handler, tmp_path, clock=clock, backoff_s=1.0) as sec:
            return await sec.list_filings(1318605)

    assert run(go()).name == "Tesla, Inc."
    assert responses == []


def _industry_page(ciks):
    entries = "".join(f"<entry><content type=\"text/xml\"><company-info name=\"ARRAY(0x1)\">"
                      f"<cik>{c:010d}</cik><sic>3711</sic></company-info></content></entry>" for c in ciks)
    return f'<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


def test_ciks_by_sic_pages_through_the_industry_list(tmp_path):
    pages = {"0": list(range(1, 101)), "100": [101, 102, 5]}  # a full page, then a short one (5 repeats)
    seen = []

    def handler(request):
        q = parse_qs(urlparse(str(request.url)).query)
        seen.append(q)
        return httpx.Response(200, text=_industry_page(pages[q.get("start", ["0"])[0]]))

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.ciks_by_sic("3711")

    url, ciks = run(go())
    assert ciks == list(range(1, 103))
    assert len(seen) == 2 and seen[0]["SIC"] == ["3711"] and seen[0]["output"] == ["atom"]
    assert url.startswith(BROWSE_URL) and "SIC=3711" in url and "start=" not in url


def test_ciks_by_sic_stops_at_max_pages(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=_industry_page(range(len(calls) * 1000, len(calls) * 1000 + 100)))

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.ciks_by_sic("7372", max_pages=2)

    _, ciks = run(go())
    assert len(calls) == 2 and len(ciks) == 200


def test_other_errors_raise_without_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    async def go():
        async with make_client(handler, tmp_path) as sec:
            await sec.fetch_text("https://www.sec.gov/Archives/edgar/data/1/2/missing.htm")

    with pytest.raises(httpx.HTTPStatusError):
        run(go())
    assert len(calls) == 1


# ---- cache ----


def test_responses_are_cached_on_disk_by_url(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=SUBMISSIONS)

    async def go():
        async with make_client(handler, tmp_path) as sec:
            await sec.list_filings(1318605)
        async with make_client(handler, tmp_path) as sec:  # new client, same cache dir
            await sec.list_filings(1318605)
            await sec.list_filings(320193)

    run(go())
    assert len(calls) == 2
    assert len(list((tmp_path / "cache").iterdir())) == 2


def test_stale_cache_is_refetched_but_archives_are_not(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=SUBMISSIONS) if "submissions" in str(request.url) else httpx.Response(200, text="x")

    async def go():
        async with make_client(handler, tmp_path, cache_ttl_s=-1) as sec:
            await sec.list_filings(1318605)
            await sec.list_filings(1318605)
            await sec.fetch_text("https://www.sec.gov/Archives/edgar/data/1/2/a.htm")
            await sec.fetch_text("https://www.sec.gov/Archives/edgar/data/1/2/a.htm")

    run(go())
    assert sum("submissions" in u for u in calls) == 2
    assert sum("Archives" in u for u in calls) == 1


# ---- fetch_text ----


def test_html_to_text_strips_markup_scripts_and_xbrl_header():
    html = """<html><head><title>T</title><style>p{color:red}</style></head><body>
    <div style="display:none"><ix:header><ix:hidden>dei:Junk 123</ix:hidden></ix:header></div>
    <p>Item&nbsp;1A. Risk&#160;Factors</p>
    <script>var x = 1;</script>
    <p>We rely on <b>Panasonic</b> &amp; others   for cells.</p>
    <table><tr><td>Supplier</td><td>Share</td></tr></table>
    </body></html>"""
    text = html_to_text(html)
    assert text.splitlines() == [
        "Item 1A. Risk Factors",
        "We rely on Panasonic & others for cells.",
        "Supplier Share",
    ]


def test_fetch_text_downloads_and_strips(tmp_path):
    def handler(request):
        return httpx.Response(200, content="<p>Café &lt;supplier&gt;</p>".encode())

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.fetch_text("https://www.sec.gov/Archives/edgar/data/1/2/a.htm")

    assert run(go()) == "Café <supplier>"


# ---- full_text_search ----


def test_fetch_raw_keeps_the_markup(tmp_path):
    xml = "<ownershipDocument><issuer><issuerCik>0001318605</issuerCik></issuer></ownershipDocument>"

    def handler(request):
        return httpx.Response(200, text=xml)

    async def run():
        async with SecClient("me@example.com", cache_dir=tmp_path, transport=httpx.MockTransport(handler)) as sec:
            return await sec.fetch_raw("https://www.sec.gov/Archives/edgar/data/1318605/000000012600000008/form4.xml")

    assert asyncio.run(run()) == xml


def test_full_text_search_params_and_parsing(tmp_path):
    seen = []
    body = {
        "hits": {
            "total": {"value": 2, "relation": "eq"},
            "hits": [
                efts_hit("0001699031-25-000041", "gral-20241231.htm", "0001699031",
                         "GRAIL, Inc.  (GRAL)  (CIK 0001699031)"),
                efts_hit("0000000001-25-000001", "ex21.htm", "0000000001",
                         "Two Class Corp  (TCA, TCB)  (CIK 0000000001)", form="10-K/A",
                         file_date="2025-06-01", sics=()),
                {"_id": "broken", "_source": {}},  # malformed: skipped
            ],
        }
    }

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json=body)

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.full_text_search('"sole supplier"', forms=["10-K", "10-Q"], since=date(2025, 1, 1))

    hits = run(go())
    assert len(seen) == 1
    url = seen[0]
    assert f"{url.scheme}://{url.host}{url.path}" == "https://efts.sec.gov/LATEST/search-index"
    params = parse_qs(urlparse(str(url)).query)
    assert params["q"] == ['"sole supplier"']
    assert params["forms"] == ["10-K,10-Q"]
    assert params["dateRange"] == ["custom"]
    assert params["startdt"] == ["2025-01-01"]
    assert "from" not in params

    assert len(hits) == 2
    first, second = hits
    assert first.company_name == "GRAIL, Inc."
    assert first.tickers == ["GRAL"]
    assert first.cik == 1699031
    assert first.form == "10-K"
    assert first.filing_date == date(2025, 3, 5)
    assert first.sic_code == "8071"
    assert first.url == "https://www.sec.gov/Archives/edgar/data/1699031/000169903125000041/gral-20241231.htm"
    assert second.tickers == ["TCA", "TCB"]
    assert second.sic_code is None


def test_full_text_search_paginates_until_limit(tmp_path):
    offsets = []

    def handler(request):
        offset = int(parse_qs(urlparse(str(request.url)).query).get("from", ["0"])[0])
        offsets.append(offset)
        page = [
            efts_hit(f"0000000001-25-{offset + i:06d}", "d.htm", "1", "Acme  (ACME)  (CIK 0000000001)")
            for i in range(100)
        ]
        return httpx.Response(200, json={"hits": {"total": {"value": 250}, "hits": page}})

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.full_text_search("supplier", limit=150)

    hits = run(go())
    assert offsets == [0, 100]
    assert len(hits) == 150


def test_full_text_search_stops_at_total_and_handles_empty(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps({"hits": {"total": {"value": 0}, "hits": []}}))

    async def go():
        async with make_client(handler, tmp_path) as sec:
            return await sec.full_text_search("nothing matches this", limit=500)

    assert run(go()) == []
    assert len(calls) == 1


def test_clients_without_their_own_limiter_share_one_per_event_loop():
    from company_graph.sec import shared_limiter

    async def go():
        a = SecClient(cache_dir=None, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        b = SecClient(cache_dir=None, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert a.limiter is None and b.limiter is None  # both fall through to the shared one
        return shared_limiter(), shared_limiter()

    first, again = run(go())
    assert first is again
    assert run(go())[0] is not first  # a new event loop gets its own: a lock can't cross loops

import asyncio
import json
import os
import time

import httpx
import pytest

from company_graph import companies
from company_graph.companies import Company, CompanyDirectory, load_sec_json, normalize_name
from company_graph.sec import SecClient, SecConfigError

# A small stand-in for SEC's company_tickers.json (same shape).
SEC_FIXTURE = {
    "0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "3": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
    "4": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    "5": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "6": {"cik_str": 1090872, "ticker": "A", "title": "AGILENT TECHNOLOGIES, INC."},
    "7": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "8": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    "9": {"cik_str": 111, "ticker": "ACME", "title": "Acme Corp"},
    "10": {"cik_str": 222, "ticker": "ACMY", "title": "Acme Ltd"},
    "11": {"cik_str": 333, "ticker": "TSM", "title": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"},
    "12": {"cik_str": 444, "ticker": "FOO", "title": "Foo Industries Corp /DE/"},
}


@pytest.fixture
def directory():
    return CompanyDirectory.from_sec_json(SEC_FIXTURE)


def test_resolve_ticker_and_name_give_same_company(directory):
    by_ticker = directory.resolve("TSLA")
    assert by_ticker == Company(symbol="TSLA", name="Tesla, Inc.", cik=1318605)
    assert directory.resolve("Tesla Inc") == by_ticker
    assert directory.resolve("Tesla") == by_ticker
    assert directory.resolve("tesla, inc.") == by_ticker
    assert directory.resolve("$TSLA") == by_ticker


def test_resolve_unknown_or_vague_returns_none(directory):
    assert directory.resolve("Customer A") is None
    assert directory.resolve("A major supplier") is None
    assert directory.resolve("") is None
    assert directory.resolve("   ") is None


def test_resolve_ambiguous_name_returns_none(directory):
    # "Acme Corp" and "Acme Ltd" are different issuers with the same normalized name.
    assert directory.resolve("Acme") is None
    assert directory.resolve("ACME") == directory.get("ACME")  # the ticker itself is still exact


def test_resolve_share_classes_pick_first_listed(directory):
    assert directory.resolve("Alphabet").symbol == "GOOGL"
    assert directory.resolve("GOOG").symbol == "GOOG"


def test_resolve_misc_forms(directory):
    assert directory.resolve("Microsoft Corporation").symbol == "MSFT"
    assert directory.resolve("nvidia").symbol == "NVDA"
    assert directory.resolve("msft").symbol == "MSFT"  # lower-case ticker fallback
    assert directory.resolve("BRK.B").symbol == "BRK-B"
    assert directory.resolve("Taiwan Semiconductor Manufacturing Co., Ltd.").symbol == "TSM"
    assert directory.resolve("Foo Industries").symbol == "FOO"


def test_normalize_name():
    assert normalize_name("Tesla, Inc.") == "tesla"
    assert normalize_name("Amazon.com, Inc.") == "amazon"
    assert normalize_name("AMAZON COM INC") == "amazon"
    assert normalize_name("Foo Industries Corp /DE/") == "foo industries"
    assert normalize_name("Procter & Gamble Co") == "procter and gamble"


def test_search_ranks_tickers_then_names(directory):
    syms = [c.symbol for c in directory.search("a")]
    assert syms[0] == "A"
    assert {"AAPL", "AMZN", "ACME", "ACMY"} <= set(syms)
    assert [c.symbol for c in directory.search("tes")] == ["TSLA"]
    assert [c.symbol for c in directory.search("semiconductor")] == ["TSM"]
    assert len(directory.search("a", limit=2)) == 2
    assert directory.search("") == []
    assert directory.search("zzzz") == []


def test_search_is_fast_on_full_size_list():
    big = {
        str(i): {"cik_str": i, "ticker": f"T{i}", "title": f"Company Number {i} Holdings Inc"}
        for i in range(12_000)
    }
    d = CompanyDirectory.from_sec_json(big)
    start = time.perf_counter()
    results = d.search("company number 11", limit=10)
    elapsed = time.perf_counter() - start
    assert len(results) == 10
    assert elapsed < 0.1


def test_aliases_for(directory):
    aliases = directory.aliases_for(["TSLA", "MSFT", "TSLA", "Samsung Electronics"])
    assert [a.symbol for a in aliases] == ["TSLA", "MSFT", "Samsung Electronics"]
    assert aliases[0].aliases == ["Tesla, Inc.", "Tesla"]
    assert aliases[1].aliases == ["MICROSOFT CORP", "MICROSOFT"]
    assert aliases[2].aliases == []


def test_aliases_are_entity_alias_used_by_news():
    from backend.entities import EntityAlias, EntityMatcher

    d = CompanyDirectory.from_sec_json(SEC_FIXTURE)
    aliases = d.aliases_for(["TSLA"])
    assert isinstance(aliases[0], EntityAlias)
    assert EntityMatcher(aliases).match("Tesla recalls vehicles", None) == ["TSLA"]


def test_load_sec_json_fetches_once_then_uses_cache(tmp_path):
    cache = tmp_path / "company_graph" / "company_tickers.json"
    calls = []

    def fetch():
        calls.append(1)
        return SEC_FIXTURE

    assert load_sec_json(cache, fetch) == SEC_FIXTURE
    assert cache.exists()
    assert load_sec_json(cache, fetch) == SEC_FIXTURE
    assert len(calls) == 1


def _fake_sec_client(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=SEC_FIXTURE)

    return lambda: SecClient("test@example.com", cache_dir=None, transport=httpx.MockTransport(handler))


def test_fetch_sec_json_goes_through_sec_client():
    seen = []
    assert companies._fetch_sec_json(_fake_sec_client(seen)) == SEC_FIXTURE
    assert str(seen[0].url) == companies.SEC_TICKERS_URL
    assert "test@example.com" in seen[0].headers["User-Agent"]


def test_fetch_sec_json_works_inside_a_running_event_loop():
    seen = []

    async def handler_like_caller():
        return companies._fetch_sec_json(_fake_sec_client(seen))

    assert asyncio.run(handler_like_caller()) == SEC_FIXTURE
    assert len(seen) == 1


def test_fetch_sec_json_without_contact_email_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    with pytest.raises(SecConfigError):
        companies._fetch_sec_json()


def test_load_sec_json_falls_back_to_stale_cache(tmp_path):
    cache = tmp_path / "company_tickers.json"
    cache.write_text(json.dumps(SEC_FIXTURE))
    old = time.time() - 30 * 24 * 3600
    os.utime(cache, (old, old))

    def failing_fetch():
        raise RuntimeError("network down")

    assert load_sec_json(cache, failing_fetch) == SEC_FIXTURE
    with pytest.raises(RuntimeError):
        load_sec_json(tmp_path / "missing.json", failing_fetch)


def test_module_level_functions_use_loaded_directory(monkeypatch):
    companies.get_directory.cache_clear()
    monkeypatch.setattr(companies, "load_sec_json", lambda: SEC_FIXTURE)
    try:
        assert companies.resolve("TSLA") == companies.resolve("Tesla Inc")
        assert companies.resolve("Customer A") is None
        assert companies.search_companies("goo")[0].symbol in {"GOOG", "GOOGL"}
        assert companies.aliases_for(["AAPL"])[0].aliases == ["Apple Inc.", "Apple"]
    finally:
        companies.get_directory.cache_clear()


# --- ensure_entity -----------------------------------------------------------

class _FakeEntity:
    def __init__(self, symbol, name, type):
        self.symbol, self.name, self.type = symbol, name, type


class _FakeSession:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.added = []

    async def get(self, model, pk):
        return self.rows.get(pk)

    def add(self, row):
        self.added.append(row)
        self.rows[row.symbol] = row


def test_ensure_entity_inserts_company_row():
    session = _FakeSession()
    tesla = Company("TSLA", "Tesla, Inc.", 1318605)
    row = asyncio.run(companies.ensure_entity(session, tesla, entity_model=_FakeEntity))
    assert (row.symbol, row.name, row.type) == ("TSLA", "Tesla, Inc.", "company")
    assert session.added == [row]


def test_ensure_entity_updates_existing_row_without_duplicating():
    existing = _FakeEntity("TSLA", "Tesla Motors", "company")
    session = _FakeSession({"TSLA": existing})
    tesla = Company("TSLA", "Tesla, Inc.", 1318605)
    row = asyncio.run(companies.ensure_entity(session, tesla, entity_model=_FakeEntity))
    assert row is existing
    assert row.name == "Tesla, Inc."
    assert session.added == []


def test_ensure_entity_with_real_entities_model():
    entity_mod = pytest.importorskip(
        "backend.models.entity", reason="F0 has not added backend/models/entity.py (the `entities` table) yet"
    )
    session = _FakeSession()
    row = asyncio.run(companies.ensure_entity(session, Company("TSLA", "Tesla, Inc.", 1318605)))
    assert isinstance(row, entity_mod.Entity)
    assert row.type == "company"


def test_size_rank_follows_secs_order_and_lookup_by_cik():
    d = CompanyDirectory.from_sec_json(SEC_FIXTURE)
    assert d.size_rank(1318605) == 0  # TSLA is first in the fixture
    assert d.size_rank(1652044) == 2  # GOOGL and GOOG share one CIK and one rank
    assert d.size_rank(1018724) == 3  # AMZN comes right after it
    assert d.size_rank(42) == len(d.__dict__["_size_rank"])  # unlisted: after every listed company
    assert d.by_cik(1652044).symbol == "GOOGL"  # the first listing for the CIK
    assert d.by_cik(42) is None


@pytest.mark.parametrize("title,short", [
    ("JPMORGAN CHASE & CO", "JPMORGAN CHASE"),       # connector left behind by "& CO"
    ("ELI LILLY & Co", "ELI LILLY"),
    ("Merck & Co., Inc.", "Merck"),
    ("PROGRESSIVE CORP/OH/", "PROGRESSIVE"),         # upper-case state tag with no space
    ("MARRIOTT INTERNATIONAL INC /MD/", "MARRIOTT INTERNATIONAL"),
    ("JOHNSON & JOHNSON", "JOHNSON & JOHNSON"),       # an "&" inside the name stays
    ("AT&T INC.", "AT&T"),
    ("111, Inc.", "111"),                             # number-only names are kept
    ("Tesla, Inc.", "Tesla"),
])
def test_short_name_gives_the_name_filings_use(title, short):
    assert companies.short_name(title) == short


def test_aliases_join_search_and_resolve():
    from backend.entities import EntityAlias

    directory = CompanyDirectory.from_sec_json(
        SEC_FIXTURE, aliases=[EntityAlias(symbol="GOOGL", aliases=["Google", "Alphabet"]), EntityAlias(symbol="ZZZ", aliases=["Nobody"])]
    )
    # "goo" is a ticker prefix of GOOG/GOOGL and an alias prefix (Google); each company appears once.
    assert sorted(c.symbol for c in directory.search("goo")) == ["GOOG", "GOOGL"]
    assert [c.symbol for c in directory.search("googl")] == ["GOOGL"]
    assert directory.search("google")[0].symbol == "GOOGL"
    assert directory.resolve("google").symbol == "GOOGL"
    assert directory.resolve("Google") is not None
    # An alias for an unlisted symbol is ignored rather than crashing.
    assert directory.search("nobody") == []

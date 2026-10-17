import json
from pathlib import Path

import pytest

from backend.llm.client import DEFAULT_MODEL
from company_graph import config
from company_graph.schemas import FIXTURES_DIR, GraphResponse, fixture_tickers, load_fixture

FRONTEND_FIXTURES = Path(__file__).resolve().parents[2] / "frontend/src/features/company-graph/fixtures"
ENV_NAMES = ["GRAPH_LINK_TTL_DAYS", "GRAPH_EVENT_WINDOW_DAYS", "GRAPH_MAX_LINKED", "GRAPH_NEWS_TTL_HOURS",
             "GRAPH_NEWS_DAILY_BUDGET", "GRAPH_FAKE", "GRAPH_LLM_MODEL", "SEC_CONTACT_EMAIL", "NEWSAPI_KEY",
             "DATABASE_URL", "GRAPH_BOARD_TTL_DAYS", "GRAPH_BOARD_MAX_FILINGS"]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_match_the_spec(clean_env):
    cfg = config.load()
    assert (cfg.graph_link_ttl_days, cfg.graph_event_window_days, cfg.graph_max_linked) == (7, 7, 20)
    assert (cfg.graph_news_ttl_hours, cfg.graph_news_daily_budget) == (6, 40)
    assert cfg.fake is False
    assert cfg.graph_llm_model == DEFAULT_MODEL
    assert cfg.link_ttl_s == 7 * 86400
    assert (cfg.graph_board_ttl_days, cfg.graph_board_max_filings, cfg.board_ttl_s) == (7, 40, 7 * 86400)


def test_env_overrides_each_setting_by_its_upper_case_name(clean_env):
    clean_env.setenv("GRAPH_MAX_LINKED", "5")
    clean_env.setenv("GRAPH_FAKE", "1")
    clean_env.setenv("GRAPH_LINK_TTL_DAYS", "0.5")
    clean_env.setenv("SEC_CONTACT_EMAIL", " me@example.com ")
    cfg = config.load()
    assert cfg.graph_max_linked == 5 and isinstance(cfg.graph_max_linked, int)
    assert cfg.fake is True
    assert cfg.link_ttl_s == 43200
    assert cfg.sec_contact_email == "me@example.com"


@pytest.mark.parametrize("name,value", [("GRAPH_FAKE", "2"), ("GRAPH_MAX_LINKED", "0"), ("GRAPH_BOARD_MAX_FILINGS", "0")])
def test_bad_values_stop_with_a_clear_message(clean_env, name, value):
    clean_env.setenv(name, value)
    with pytest.raises(SystemExit, match=name):
        config.load()


def test_there_are_three_fixtures_within_the_spec_ranges():
    assert fixture_tickers() == ["AAPL", "NVDA", "TSLA"]
    for ticker in fixture_tickers():
        graph = load_fixture(ticker)
        assert graph.company.symbol == ticker
        assert 6 <= len(graph.links) <= 10
        assert 1 <= len(graph.highlights) <= 3


def test_fixtures_are_internally_consistent():
    for ticker in fixture_tickers():
        graph = load_fixture(ticker)
        symbols = {n.symbol for n in graph.nodes}
        assert all(link.source == ticker and link.target in symbols for link in graph.links)
        assert all(h.target in symbols for h in graph.highlights)
        assert all(link.evidence_url and h.source_url for link in graph.links for h in graph.highlights)


def test_unknown_ticker_has_no_fixture():
    assert load_fixture("ZZZZ") is None


def test_api_shape_rejects_unknown_fields_and_values():
    good = json.loads((FIXTURES_DIR / "TSLA.json").read_text())
    GraphResponse.model_validate(good)
    with pytest.raises(ValueError):
        GraphResponse.model_validate({**good, "extra": 1})
    with pytest.raises(ValueError):
        GraphResponse.model_validate({**good, "status": "pending"})


def test_backend_and_frontend_fixtures_are_identical():
    for ticker in fixture_tickers():
        backend = json.loads((FIXTURES_DIR / f"{ticker}.json").read_text())
        frontend = json.loads((FRONTEND_FIXTURES / f"{ticker}.json").read_text())
        assert backend == frontend, f"{ticker}.json differs between company_graph/ and frontend/"


def test_backend_and_frontend_board_fixtures_are_identical():
    backend_dir = FIXTURES_DIR.parent / "board_fixtures"
    frontend_dir = FRONTEND_FIXTURES.parent / "board-fixtures"
    names = sorted(p.name for p in backend_dir.glob("*.json"))
    assert names and names == sorted(p.name for p in frontend_dir.glob("*.json"))
    for name in names:
        assert json.loads((backend_dir / name).read_text()) == json.loads((frontend_dir / name).read_text()), \
            f"{name} differs between company_graph/board_fixtures/ and frontend/"

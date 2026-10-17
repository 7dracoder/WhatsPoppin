"""Company graph settings: defaults below, each overridable in .env by its upper-case name.

The defaults are placeholders to tune. Set GRAPH_FAKE=1 to serve company_graph/fixtures/
and call no outside service.
"""

import os
from dataclasses import dataclass, fields

from backend.llm.client import DEFAULT_MODEL


@dataclass(frozen=True)
class Config:
    graph_link_ttl_days: float = 7       # how long stored links are reused before a rebuild
    graph_event_window_days: float = 7   # how far back highlights look
    graph_max_linked: int = 20           # most linked companies per graph (10-Ks name 15-30; hop 2 needs the breadth)
    graph_news_ttl_hours: float = 6      # how long one company's news result is reused
    graph_news_daily_budget: int = 40    # most NewsAPI requests this feature may make per day
    graph_board_ttl_days: float = 7      # how long a stored board is reused before a rebuild
    graph_board_max_filings: int = 40    # most Form 3 / 4 filings read per board run (newest first)
    graph_fake: int = 0                  # 1 = serve fixtures, call nothing outside
    graph_llm_provider: str = "openrouter"  # "openrouter" (the shared client: Nicrron or OpenRouter) or "anthropic" (ANTHROPIC_API_KEY)
    graph_llm_model: str = DEFAULT_MODEL  # OpenRouter model id; the project default lives in backend/llm/client.py
    graph_anthropic_model: str = "claude-opus-5-5"  # Claude model id when GRAPH_LLM_PROVIDER=anthropic
    sec_contact_email: str = ""          # sent in the SEC User-Agent; required for any SEC call
    newsapi_key: str = ""
    graph_social_ttl_hours: float = 6    # how long one company's X search result is reused
    graph_social_daily_budget: int = 20  # most X searches this feature may make per day (each reads up to 100 posts)
    graph_social_min_likes: int = 5      # posts with fewer likes are not sent to Jev (most are noise)
    x_bearer_token: str = ""
    graph_pair_ttl_hours: float = 6      # how long news about one pair of companies is reused
    graph_pair_daily_budget: int = 30    # most pair searches per source (NewsAPI, X) per day
    database_url: str = ""

    @property
    def fake(self) -> bool:
        return self.graph_fake == 1

    @property
    def link_ttl_s(self) -> float:
        return self.graph_link_ttl_days * 86400

    @property
    def board_ttl_s(self) -> float:
        return self.graph_board_ttl_days * 86400

    @property
    def event_window_s(self) -> float:
        return self.graph_event_window_days * 86400

    @property
    def news_ttl_s(self) -> float:
        return self.graph_news_ttl_hours * 3600

    @property
    def social_ttl_s(self) -> float:
        return self.graph_social_ttl_hours * 3600

    @property
    def pair_ttl_s(self) -> float:
        return self.graph_pair_ttl_hours * 3600


def load() -> Config:
    values = {}
    for f in fields(Config):
        raw = os.environ.get(f.name.upper())
        if raw:
            values[f.name] = f.type(raw.strip())  # the annotation (float/int/str), not the default's type
    cfg = Config(**values)
    if cfg.graph_fake not in (0, 1):
        raise SystemExit(f"GRAPH_FAKE must be 0 or 1, got {cfg.graph_fake}")
    if cfg.graph_llm_provider not in ("openrouter", "anthropic"):
        raise SystemExit(f"GRAPH_LLM_PROVIDER must be openrouter or anthropic, got {cfg.graph_llm_provider!r}")
    if cfg.graph_max_linked < 1:
        raise SystemExit(f"GRAPH_MAX_LINKED must be at least 1, got {cfg.graph_max_linked}")
    if cfg.graph_board_max_filings < 1:
        raise SystemExit(f"GRAPH_BOARD_MAX_FILINGS must be at least 1, got {cfg.graph_board_max_filings}")
    return cfg

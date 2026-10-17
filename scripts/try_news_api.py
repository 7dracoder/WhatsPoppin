"""Manual smoke test for the news_api ingestion layer — run this directly to
see real NewsAPI results printed to the terminal, no backend/frontend
needed.

Also saves the fetched articles to a JSON file (default:
scripts/output/news_<query>_<timestamp>.json) so other scripts — e.g.
bench_jev_news.py --input <file> — can reuse this one fetch instead of
calling NewsAPI again. NewsAPI's free tier caps you at 100 requests/day,
so fetch once here and replay from the file for everything downstream.

Usage:
    NEWSAPI_KEY=your_key_here python scripts/try_news_api.py
    # or add NEWSAPI_KEY=... to the repo's .env file and just run:
    python scripts/try_news_api.py [--query nvidia] [--count 100] [--out PATH]

Get a free key at https://newsapi.org/register (Developer plan: 100
requests/day, dev/testing use only — see new_specs/ingestion/news-aggregator.md).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Running this script directly (not via `python -m`) puts scripts/ on
# sys.path, not the repo root — add it so `ingestion` is importable.
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT_DIR = REPO_ROOT / "scripts" / "output"

from backend.entities import load_entities  # noqa: E402
from backend.ingestion.news_api import (  # noqa: E402
    NewsApiError,
    NewsApiGateway,
    NewsQueryFilters,
    poll_news,
)


def _load_dotenv_fallback(key: str) -> str | None:
    """Minimal .env reader — avoids adding python-dotenv as a dependency
    for a one-off script. Only used if the var isn't already in the
    environment."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip()
    return None


def get_api_key() -> str:
    key = os.environ.get("NEWSAPI_KEY") or _load_dotenv_fallback("NEWSAPI_KEY")
    if not key:
        print(
            "No NEWSAPI_KEY found (checked env and .env).\n"
            "Get a free key at https://newsapi.org/register, then either:\n"
            "  export NEWSAPI_KEY=your_key_here\n"
            "or add a line to .env:\n"
            "  NEWSAPI_KEY=your_key_here",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--query", default="nvidia", help="keyword_query to search NewsAPI for (default nvidia)")
    parser.add_argument("--count", type=int, default=100, help="page_size, max 100 (default 100)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to save fetched articles as JSON "
        f"(default: {DEFAULT_OUTPUT_DIR}/news_<query>_<timestamp>.json)",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    gateway = NewsApiGateway(api_key=api_key)

    filters = NewsQueryFilters(keyword_query=args.query, language="en", page_size=args.count)
    entities = load_entities()  # top-50 S&P watchlist, loaded once at startup

    try:
        items = poll_news(filters=filters, gateway=gateway, entities=entities)
    except NewsApiError as exc:
        print(f"NewsAPI call failed: [{exc.code}] {exc.message} (retryable={exc.retryable})")
        sys.exit(1)

    if not items:
        print("No articles returned — try a different keyword_query.")
        return

    for item in items:
        print(f"- {item.title}")
        print(f"  source:       {item.source}")
        print(f"  id:           {item.id}")
        print(f"  author:       {item.author}")
        print(f"  text:         {item.text}")
        print(f"  entities:     {item.entities or '(none matched)'}")
        print(f"  url:          {item.url}")
        print(f"  published_at: {item.published_at}")
        print()

    out_path = args.out
    if out_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = DEFAULT_OUTPUT_DIR / f"news_{args.query}_{timestamp}.json"
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "query": args.query,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "articles": [item.model_dump(mode="json") for item in items],
            },
            indent=2,
        )
    )
    print(f"Saved {len(items)} articles to {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")


if __name__ == "__main__":
    main()

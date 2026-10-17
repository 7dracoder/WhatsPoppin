"""Fetch real NVDA news from NewsAPI and run the classification/ library
(Jev, via OpenRouter) against every article, to see real latency numbers
and a human-reviewable transcript — not a synthetic smoke test.

This measures SPEED (per-call latency, end-to-end time) directly. It does
NOT measure ACCURACY automatically — there's no ground-truth label for
"is this NVDA article actually positive/negative," so accuracy here means
printing title + label + confidence side by side and eyeballing whether
the judgment is sane. Don't read the summary's label distribution as a
validated accuracy number; it's a sentiment histogram, not a scorecard.

Saves the fetched articles and their classification results to a JSON
file (default: scripts/output/jev_news_bench_<timestamp>.json) so you
have a persistent record to review or diff against later, instead of
only scrolling terminal output.

Calls run concurrently (a thread pool, default 10 at a time) since each
call is network-bound (~200ms round trip) — running 100 of them one at a
time serializes 100 round trips for no reason.

Before scoring sentiment, articles are passed through a relevance filter
(classification.relevance.filter_relevant) — a keyword match alone pulls
in noise (a PyPI release page whose package name happens to contain
"nvidia", a roundup article that namedrops NVDA once among five other
companies), and that noise shouldn't count toward NVDA's sentiment any
more than it should toward NVDA's alert signal. Dropped articles are
listed separately so you can see what got filtered and why. Use
--no-relevance-filter to classify every fetched article unfiltered
(e.g. to see what the filter is actually removing).

By default this calls NewsAPI directly (needs NEWSAPI_KEY) — but NewsAPI's
free tier caps you at 100 requests/day, so pass --input <file> to replay
articles already saved by try_news_api.py instead of fetching again:

    python scripts/try_news_api.py --out scripts/output/news.json
    python scripts/bench_jev_news.py --input scripts/output/news.json

Usage:
    python scripts/bench_jev_news.py [--count N] [--mode sentiment|boolean] [--concurrency N] [--out PATH] [--no-relevance-filter] [--input PATH]
    # needs OPENROUTER set (env or .env); NEWSAPI_KEY only if --input isn't given
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT_DIR = REPO_ROOT / "scripts" / "output"

from backend.classification import BooleanSpec, JevError, SentimentSpec, classify, filter_relevant  # noqa: E402
from backend.ingestion.news_api import (  # noqa: E402
    ContentItem,
    NewsApiError,
    NewsApiGateway,
    NewsQueryFilters,
    poll_news,
)
from backend.entities import load_entities  # noqa: E402


def _load_dotenv_fallback(key: str) -> str | None:
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


def require_env(key: str) -> str:
    value = os.environ.get(key) or _load_dotenv_fallback(key)
    if not value:
        print(f"No {key} found (checked env and .env). Set it and retry.", file=sys.stderr)
        sys.exit(1)
    os.environ.setdefault(key, value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100, help="how many NVDA articles to fetch (max 100)")
    parser.add_argument(
        "--mode",
        choices=["sentiment", "boolean"],
        default="sentiment",
        help="sentiment: 3-way positive/negative/neutral read. "
        "boolean: yes/no answer to a fixed NVDA-relevance question.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to save fetched articles + results as JSON "
        f"(default: {DEFAULT_OUTPUT_DIR}/jev_news_bench_<timestamp>.json)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="how many classify() calls to run at once (default 10)",
    )
    parser.add_argument(
        "--entity",
        default="NVDA",
        help="entity to filter relevance against and ask the boolean question about (default NVDA)",
    )
    parser.add_argument(
        "--no-relevance-filter",
        action="store_true",
        help="skip the relevance filter and classify every fetched article as-is",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="replay articles from a JSON file saved by try_news_api.py's --out "
        "instead of calling NewsAPI (skips NEWSAPI_KEY entirely)",
    )
    args = parser.parse_args()

    require_env("OPENROUTER")

    if args.input is not None:
        data = json.loads(args.input.read_text())
        items = [ContentItem.model_validate(raw) for raw in data["articles"]]
        print(f"Loaded {len(items)} articles from {args.input} (query={data.get('query')!r}, "
              f"fetched_at={data.get('fetched_at')!r}).")
    else:
        newsapi_key = require_env("NEWSAPI_KEY")
        gateway = NewsApiGateway(api_key=newsapi_key)
        filters = NewsQueryFilters(keyword_query="nvidia", language="en", page_size=min(args.count, 100))
        entities = load_entities()

        try:
            items = poll_news(filters=filters, gateway=gateway, entities=entities)
        except NewsApiError as exc:
            print(f"NewsAPI call failed: [{exc.code}] {exc.message} (retryable={exc.retryable})")
            sys.exit(1)

        if not items:
            print("No articles returned.")
            return

        print(f"Fetched {len(items)} real NVDA articles.")

    items = items[: args.count]

    dropped_records: list[dict] = []
    if not args.no_relevance_filter:
        relevant_items, dropped_items = filter_relevant(items, args.entity, max_workers=args.concurrency)
        print(
            f"Relevance filter ({args.entity}): kept {len(relevant_items)}, "
            f"dropped {len(dropped_items)} as not actually about {args.entity}."
        )
        for item in dropped_items:
            print(f"  dropped: {item.title[:80]!r}")
            dropped_records.append({"article": item.model_dump(mode="json")})
        items = relevant_items
    print(f"\nClassifying {len(items)} articles with mode={args.mode!r}...\n")

    spec = SentimentSpec() if args.mode == "sentiment" else BooleanSpec(
        question="Does this article suggest something materially good or bad for NVDA specifically?"
    )

    def classify_one(item):
        start = time.perf_counter()
        try:
            result = classify(item.title, item.text, spec)
        except JevError as exc:
            return item, (time.perf_counter() - start) * 1000, None, exc
        return item, (time.perf_counter() - start) * 1000, result, None

    latencies_ms: list[float] = []
    label_counts: dict[str, int] = {}
    failures = 0
    records: list[dict] = [None] * len(items)
    batch_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_index = {pool.submit(classify_one, item): i for i, item in enumerate(items)}
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            item, elapsed_ms, result, exc = future.result()

            if exc is not None:
                failures += 1
                print(f"[{i + 1:>3}] FAILED ({elapsed_ms:.0f}ms): {item.title[:70]!r} -> {exc}")
                records[i] = {
                    "article": item.model_dump(mode="json"),
                    "elapsed_ms": elapsed_ms,
                    "error": str(exc),
                }
                continue

            latencies_ms.append(elapsed_ms)
            label = str(result.label)
            label_counts[label] = label_counts.get(label, 0) + 1
            confidence = result.probability if result.probability is not None else result.confidence
            conf_str = f"{confidence:.2f}" if confidence is not None else "n/a"
            print(f"[{i + 1:>3}] {elapsed_ms:>6.0f}ms  {label:<10} (conf={conf_str})  {item.title[:70]!r}")
            records[i] = {
                "article": item.model_dump(mode="json"),
                "elapsed_ms": elapsed_ms,
                "result": result.model_dump(mode="json"),
            }

    total_s = time.perf_counter() - batch_start

    print("\n--- summary ---")
    print(f"fetched:         {len(items) + len(dropped_records)}")
    print(f"dropped (irrelevant): {len(dropped_records)}")
    print(f"classified:      {len(items)}")
    print(f"succeeded:       {len(latencies_ms)}")
    print(f"failed:          {failures}")
    print(f"total wall time: {total_s:.1f}s")
    if latencies_ms:
        sorted_lat = sorted(latencies_ms)
        p95_idx = min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))
        print(f"latency min/median/p95/max (ms): "
              f"{sorted_lat[0]:.0f} / {statistics.median(sorted_lat):.0f} / "
              f"{sorted_lat[p95_idx]:.0f} / {sorted_lat[-1]:.0f}")
    print(f"label distribution: {label_counts}")
    print(
        "\nAccuracy isn't computed above — there's no ground truth here. "
        "Scroll the per-article lines and eyeball whether the label matches "
        "what the headline actually says."
    )

    out_path = args.out
    if out_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = DEFAULT_OUTPUT_DIR / f"jev_news_bench_{timestamp}.json"
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "mode": args.mode,
                "entity": args.entity,
                "relevance_filter_applied": not args.no_relevance_filter,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "fetched": len(items) + len(dropped_records),
                    "dropped_irrelevant": len(dropped_records),
                    "classified": len(items),
                    "succeeded": len(latencies_ms),
                    "failed": failures,
                    "total_wall_time_s": total_s,
                    "label_counts": label_counts,
                },
                "dropped": dropped_records,
                "records": records,
            },
            indent=2,
        )
    )
    print(f"\nSaved {len(records)} classified + {len(dropped_records)} dropped records to {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")


if __name__ == "__main__":
    main()

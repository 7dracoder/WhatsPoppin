"""Fetch a real account's tweets via ingestion/twitter_lookup and run the
classification/ library (Jev, via OpenRouter) against every one, to see
real latency numbers and a human-reviewable transcript — not a synthetic
smoke test. Mirrors scripts/bench_jev_news.py's pattern for the Twitter
side (see new_specs/twitter-jev-classification.md Functional Requirement 8).

This measures SPEED directly, and gives per-post label + full
probabilities for manual review — specifically to check the "does
everything just get marked unrelated" risk called out in that spec's
Design/Approach, the same way bench_jev_news.py was used to tune
is_relevant()'s wording for news. There's no ground truth, so this is
eyeball review, not automated accuracy scoring.

Saves every classified (and dropped) post to a JSON file (default:
scripts/output/twitter_jev_test.json) for review.

Usage:
    python scripts/bench_jev_twitter.py --handle realDonaldTrump --entity NVDA [--tags oil,shipping] [--count N] [--concurrency N] [--threshold F] [--out PATH]
    # needs OPENROUTER (for Jev + the Haiku category fallback) and X_BEARER_TOKEN set (env or .env)
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

from backend.api.twitter import (  # noqa: E402
    ACCOUNT_LOOKBACK,
    _build_spec,
    _classify_item,
    _resolve_categories,
)
from backend.ingestion.twitter_lookup import TwitterApiError, TwitterApiGateway, lookup_account  # noqa: E402


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
            return value.strip().strip('"')
    return None


def require_env(key: str) -> str:
    value = os.environ.get(key) or _load_dotenv_fallback(key)
    if not value:
        print(f"No {key} found (checked env and .env). Set it and retry.", file=sys.stderr)
        sys.exit(1)
    os.environ.setdefault(key, value)
    return value


def main() -> None:
    # Tweet text can contain characters the Windows console's default
    # encoding can't print (smart quotes, em-dashes, emoji); without this,
    # one such tweet crashes the whole run after burning real Jev calls.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", required=True, help="X account handle to fetch, e.g. realDonaldTrump")
    parser.add_argument("--entity", default="NVDA", help="entity to classify posts against (default NVDA)")
    parser.add_argument("--tags", default=None, help="comma-separated tags, skips the Haiku category step")
    parser.add_argument("--count", type=int, default=None, help="classify at most N fetched posts")
    parser.add_argument("--concurrency", type=int, default=10, help="concurrent classify() calls (default 10)")
    parser.add_argument("--threshold", type=float, default=0.6, help="drop a post if unrelated prob >= this")
    parser.add_argument(
        "--out", type=Path, default=None,
        help=f"output JSON path (default: {DEFAULT_OUTPUT_DIR}/twitter_jev_test.json)",
    )
    args = parser.parse_args()

    require_env("OPENROUTER")
    bearer_token = require_env("X_BEARER_TOKEN")

    gateway = TwitterApiGateway(bearer_token=bearer_token)
    now = datetime.now(timezone.utc)

    try:
        items = lookup_account(args.handle, now - ACCOUNT_LOOKBACK, now, gateway, entities=[])
    except TwitterApiError as exc:
        print(f"Twitter lookup failed: [{exc.code}] {exc.message} (retryable={exc.retryable})")
        sys.exit(1)

    if not items:
        print("No tweets returned.")
        return
    if args.count:
        items = items[: args.count]
    print(f"Fetched {len(items)} tweet(s) from @{args.handle}.")

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
    categories = _resolve_categories(args.entity, tags)
    print(f"Indirect-relevance categories: {categories}")
    spec = _build_spec(args.entity, categories)

    def classify_one(item):
        start = time.perf_counter()
        result = _classify_item(item, spec)
        return item, (time.perf_counter() - start) * 1000, result

    latencies_ms: list[float] = []
    label_counts: dict[str, int] = {}
    kept_records: list[dict] = []
    dropped_records: list[dict] = []
    batch_start = time.perf_counter()

    def save(total_s: float) -> None:
        """Always called, even on a mid-run crash — a long paid run's
        results shouldn't be lost over one bad print or network blip.
        """
        print("\n--- summary ---")
        print(f"fetched:    {len(items)}")
        print(f"classified: {len(latencies_ms)}")
        print(f"kept:       {len(kept_records)}")
        print(f"dropped:    {len(dropped_records)}")
        print(f"total wall time: {total_s:.1f}s")
        if latencies_ms:
            sorted_lat = sorted(latencies_ms)
            p95_idx = min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))
            print(f"latency min/median/p95/max (ms): "
                  f"{sorted_lat[0]:.0f} / {statistics.median(sorted_lat):.0f} / "
                  f"{sorted_lat[p95_idx]:.0f} / {sorted_lat[-1]:.0f}")
        print(f"label distribution: {label_counts}")
        print(
            "\nAccuracy isn't computed above — there's no ground truth. Scroll the "
            "per-tweet lines and eyeball whether 'unrelated' is doing its job, not "
            "swallowing real signal (see new_specs/twitter-jev-classification.md "
            "Design/Approach)."
        )

        out_path = args.out or (DEFAULT_OUTPUT_DIR / "twitter_jev_test.json")
        out_path = out_path.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "handle": args.handle,
                    "entity": args.entity,
                    "categories": categories,
                    "threshold": args.threshold,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "summary": {
                        "fetched": len(items),
                        "classified": len(latencies_ms),
                        "kept": len(kept_records),
                        "dropped": len(dropped_records),
                        "total_wall_time_s": total_s,
                        "label_counts": label_counts,
                    },
                    "kept": kept_records,
                    "dropped": dropped_records,
                },
                indent=2,
            )
        )
        print(f"\nSaved {len(kept_records)} kept + {len(dropped_records)} dropped records to "
              f"{out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")

    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(classify_one, item) for item in items]
            for i, future in enumerate(as_completed(futures)):
                item, elapsed_ms, result = future.result()

                if result is None:
                    print(f"[{i + 1:>3}] FAILED ({elapsed_ms:.0f}ms): {item.text[:70]!r}")
                    dropped_records.append({"tweet": item.model_dump(mode="json"), "elapsed_ms": elapsed_ms,
                                             "error": "JevError"})
                    continue

                latencies_ms.append(elapsed_ms)
                label_counts[result.label] = label_counts.get(result.label, 0) + 1
                unrelated_prob = (result.probabilities or {}).get("unrelated", 0.0)
                kept = unrelated_prob < args.threshold
                record = {"tweet": item.model_dump(mode="json"), "elapsed_ms": elapsed_ms,
                           "result": result.model_dump(mode="json"), "kept": kept}
                print(f"[{i + 1:>3}] {elapsed_ms:>6.0f}ms  {result.label:<10} "
                      f"(unrelated={unrelated_prob:.2f}, {'KEPT' if kept else 'dropped'})  {item.text[:70]!r}")
                (kept_records if kept else dropped_records).append(record)
    finally:
        save(time.perf_counter() - batch_start)


if __name__ == "__main__":
    main()

import json
from pathlib import Path

from backend.ingestion.twitter_lookup.normalize import normalize_batch, normalize_tweet

FIXTURES = Path(__file__).parent / "fixtures"


def _load_tweets() -> list[dict]:
    data = json.loads((FIXTURES / "sample_tweets_response.json").read_text())
    tweets = data["data"]
    users = {u["id"]: u["username"] for u in data["includes"]["users"]}
    for t in tweets:
        t["_author_username"] = users.get(t["author_id"])
    return tweets


def test_normalize_tweet_maps_fields():
    raw = _load_tweets()[0]
    item = normalize_tweet(raw)

    assert item.id == "1234567890"
    assert item.author == "realDonaldTrump"
    assert item.source == "twitter"
    assert item.title is None
    assert item.text.startswith("Nvidia just announced")
    assert item.url == "https://x.com/realDonaldTrump/status/1234567890"
    assert item.engagement.likes == 500
    assert item.engagement.reposts == 120
    assert item.engagement.replies == 30
    assert item.engagement.quotes == 10


def test_normalize_tweet_raises_on_empty_text():
    raw = _load_tweets()[1]  # fixture's second tweet has empty text
    try:
        normalize_tweet(raw)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "text" in str(exc)


def test_normalize_batch_skips_malformed_tweets():
    items = normalize_batch(_load_tweets())

    assert len(items) == 1
    assert items[0].id == "1234567890"


def test_entity_matcher_short_ticker_matches_only_in_capitals():
    from backend.ingestion.twitter_lookup.entity_match import EntityAlias, EntityMatcher

    matcher = EntityMatcher([EntityAlias(symbol="A", aliases=["Agilent"])])
    assert matcher.match("what a day for chips") == []
    assert matcher.match("$A and Agilent both up") == ["A"]

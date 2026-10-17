import pytest

from backend.ingestion.twitter_lookup.query_builder import MAX_QUERY_CHARS, build_keyword_query


def test_phrase_only():
    assert build_keyword_query(phrase="strait of hormuz") == '"strait of hormuz"'


def test_hashtags_only():
    assert build_keyword_query(hashtags=["nvidia", "#AI"]) == "#nvidia OR #AI"


def test_cashtags_only():
    assert build_keyword_query(cashtags=["nvda", "$TSLA"]) == "$NVDA OR $TSLA"


def test_combined_phrase_hashtag_cashtag():
    query = build_keyword_query(phrase="strait of hormuz", hashtags=["hormuz"], cashtags=["oil"])
    assert query == '"strait of hormuz" OR #hormuz OR $OIL'


def test_nothing_given_raises():
    with pytest.raises(ValueError, match="At least one of"):
        build_keyword_query()


def test_long_query_trimmed_to_fit_char_limit():
    many_hashtags = [f"tag{i}" * 10 for i in range(20)]  # well over 512 chars combined
    query = build_keyword_query(phrase="strait of hormuz", hashtags=many_hashtags)

    assert len(query) <= MAX_QUERY_CHARS
    assert query.startswith('"strait of hormuz"')  # phrase kept first, trailing terms dropped


def test_phrase_alone_over_limit_raises():
    with pytest.raises(ValueError, match="exceeds"):
        build_keyword_query(phrase="x" * (MAX_QUERY_CHARS + 10))

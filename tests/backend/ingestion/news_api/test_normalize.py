import json
from pathlib import Path

import pytest

from backend.ingestion.news_api.normalize import normalize_article, normalize_batch

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_normalize_article_maps_fields():
    raw = _load("sample_article.json")
    item = normalize_article(raw)

    assert item.source == "news"
    assert item.id == raw["url"]
    assert item.url == raw["url"]
    assert item.author == "The Verge"  # outlet name, not raw["author"]
    assert item.title == raw["title"]
    assert item.text == raw["content"]
    assert item.entities == []


def test_normalize_article_missing_url_raises():
    raw = {"title": "headline", "publishedAt": "2026-10-09T10:00:00Z"}
    with pytest.raises(ValueError, match="url"):
        normalize_article(raw)


def test_normalize_article_missing_title_raises():
    raw = {"url": "https://example.com/a", "publishedAt": "2026-10-09T10:00:00Z"}
    with pytest.raises(ValueError, match="title"):
        normalize_article(raw)


def test_normalize_article_allows_null_content_and_author():
    raw = {
        "source": {"name": "Some Outlet"},
        "author": None,
        "title": "A headline",
        "url": "https://example.com/a",
        "publishedAt": "2026-10-09T10:00:00Z",
        "content": None,
    }
    item = normalize_article(raw)
    assert item.text is None
    assert item.author == "Some Outlet"


def test_normalize_batch_skips_malformed_articles():
    response = _load("sample_everything_response.json")
    items = normalize_batch(response["articles"])

    # Second article has url=None and title="[Removed]" with other nulls —
    # url is missing so it's skipped; only the first, valid article remains.
    assert len(items) == 1
    assert items[0].title == "Nvidia unveils new AI chip as NVDA shares climb"

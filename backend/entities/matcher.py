"""Lightweight substring/alias matching to tag content (news articles,
market titles, tweets — any text) with tracked entities (tickers/company
names).

Deliberately simple: no NLP/NER. This will produce false positives on
generic-word company names (e.g. "Target") — accepted for now, since
jev-classifier (or a later relevance pass) can correct it; the db design's
message_entities.relevant column already has a slot for exactly that kind
of correction.

Source-agnostic by design: this only operates on plain text, so any
ingestion source (news, Kalshi, Twitter) can tag its own content with the
same tracked-entity list without duplicating this logic.
"""

import re

from .models import EntityAlias


class EntityMatcher:
    def __init__(self, entities: list[EntityAlias]):
        """Pre-builds the lookup once per batch of entities, rather than
        re-scanning the entity list per item.
        """
        self._patterns: dict[str, list[re.Pattern]] = {}
        for entity in entities:
            self._patterns[entity.symbol] = [
                _term_pattern(entity.symbol, is_symbol=True),
                *(_term_pattern(alias) for alias in entity.aliases),
            ]

    def match(self, title: str, text: str | None) -> list[str]:
        """Word-boundary match of each tracked entity's symbol + aliases
        against title + text. Aliases match in any case; a ticker symbol
        matches only as written (see _term_pattern). Returns matched
        symbols, deduped, in the order entities were passed to __init__.
        """
        haystack = f"{title} {text or ''}"
        matched: list[str] = []
        for symbol, patterns in self._patterns.items():
            if any(pattern.search(haystack) for pattern in patterns):
                matched.append(symbol)
        return matched


def _term_pattern(term: str, is_symbol: bool = False) -> re.Pattern:
    """A ticker ("A", "ON", "NVDA") must appear in capitals, so short
    tickers don't tag every text containing the ordinary words "a" or
    "on". A symbol that isn't a ticker (a non-US company stored under its
    name) and every alias match in any case.
    """
    is_ticker = is_symbol and term.isupper() and " " not in term
    return re.compile(rf"\b{re.escape(term)}\b", 0 if is_ticker else re.IGNORECASE)

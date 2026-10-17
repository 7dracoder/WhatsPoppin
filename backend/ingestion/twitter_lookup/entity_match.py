"""Lightweight substring/alias matching to tag tweets with tracked entities
(tickers/company names).

Deliberately simple: no NLP/NER — same convention and same tradeoffs as
ingestion/news_api/entity_match.py. Duplicated here rather than imported
across the top-level ingestion/ and backend/ingestion/ split; consolidate
into one shared module when the two get reorganized together.
"""

import re

from pydantic import BaseModel


class EntityAlias(BaseModel):
    symbol: str  # e.g. "NVDA" — matches entities.symbol in the db design
    aliases: list[str] = []  # e.g. ["Nvidia", "Nvidia Corporation"]


class EntityMatcher:
    def __init__(self, entities: list[EntityAlias]):
        """Pre-builds the lookup once per batch of entities,
        rather than re-scanning the entity list per tweet.
        """
        self._patterns: dict[str, list[re.Pattern]] = {}
        for entity in entities:
            self._patterns[entity.symbol] = [
                _term_pattern(entity.symbol, is_symbol=True),
                *(_term_pattern(alias) for alias in entity.aliases),
            ]

    def match(self, text: str) -> list[str]:
        """Word-boundary match of each tracked entity's symbol + aliases
        against the tweet text. Aliases match in any case; a ticker symbol
        matches only in capitals (see _term_pattern). Returns matched symbols,
        deduped, in the order entities were passed to __init__.
        """
        matched: list[str] = []
        for symbol, patterns in self._patterns.items():
            if any(pattern.search(text) for pattern in patterns):
                matched.append(symbol)
        return matched


def _term_pattern(term: str, is_symbol: bool = False) -> re.Pattern:
    """Same rule as backend/entities/matcher.py: a ticker ("A", "ON") must
    appear in capitals; non-ticker symbols and aliases match in any case.
    """
    is_ticker = is_symbol and term.isupper() and " " not in term
    return re.compile(rf"\b{re.escape(term)}\b", 0 if is_ticker else re.IGNORECASE)

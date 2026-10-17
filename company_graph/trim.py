"""Cut a long SEC filing down to the passages worth sending to the extractor.

Pure functions: plain text in, chunks out. No network, no model calls, no I/O.

Each hit (a phrase or a company name) opens a window of `radius` characters on
both sides. Windows that overlap or touch are merged into one chunk. At most
MAX_CHUNKS chunks are kept: the ones with the most hits (ties go to the earlier
chunk), returned in document order. Matching ignores case.
"""

import re
from collections.abc import Iterable
from typing import NamedTuple

DEFAULT_RADIUS = 1500
MAX_CHUNKS = 12

DEFAULT_PHRASES = (
    "accounted for",
    "% of revenue",
    "% of net sales",
    "sole source",
    "single source",
    "supplier",
    "competitors",
    "compete with",
    "agreement with",
)


class Chunk(NamedTuple):
    offset: int  # character offset of text[0] in the original filing
    text: str


def trim_by_phrases(
    text: str,
    phrases: Iterable[str] = DEFAULT_PHRASES,
    radius: int = DEFAULT_RADIUS,
) -> list[Chunk]:
    """Chunks around every occurrence of any phrase (plain substring match)."""
    pattern = _alternation(phrases, whole_word=False)
    return _trim(text, pattern, radius)


def trim_by_name(
    text: str,
    name_variants: Iterable[str],
    radius: int = DEFAULT_RADIUS,
) -> list[Chunk]:
    """Chunks around every mention of the company, matched as whole words.

    Whole-word matching keeps "Apple" from hitting "Pineapple".
    """
    pattern = _alternation(name_variants, whole_word=True)
    return _trim(text, pattern, radius)


def _alternation(terms: Iterable[str], whole_word: bool) -> re.Pattern | None:
    # Longest first so the regex prefers "Acme Corporation" over "Acme" at the same spot.
    unique = sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    if not unique:
        return None
    body = "|".join(re.escape(t) for t in unique)
    if whole_word:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    return re.compile(body, re.IGNORECASE)


def _trim(text: str, pattern: re.Pattern | None, radius: int) -> list[Chunk]:
    if pattern is None or not text:
        return []
    radius = max(0, radius)
    n = len(text)

    # Merge while scanning: hits arrive in document order, so windows do too.
    spans: list[list[int]] = []  # [start, end, hit_count]
    for m in pattern.finditer(text):
        start, end = max(0, m.start() - radius), min(n, m.end() + radius)
        if spans and start <= spans[-1][1]:
            last = spans[-1]
            last[1] = max(last[1], end)
            last[2] += 1
        else:
            spans.append([start, end, 1])

    if len(spans) > MAX_CHUNKS:
        ranked = sorted(range(len(spans)), key=lambda i: (-spans[i][2], i))
        spans = [spans[i] for i in sorted(ranked[:MAX_CHUNKS])]

    return [Chunk(start, text[start:end]) for start, end, _ in spans]

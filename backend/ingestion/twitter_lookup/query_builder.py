"""Builds the OR'd keyword/topic query string for GET /2/tweets/search/recent
from a market's title/event_title/tags.

See new_specs/ingestion/twitter-lookup.md's Resolved open questions: exact
phrase + hashtag + cashtag combined via OR, e.g.
`"strait of hormuz" OR #hormuz OR $OIL`, trimmed to fit X's query character
limit rather than picking one strategy.
"""

MAX_QUERY_CHARS = 512


def build_keyword_query(
    phrase: str | None = None,
    hashtags: list[str] | None = None,
    cashtags: list[str] | None = None,
) -> str:
    """Combine an exact phrase, hashtags, and cashtags into one OR'd query.

    Raises ValueError if nothing is given — an empty query is always a
    caller bug, not valid input.
    """
    parts: list[str] = []
    if phrase:
        parts.append(f'"{phrase}"')
    parts.extend(f"#{tag.lstrip('#')}" for tag in (hashtags or []))
    parts.extend(f"${tag.lstrip('$').upper()}" for tag in (cashtags or []))

    if not parts:
        raise ValueError("At least one of phrase, hashtags, or cashtags must be given")

    query = " OR ".join(parts)
    if len(query) <= MAX_QUERY_CHARS:
        return query

    # Too long: drop trailing OR terms (phrase stays first) until it fits,
    # rather than truncating mid-term.
    while parts and len(" OR ".join(parts)) > MAX_QUERY_CHARS:
        parts.pop()
    if not parts:
        raise ValueError(f"phrase alone exceeds the {MAX_QUERY_CHARS}-char query limit")
    return " OR ".join(parts)

"""Translates a NewsQueryFilters into the kwargs newsapi-python's
NewsApiClient.get_everything() expects.
"""

from typing import Any

from .models import NewsQueryFilters


def build_query_string(filters: NewsQueryFilters) -> str:
    """Combine keyword_query / exact_phrases / boolean_terms into the single
    `q` string /v2/everything expects.

    - keyword_query is used as-is (bare terms, implicitly OR'd by NewsAPI).
    - each exact_phrases entry is wrapped in double quotes.
    - boolean_terms is a raw AND/OR/NOT(...) expression, supplied by the
      caller in NewsAPI's own syntax.

    If more than one part is set, they're joined with AND — phrases and
    boolean terms are meant to narrow a plain keyword search, not widen it.

    Raises ValueError if all three are empty: an empty q is almost always a
    caller bug, not valid input, when no sources/domains are set either.
    """
    parts: list[str] = []

    if filters.keyword_query:
        parts.append(filters.keyword_query)
    if filters.exact_phrases:
        parts.extend(f'"{phrase}"' for phrase in filters.exact_phrases)
    if filters.boolean_terms:
        parts.append(filters.boolean_terms)

    if not parts:
        raise ValueError(
            "At least one of keyword_query, exact_phrases, or boolean_terms "
            "must be set to build a query string"
        )

    return " AND ".join(parts)


def build_request_kwargs(filters: NewsQueryFilters) -> dict[str, Any]:
    """Map a NewsQueryFilters to NewsApiClient.get_everything() kwargs.

    Omits keys whose value is None rather than passing None through — the
    client library treats an explicit None differently from an omitted
    kwarg for some params.
    """
    kwargs: dict[str, Any] = {
        "q": build_query_string(filters),
        "language": filters.language,
        "sort_by": filters.sort_by.value,
        "page_size": filters.page_size,
        "page": filters.page,
    }

    if filters.source_ids:
        kwargs["sources"] = ",".join(filters.source_ids)
    if filters.domains:
        kwargs["domains"] = ",".join(filters.domains)
    if filters.exclude_domains:
        kwargs["exclude_domains"] = ",".join(filters.exclude_domains)
    if filters.search_in:
        kwargs["search_in"] = ",".join(field.value for field in filters.search_in)
    if filters.from_time:
        kwargs["from_param"] = filters.from_time
    if filters.to_time:
        kwargs["to"] = filters.to_time

    return kwargs

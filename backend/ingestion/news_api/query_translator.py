"""Translates a free-text user query into NewsQueryFilters, via the shared
LLM client. This is the only file in the codebase that knows how to
phrase NewsQueryFilters' fields as an LLM prompt — llm/client.py stays
generic, and a Kalshi/Twitter equivalent would get its own
query_translator.py here rather than extending this one, since their
filter shapes aren't full-text search at all.

This step is upstream of everything else in this package: it only
produces a NewsQueryFilters object, the same thing you'd construct by
hand. It doesn't call NewsAPI and doesn't touch entity matching.
"""

from datetime import datetime, timezone

from backend.llm.client import complete_structured

from .models import NewsQueryFilters

_SYSTEM_PROMPT_TEMPLATE = """You translate a user's free-text news request into search parameters for NewsAPI's /v2/everything endpoint.

Today's date (UTC) is {today}. Resolve relative dates ("last week", "past month") into absolute from_time/to_time values (ISO 8601).

Field guidance:
- keyword_query: bare search terms (NewsAPI effectively ORs these together). Use this for the general topic/subject.
- exact_phrases: only phrases the user wants matched exactly as written (e.g. a quoted phrase, a specific title).
- boolean_terms: only if the user explicitly describes AND/OR/NOT logic between terms (e.g. "Tesla or Rivian but not recalls"); otherwise leave null.
- language: ISO 639-1 code; default "en" unless the user names another language.
- domains / exclude_domains: only if the user names specific outlets/sites by name.
- sort_by: "relevancy" by default; "publishedAt" if the user asks for "latest"/"newest"/"most recent"; "popularity" if they ask for "most popular"/"most read".
- page_size: default 20 unless the user asks for a specific number (max 100, since NewsAPI caps it there).

A person or company name the user mentions should go into keyword_query or exact_phrases — it is NOT a separate field here, since this schema only controls what NewsAPI searches for, not how results get tagged afterward.

Leave every field you have no basis for at its default/empty value. Do not invent domains, dates, or boolean logic the user didn't ask for."""


def build_filters_from_query(user_query: str) -> NewsQueryFilters:
    """Free-text user query -> NewsQueryFilters, via one LLM call.

    Raises llm.client.LlmError if the call fails, or if the model's output
    doesn't validate as NewsQueryFilters (e.g. all query fields empty,
    which NewsQueryFilters doesn't itself reject at construction — that
    check lives in query_builder.build_query_string, raised later when
    the filters are actually used).
    """
    today = datetime.now(timezone.utc).date().isoformat()
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(today=today)
    return complete_structured(system_prompt, user_query, NewsQueryFilters)

"""Thin gateway over newsapi-python's NewsApiClient.

Translates its NewsAPIException into our own NewsApiError so callers never
need to import newsapi-python's exception type directly — if the provider
is ever swapped out, only this file and query_builder.py should need to
change.
"""

from newsapi import NewsApiClient
from newsapi.newsapi_exception import NewsAPIException

from .models import NewsApiError, NewsQueryFilters
from .query_builder import build_request_kwargs

# NewsAPI's documented error codes -> whether a future scheduler should
# retry. Unmapped/unknown codes fail closed (not retryable) rather than
# risk retrying something we don't understand.
# https://newsapi.org/docs/errors
_RETRYABLE_CODES = {"rateLimited"}


class NewsApiGateway:
    def __init__(self, api_key: str, http_client: NewsApiClient | None = None):
        """http_client is injectable for tests (pass a fake); defaults to a
        real NewsApiClient(api_key=api_key) otherwise.
        """
        self._client = http_client or NewsApiClient(api_key=api_key)

    def fetch_raw(self, filters: NewsQueryFilters) -> list[dict]:
        """Build kwargs via query_builder, call get_everything, return the
        raw `articles` list. Raises NewsApiError on any non-"ok" status —
        never returns a partial/error response to the caller silently.
        """
        kwargs = build_request_kwargs(filters)
        try:
            response = self._client.get_everything(**kwargs)
        except NewsAPIException as exc:
            raise self._translate_exception(exc) from exc

        if response.get("status") != "ok":
            raise self._translate_exception(
                NewsAPIException(
                    {
                        "code": response.get("code", "unknown"),
                        "message": response.get("message", "unknown error"),
                    }
                )
            )

        return response.get("articles", [])

    @staticmethod
    def _translate_exception(exc: NewsAPIException) -> NewsApiError:
        code = exc.get_code() if hasattr(exc, "get_code") else "unknown"
        message = exc.get_message() if hasattr(exc, "get_message") else str(exc)
        return NewsApiError(
            code=code,
            message=message,
            retryable=code in _RETRYABLE_CODES,
        )

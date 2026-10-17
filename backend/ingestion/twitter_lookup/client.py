"""Thin gateway over the X (Twitter) API v2 REST endpoints used for
on-demand lookups: GET /2/users/:id/tweets (account backfill) and
GET /2/tweets/search/recent (keyword/topic search).

Translates non-2xx responses into our own TwitterApiError so callers never
need to know about httpx's exception types directly — if the HTTP client
is ever swapped out, only this file should need to change.
"""

from datetime import datetime

import httpx

from .models import TwitterApiError

BASE_URL = "https://api.x.com/2"
TWEET_FIELDS = "created_at,public_metrics,author_id"
USER_FIELDS = "username"
EXPANSIONS = "author_id"
PAGE_SIZE = 100
MAX_PAGES = 32  # 32 * 100 = 3,200, matching X's own user-timeline cap

# X's recent/429/5xx errors are worth a retry upstream; anything else
# (bad query, bad auth, not found) is a caller bug or a dead credential.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TwitterApiGateway:
    def __init__(self, bearer_token: str, http_client: httpx.Client | None = None):
        """http_client is injectable for tests (pass a fake implementing
        .get(path, params=...) -> a fake response with .status_code/.json());
        defaults to a real httpx.Client otherwise.
        """
        self._client = http_client or httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=15,
        )

    def fetch_user_timeline(self, username: str, start: datetime, end: datetime) -> list[dict]:
        """One account's tweets in [start, end], following pagination
        (`meta.next_token`) up to X's own ~3,200-tweet user-timeline cap
        (`MAX_PAGES` pages of `PAGE_SIZE` each) — see
        new_specs/ingestion/twitter-lookup.md Resolved open questions.
        """
        user_id = self._resolve_user_id(username)
        return self._paginate(
            f"/users/{user_id}/tweets",
            self._params(start, end),
            token_param="pagination_token",
            fallback_author=username.lstrip("@"),
        )

    def fetch_recent_search(
        self,
        query: str,
        start: datetime,
        end: datetime,
        max_pages: int = MAX_PAGES,
        sort_order: str | None = None,
    ) -> list[dict]:
        """Tweets matching `query` in [start, end], following pagination
        up to `max_pages` pages. X's recent-search only covers roughly
        the last 7 days regardless of how far back `start` is set.
        `sort_order` is X's "recency" (its default) or "relevancy".
        """
        params = {**self._params(start, end), "query": query}
        if sort_order:
            params["sort_order"] = sort_order
        return self._paginate("/tweets/search/recent", params, token_param="next_token", max_pages=max_pages)

    def _paginate(
        self,
        path: str,
        base_params: dict,
        token_param: str,
        fallback_author: str | None = None,
        max_pages: int = MAX_PAGES,
    ) -> list[dict]:
        tweets: list[dict] = []
        next_token = None
        for _ in range(max_pages):
            params = dict(base_params)
            if next_token:
                params[token_param] = next_token
            body = self._raise_for_status(self._client.get(path, params=params))
            tweets += self._tweets_with_authors(body, fallback_author=fallback_author)
            next_token = (body.get("meta") or {}).get("next_token")
            if not next_token:
                break
        return tweets

    def _resolve_user_id(self, username: str) -> str:
        resp = self._client.get(f"/users/by/username/{username.lstrip('@')}")
        body = self._raise_for_status(resp)
        user = body.get("data")
        if not user:
            raise TwitterApiError(
                code="not_found", message=f"No X account found for @{username}", retryable=False
            )
        return user["id"]

    @staticmethod
    def _tweets_with_authors(body: dict, fallback_author: str | None = None) -> list[dict]:
        tweets = body.get("data") or []
        users = {u["id"]: u["username"] for u in (body.get("includes") or {}).get("users", [])}
        for tweet in tweets:
            tweet["_author_username"] = users.get(tweet.get("author_id"), fallback_author)
        return tweets

    @staticmethod
    def _params(start: datetime, end: datetime) -> dict:
        return {
            "start_time": _iso(start),
            "end_time": _iso(end),
            "max_results": PAGE_SIZE,
            "tweet.fields": TWEET_FIELDS,
            "expansions": EXPANSIONS,
            "user.fields": USER_FIELDS,
        }

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            raise TwitterApiError(
                code=str(resp.status_code),
                message=body.get("title") or body.get("detail") or resp.text[:200],
                retryable=resp.status_code in _RETRYABLE_STATUS,
            )
        return resp.json()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

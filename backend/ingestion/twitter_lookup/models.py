"""Data models for the twitter_lookup ingestion module.

Pydantic, not plain dataclasses: this module sits at an external boundary
(X API-supplied JSON), same reasoning as ingestion/news_api/models.py.
"""

from datetime import datetime

from pydantic import BaseModel


class Engagement(BaseModel):
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0


class ContentItem(BaseModel):
    source: str = "twitter"
    id: str  # the tweet id
    author: str | None = None  # the posting account's handle, no leading "@"
    title: str | None = None  # tweets have no headline; always None
    text: str
    entities: list[str] = []
    url: str
    published_at: datetime
    engagement: Engagement = Engagement()


class TwitterApiError(Exception):
    """Raised for any non-2xx X API response.

    `retryable` is a signal for whatever caller calls this layer later —
    this module never retries on its own.
    """

    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{code}] {message}")

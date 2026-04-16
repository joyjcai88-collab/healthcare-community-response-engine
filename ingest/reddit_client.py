"""Reddit ingestion via PRAW."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)


def _client():
    import praw  # imported lazily so tests can run without it

    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "community-capture/0.1"),
    )


def _to_record(submission) -> dict:
    return {
        "platform": "reddit",
        "external_id": f"t3_{submission.id}",
        "subreddit": str(submission.subreddit),
        "author": str(submission.author) if submission.author else None,
        "title": submission.title,
        "text": submission.selftext or "",
        "permalink": f"https://www.reddit.com{submission.permalink}",
        "created_utc": datetime.fromtimestamp(
            submission.created_utc, tz=timezone.utc
        ).isoformat(),
    }


def pull_recent(
    subreddits: List[str],
    lookback_hours: int = 6,
    per_sub_limit: int = 50,
) -> Iterator[dict]:
    """Yield post records from `new` listings of each subreddit within the lookback window."""
    cutoff = datetime.now(tz=timezone.utc).timestamp() - lookback_hours * 3600
    reddit = _client()
    for name in subreddits:
        sub = reddit.subreddit(name.strip())
        try:
            for submission in sub.new(limit=per_sub_limit):
                if submission.created_utc < cutoff:
                    continue
                if submission.stickied:
                    continue
                yield _to_record(submission)
        except Exception as exc:
            logger.warning("Failed pulling r/%s: %s", name, exc)

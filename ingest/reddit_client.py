"""Reddit ingestion via the public unauthenticated JSON endpoint.

Reddit exposes every subreddit listing as JSON at
    https://www.reddit.com/r/<subreddit>/new.json

No OAuth credentials required — just a non-default User-Agent (Reddit blocks
generic UAs like `python-requests/x.x`). Subject to Reddit's rate limits
(roughly 60 req/min from a single IP); fine for low-volume ingestion.

If Reddit ever blocks unauth access, swap this back to PRAW with real creds.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Iterator, List

import httpx

logger = logging.getLogger(__name__)

DEFAULT_UA = "community-capture/0.1 (read-only research; +https://community-capture.vercel.app)"
TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def _ua() -> str:
    return os.environ.get("REDDIT_USER_AGENT") or DEFAULT_UA


def _to_record(child: dict) -> dict:
    data = child.get("data", {})
    created = data.get("created_utc", 0)
    return {
        "platform": "reddit",
        "external_id": f"t3_{data.get('id')}",
        "subreddit": data.get("subreddit"),
        "author": data.get("author"),
        "title": data.get("title", ""),
        "text": data.get("selftext", "") or "",
        "permalink": f"https://www.reddit.com{data.get('permalink', '')}",
        "created_utc": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
    }


def pull_recent(
    subreddits: List[str],
    lookback_hours: int = 6,
    per_sub_limit: int = 50,
) -> Iterator[dict]:
    """Yield post records from each subreddit's /new listing within lookback window."""
    cutoff = time.time() - lookback_hours * 3600
    headers = {"User-Agent": _ua()}
    with httpx.Client(headers=headers, timeout=TIMEOUT, follow_redirects=True) as client:
        for raw in subreddits:
            name = raw.strip().lstrip("r/").strip("/")
            if not name:
                continue
            url = f"https://www.reddit.com/r/{name}/new.json?limit={per_sub_limit}"
            try:
                r = client.get(url)
                if r.status_code == 429:
                    logger.warning("Rate limited on r/%s — backing off 5s", name)
                    time.sleep(5)
                    continue
                r.raise_for_status()
                payload = r.json()
            except Exception as exc:
                logger.warning("Failed pulling r/%s: %s", name, exc)
                continue

            children = payload.get("data", {}).get("children", [])
            for child in children:
                data = child.get("data", {})
                if data.get("stickied"):
                    continue
                if data.get("created_utc", 0) < cutoff:
                    continue
                yield _to_record(child)
            # Light throttle between subreddits to stay under the 60/min limit
            time.sleep(1)

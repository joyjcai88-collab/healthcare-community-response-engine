"""CLI runner: pull posts (real Reddit or mock), classify, draft, store."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Iterable, List

# Make project root importable when invoked as `python -m ingest.runner`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(override=True)

from database import init_db, insert_draft, upsert_post  # noqa: E402
from ingest import mock as mock_source  # noqa: E402
from ingest.classifier import classify, keyword_match  # noqa: E402
from responder.generator import generate  # noqa: E402
from responder.safety import check  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest.runner")


def ingest_records(
    records: Iterable[dict],
    draft: bool = True,
    require_keywords: bool = True,
) -> dict:
    """Shared pipeline: classify → store → draft → safety-check.

    Returns a summary dict. Used by both the CLI and the /api/ingest/mock
    endpoint so they behave identically.
    """
    init_db()
    seen = kept = drafts = 0
    for record in records:
        seen += 1
        body = f"{record.get('title','')}\n\n{record.get('text','')}"
        if require_keywords and not keyword_match(body):
            continue

        scoring = classify(record.get("text", ""), record.get("title", ""))
        record.update(scoring)

        post_id = upsert_post(record)
        if post_id is None:
            continue
        kept += 1
        logger.info(
            "Stored post id=%s urgency=%.2f topic=%s sub=%s",
            post_id, scoring["urgency_score"], scoring["topic"], record.get("subreddit"),
        )

        if draft:
            try:
                gen = generate(
                    title=record.get("title", ""),
                    text=record.get("text", ""),
                    urgency_score=scoring["urgency_score"],
                    topic=scoring["topic"],
                )
                safety = check(gen["draft_text"])
                insert_draft(
                    post_id=post_id,
                    draft_text=gen["draft_text"],
                    model=gen["model"],
                    prompt_version=gen["prompt_version"],
                    safety_passed=safety["passed"],
                    safety_violations=safety["violations"],
                )
                drafts += 1
            except Exception as exc:
                logger.warning("Draft generation failed for post %s: %s", post_id, exc)

    return {"seen": seen, "kept": kept, "drafts": drafts}


def run_reddit(draft: bool = True) -> dict:
    from ingest import reddit_client

    subreddits = [
        s.strip()
        for s in os.environ.get(
            "SUBREDDITS", "Parenting,NewParents,beyondthebump"
        ).split(",")
        if s.strip()
    ]
    lookback = int(os.environ.get("INGEST_LOOKBACK_HOURS", "6"))
    return ingest_records(
        reddit_client.pull_recent(subreddits, lookback_hours=lookback),
        draft=draft,
    )


def run_mock(n: int = 3, draft: bool = True) -> dict:
    return ingest_records(mock_source.pull_n(n), draft=draft, require_keywords=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true",
                        help="Use the built-in mock post source (no Reddit API needed).")
    parser.add_argument("--n", type=int, default=3,
                        help="How many mock posts to pull per run (only with --mock).")
    parser.add_argument("--no-draft", action="store_true",
                        help="Skip LLM draft generation.")
    args = parser.parse_args()

    if args.mock:
        summary = run_mock(n=args.n, draft=not args.no_draft)
    else:
        summary = run_reddit(draft=not args.no_draft)
    logger.info("Done. %s", summary)


if __name__ == "__main__":
    main()

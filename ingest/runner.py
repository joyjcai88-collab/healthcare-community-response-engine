"""CLI runner: pull recent Reddit posts, classify, and store qualifying rows."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Make project root importable when invoked as `python -m ingest.runner`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(override=True)

from database import init_db, upsert_post  # noqa: E402
from ingest import reddit_client  # noqa: E402
from ingest.classifier import classify, keyword_match  # noqa: E402
from responder.generator import generate  # noqa: E402
from responder.safety import check  # noqa: E402
from database import insert_draft  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest.runner")


def run(once: bool = True, draft: bool = True) -> int:
    init_db()

    subreddits = [
        s.strip()
        for s in os.environ.get(
            "SUBREDDITS", "Parenting,NewParents,beyondthebump"
        ).split(",")
        if s.strip()
    ]
    lookback = int(os.environ.get("INGEST_LOOKBACK_HOURS", "6"))

    n_seen = n_kept = n_drafts = 0
    for record in reddit_client.pull_recent(subreddits, lookback_hours=lookback):
        n_seen += 1
        body = f"{record.get('title','')}\n\n{record.get('text','')}"
        if not keyword_match(body):
            continue

        scoring = classify(record.get("text", ""), record.get("title", ""))
        record.update(scoring)

        post_id = upsert_post(record)
        if post_id is None:
            continue
        n_kept += 1
        logger.info(
            "Stored post id=%s urgency=%.2f topic=%s sub=%s",
            post_id, scoring["urgency_score"], scoring["topic"], record["subreddit"],
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
                n_drafts += 1
            except Exception as exc:
                logger.warning("Draft generation failed for post %s: %s", post_id, exc)

    logger.info("Done. seen=%d kept=%d drafts=%d", n_seen, n_kept, n_drafts)
    return n_kept


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", default=True)
    parser.add_argument(
        "--no-draft",
        action="store_true",
        help="Skip LLM draft generation (just ingest + classify).",
    )
    args = parser.parse_args()
    run(once=args.once, draft=not args.no_draft)


if __name__ == "__main__":
    main()

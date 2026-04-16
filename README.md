# Community Capture

Semi-automated AI response engine that intercepts parenting anxiety moments on
Reddit, drafts empathetic non-diagnostic replies with Claude, lets a human
reviewer approve them, and tracks the funnel from community post → click →
signup → paid Summer Health consult.

## Architecture

```
Reddit (PRAW)            Anthropic Claude
      │                         │
      ▼                         ▼
  ingest.runner ─► SQLite ◄─ responder.generator
                     │
                     ▼
               server.py (FastAPI)
                     │
                     ▼
        dashboard.html (Alpine + Tailwind)
                     │
                     ▼
        landing.html → /api/track/signup
                     │
                     ▼
                  PostHog
```

## Setup

```bash
cp .env.example .env       # fill in Reddit + Anthropic keys
pip install -r requirements.txt
python -c "from database import init_db; init_db()"
```

### Required env vars

| var | purpose |
| --- | --- |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Reddit script app credentials |
| `ANTHROPIC_API_KEY` | Claude (Haiku for classification, Opus for high-urgency drafts) |
| `POSTHOG_API_KEY`, `POSTHOG_HOST` | (optional) frontend analytics on the landing page |
| `LANDING_BASE_URL` | base URL used in tracking links (default `http://localhost:8000`) |
| `SUBREDDITS` | comma-separated subs to monitor |
| `INGEST_LOOKBACK_HOURS` | how far back each ingest pass scans |

### Reddit credentials

Create a "script" app at https://www.reddit.com/prefs/apps — that gives you
client id + secret. Set the user agent string to something like
`community-capture/0.1 by yourname`.

## Run

Two long-lived processes:

```bash
# 1. The dashboard / API
uvicorn server:app --reload --port 8000

# 2. Periodic ingest (run on demand or via cron / launchd)
python -m ingest.runner
```

To pull posts every 15 minutes via cron:

```cron
*/15 * * * * cd /path/to/community-capture && /path/to/python -m ingest.runner >> ingest.log 2>&1
```

## End-to-end smoke test

1. `cp .env.example .env` and fill Reddit + Anthropic keys.
2. `pip install -r requirements.txt`
3. `python -c "from database import init_db; init_db()"`
4. `python -m ingest.runner` — confirm ≥1 row in `posts`.
5. `uvicorn server:app --reload` and open <http://localhost:8000>.
6. Confirm the dashboard shows ingested posts with Claude-drafted responses
   and a `safety: pass` badge.
7. Click **Approve & Copy** — the response (with embedded
   `localhost:8000/go/...` link) is on your clipboard.
8. Open the link in a private tab — it lands on `/landing`, writes a `clicks`
   row, and fires a PostHog event (if `POSTHOG_API_KEY` is set).
9. Submit the email form — writes a `conversions` row; the dashboard header
   counter increments on next refresh.
10. `pytest tests/` passes.

## PRD compliance check

- Sample 5 generated drafts and confirm they each:
  - open with empathy,
  - contain no diagnostic language,
  - include exactly one soft Summer Health CTA,
  - run ≤4 sentences.
- Drop a planted line containing `you have an infection` into a draft → the
  safety filter must flag it (`tests/test_safety.py::test_planted_you_have_string_flagged`
  covers this).

## Roadmap (deferred from MVP)

- Auto-posting via Reddit API (ban risk; needs a warm account + rate limiting).
- Facebook group ingestion (no public API; needs browser automation + ToS review).
- TikTok comment monitoring (requires creator partnerships).
- Stripe webhook for true paid-conversion attribution.
- Multi-reviewer auth + audit log.
- LLM-based medical safety eval (the regex filter is a placeholder).

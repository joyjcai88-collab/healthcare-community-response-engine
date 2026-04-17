# Community Capture

Semi-automated AI response engine that intercepts parenting anxiety moments on
Reddit, drafts empathetic non-diagnostic replies with Claude, lets a human
reviewer approve them, and tracks the funnel from community post → click →
signup → paid Summer Health consult.

**Live dashboard:** https://community-capture.vercel.app/
**Repo:** https://github.com/joyjcai88-collab/healthcare-community-response-engine

## Architecture

```
┌─────────────────────┐
│  GitHub Actions     │  hourly cron, runs on GitHub IPs
│  (ingest.yml)       │  (not blocked by Reddit like AWS is)
└──────────┬──────────┘
           │  python -m ingest.runner
           ▼
   reddit.com/new.json ──► keyword filter ──► Claude (urgency + draft)
           │
           ▼
      ┌─────────┐
      │Postgres │  ◄── Vercel dashboard reads/writes the same DB
      └────┬────┘
           ▼
      server.py (FastAPI on Vercel)
           │
           ▼
    Alpine + Tailwind dashboard (Reddit-styled)
           │
           ▼
     /landing → /api/track/signup → conversions
```

## Local dev

```bash
cp .env.example .env        # fill ANTHROPIC_API_KEY at minimum
pip install -r requirements.txt
python -c "from database import init_db; init_db()"
uvicorn server:app --reload --port 8000
```

No `DATABASE_URL` → uses local SQLite (`community_capture.db`).
With `DATABASE_URL=postgres://…` → uses Postgres.

### Required env vars (local)

| var | purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude (Haiku for urgency, Opus for high-urgency drafts) |
| `DATABASE_URL` | *(optional)* Postgres connection string; falls back to SQLite |
| `LANDING_BASE_URL` | base URL for tracking links (default `http://localhost:8000`) |
| `REDDIT_USER_AGENT` | *(optional)* identifying UA string for the Reddit JSON endpoint |
| `SUBREDDITS` | *(optional)* comma-separated subs to monitor |
| `INGEST_LOOKBACK_HOURS` | *(optional)* how far back each pass scans, default `6` |
| `POSTHOG_API_KEY`, `POSTHOG_HOST` | *(optional)* landing-page analytics |

## Production setup

### 1. Create a Postgres

Any provider works. Easiest free options:
- **Neon** — https://console.neon.tech (add via Vercel integration marketplace for one-click linking)
- **Supabase** — https://supabase.com/dashboard
- **Vercel Postgres** — now powered by Neon under the hood

Copy the `postgres://…` connection string.

### 2. Vercel env vars (dashboard)

Set these in https://vercel.com/*/community-capture/settings/environment-variables:

| var | value |
| --- | --- |
| `ANTHROPIC_API_KEY` | your Claude key |
| `DATABASE_URL` | the Postgres URL from step 1 |
| `LANDING_BASE_URL` | `https://community-capture.vercel.app` |

Redeploy after adding (Vercel doesn't hot-reload env vars).

### 3. GitHub Actions secrets (hourly Reddit ingest)

`https://github.com/<owner>/<repo>/settings/secrets/actions`:

| secret | value |
| --- | --- |
| `DATABASE_URL` | **same** Postgres URL as Vercel |
| `ANTHROPIC_API_KEY` | same Claude key |

Optional repo variables (Settings → Variables → Actions) to override defaults:

| variable | default |
| --- | --- |
| `SUBREDDITS` | `Parenting,NewParents,beyondthebump` |
| `INGEST_LOOKBACK_HOURS` | `1` |
| `REDDIT_USER_AGENT` | `community-capture/0.1 (read-only research; +https://community-capture.vercel.app)` |

### 4. Trigger the first ingest

Either wait for the top of the next hour, or go to the Actions tab and click
**Reddit Ingest → Run workflow** for an immediate run. Within a minute the
Vercel dashboard header should show fresh `Posts` and `Pending` counts.

## Why GitHub Actions instead of Vercel Cron?

Reddit's public JSON endpoint **403s every request from AWS IP ranges**,
which is where Vercel's serverless functions run. GitHub's runner IP pool
isn't on that blocklist, so ingestion works from there. If Reddit starts
blocking GitHub too, fallbacks in order of effort:
- run `python -m ingest.runner` on a laptop/Raspberry Pi/cheap VPS,
- pay for a residential proxy and set `HTTP_PROXY` in the GitHub Action,
- actually obtain Reddit API credentials and swap `ingest/reddit_client.py`
  back to a PRAW-based implementation.

## End-to-end smoke test

1. `cp .env.example .env` and fill `ANTHROPIC_API_KEY`.
2. `pip install -r requirements.txt`
3. `python -c "from database import init_db; init_db()"`
4. `python -m ingest.runner` *(real Reddit)* or click **+ Seed demo post** in
   the dashboard after `uvicorn server:app --reload`.
5. Confirm the dashboard shows ingested posts with Claude-drafted responses
   and `safety: pass` badges.
6. Click **Approve & Copy** — tracked URL lands on your clipboard.
7. Open the link in a private tab → writes a `clicks` row.
8. Submit the landing email form → writes a `conversions` row.
9. `pytest tests/` passes.

## PRD compliance check

- Sample 5 generated drafts and confirm they each:
  - open with empathy,
  - contain no diagnostic language,
  - include exactly one soft Summer Health CTA,
  - run ≤4 sentences.
- Plant `you have an infection` in a draft → safety filter flags it
  (`tests/test_safety.py::test_planted_you_have_string_flagged`).

## Roadmap

- Auto-posting to Reddit (needs warm account + rate limiting).
- Facebook group + TikTok ingestion (no public APIs; browser automation).
- Stripe webhook for paid-conversion attribution (`/api/track/conversion`
  endpoint is already a stub).
- Multi-reviewer auth + audit log.
- LLM-based medical safety eval replacing the regex heuristic.

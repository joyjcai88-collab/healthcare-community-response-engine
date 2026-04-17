"""FastAPI app: review queue, tracking redirects, landing page, conversion endpoints."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

load_dotenv(override=True)

import database as db
from ingest.runner import run_mock
from responder.generator import generate
from responder.safety import check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

ROOT = Path(__file__).parent
app = FastAPI(title="Community Capture")

LANDING_BASE_URL = os.environ.get("LANDING_BASE_URL", "http://localhost:8000")

# Load templates as strings at module load time so we don't depend on
# runtime filesystem access (Vercel's bundler is finicky about non-.py files).
_TEMPLATES_DIR = ROOT / "templates"
DASHBOARD_HTML = (_TEMPLATES_DIR / "dashboard.html").read_text()
LANDING_HTML_RAW = (_TEMPLATES_DIR / "landing.html").read_text()


def _render_landing() -> str:
    posthog_key = os.environ.get("POSTHOG_API_KEY", "")
    posthog_host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
    snippet = ""
    if posthog_key:
        snippet = (
            "<script>"
            "!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(\".\");2==o.length&&(t=t[o[0]],e=o[1]);t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement(\"script\")).type=\"text/javascript\",p.crossOrigin=\"anonymous\",p.async=!0,p.src=s.api_host.replace(\".i.posthog.com\",\"-assets.i.posthog.com\")+\"/static/array.js\",(r=t.getElementsByTagName(\"script\")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a=\"posthog\",u.people=u.people||[],u.toString=function(t){var e=\"posthog\";return\"posthog\"!==a&&(e+=\".\"+a),t||(e+=\" (stub)\"),e},u.people.toString=function(){return u.toString(1)+\".people (stub)\"},o=\"init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug\".split(\" \"),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);"
            f"posthog.init('{posthog_key}', {{api_host:'{posthog_host}'}});"
            "</script>"
        )
    # Strip the Jinja {% if %} block from the source and inject our snippet
    # in its place. The raw template still has the block for local dev with
    # uvicorn — but the deployed path uses this rendered string.
    import re as _re
    return _re.sub(
        r"{%\s*if posthog_key\s*%}.*?{%\s*endif\s*%}",
        snippet,
        LANDING_HTML_RAW,
        flags=_re.DOTALL,
    )


LANDING_HTML = _render_landing()


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# ---------- Dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(content=DASHBOARD_HTML)


# ---------- API: queue + actions ----------

@app.get("/api/queue")
def api_queue(status: str = "pending", limit: int = 100) -> JSONResponse:
    return JSONResponse({"items": db.list_queue(status=status, limit=limit)})


@app.get("/api/metrics")
def api_metrics() -> JSONResponse:
    return JSONResponse(db.funnel_metrics())


class MockIngestBody(BaseModel):
    n: int = Field(default=1, ge=1, le=5)
    draft: bool = True


@app.post("/api/ingest/mock")
def api_ingest_mock(body: MockIngestBody) -> JSONResponse:
    """Seed 1-5 synthetic parenting posts (with Claude drafts).

    Useful for demos / pitches when you don't have Reddit creds, or to
    generate fresh content on-demand during a live walkthrough.
    """
    try:
        summary = run_mock(n=body.n, draft=body.draft)
    except Exception as exc:
        raise HTTPException(500, f"mock ingest failed: {exc}")
    return JSONResponse(summary)


class ApproveBody(BaseModel):
    reviewer_note: Optional[str] = None


@app.post("/api/drafts/{draft_id}/approve")
def api_approve(draft_id: int, body: ApproveBody) -> JSONResponse:
    draft = db.get_draft(draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")

    tracking_id = db.create_tracking_link(
        draft_id=draft_id,
        dest_url=f"{LANDING_BASE_URL}/landing",
        utm_source=draft.get("platform") or "reddit",
        utm_medium="community",
        utm_campaign="community-capture",
        utm_content=str(draft.get("subreddit") or ""),
    )
    db.update_draft_status(draft_id, "approved", reviewer_note=body.reviewer_note)

    tracked_url = f"{LANDING_BASE_URL}/go/{tracking_id}"
    final_text = _inject_tracking(draft["draft_text"], tracked_url)
    return JSONResponse(
        {
            "draft_id": draft_id,
            "tracking_id": tracking_id,
            "tracked_url": tracked_url,
            "final_text": final_text,
        }
    )


class RejectBody(BaseModel):
    reviewer_note: Optional[str] = None


@app.post("/api/drafts/{draft_id}/reject")
def api_reject(draft_id: int, body: RejectBody) -> JSONResponse:
    if not db.get_draft(draft_id):
        raise HTTPException(404, "draft not found")
    db.update_draft_status(draft_id, "rejected", reviewer_note=body.reviewer_note)
    return JSONResponse({"ok": True})


class RegenerateBody(BaseModel):
    steering_note: Optional[str] = None


@app.post("/api/drafts/{draft_id}/regenerate")
def api_regenerate(draft_id: int, body: RegenerateBody) -> JSONResponse:
    draft = db.get_draft(draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    try:
        gen = generate(
            title=draft.get("title") or "",
            text=draft.get("text") or "",
            urgency_score=0.5,
            steering_note=body.steering_note,
        )
    except Exception as exc:
        raise HTTPException(500, f"generator error: {exc}")
    safety = check(gen["draft_text"])
    db.replace_draft_text(
        draft_id=draft_id,
        draft_text=gen["draft_text"],
        model=gen["model"],
        prompt_version=gen["prompt_version"],
        safety_passed=safety["passed"],
        safety_violations=safety["violations"],
    )
    return JSONResponse(
        {
            "draft_id": draft_id,
            "draft_text": gen["draft_text"],
            "model": gen["model"],
            "safety_passed": safety["passed"],
            "safety_violations": safety["violations"],
        }
    )


# ---------- Tracking redirect ----------

@app.get("/go/{tracking_id}")
def go(tracking_id: str, request: Request) -> RedirectResponse:
    link = db.get_tracking_link(tracking_id)
    if not link:
        raise HTTPException(404, "unknown tracking id")
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else None
    db.record_click(
        tracking_id=tracking_id,
        ip_hash=ip_hash,
        user_agent=request.headers.get("user-agent"),
    )
    qs = urlencode(
        {
            "utm_source": link["utm_source"] or "",
            "utm_medium": link["utm_medium"] or "",
            "utm_campaign": link["utm_campaign"] or "",
            "utm_content": link["utm_content"] or "",
            "tid": tracking_id,
        }
    )
    return RedirectResponse(f"{link['dest_url']}?{qs}", status_code=302)


# ---------- Landing + conversion ----------

@app.get("/landing", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    return HTMLResponse(content=LANDING_HTML)


class SignupBody(BaseModel):
    email: str = Field(..., min_length=3)
    tracking_id: Optional[str] = None


@app.post("/api/track/signup")
def api_signup(body: SignupBody) -> JSONResponse:
    email_hash = hashlib.sha256(body.email.lower().encode()).hexdigest()[:24]
    db.record_conversion("signup", body.tracking_id, email_hash)
    return JSONResponse({"ok": True})


class ConversionBody(BaseModel):
    tracking_id: Optional[str] = None
    email: Optional[str] = None
    type: str = "paid"


@app.post("/api/track/conversion")
def api_conversion(body: ConversionBody) -> JSONResponse:
    email_hash = (
        hashlib.sha256(body.email.lower().encode()).hexdigest()[:24]
        if body.email
        else None
    )
    db.record_conversion(body.type, body.tracking_id, email_hash)
    return JSONResponse({"ok": True})


# ---------- helpers ----------

def _inject_tracking(text: str, tracked_url: str) -> str:
    """Append the tracked URL after the CTA sentence so reviewers can post as-is."""
    return f"{text}\n\n{tracked_url}"

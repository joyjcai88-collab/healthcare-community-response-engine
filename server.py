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
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

load_dotenv(override=True)

import database as db
from responder.generator import generate
from responder.safety import check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

ROOT = Path(__file__).parent
app = FastAPI(title="Community Capture")
templates = Jinja2Templates(directory=ROOT / "templates")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

LANDING_BASE_URL = os.environ.get("LANDING_BASE_URL", "http://localhost:8000")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# ---------- Dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "landing_base_url": LANDING_BASE_URL,
        },
    )


# ---------- API: queue + actions ----------

@app.get("/api/queue")
def api_queue(status: str = "pending", limit: int = 100) -> JSONResponse:
    return JSONResponse({"items": db.list_queue(status=status, limit=limit)})


@app.get("/api/metrics")
def api_metrics() -> JSONResponse:
    return JSONResponse(db.funnel_metrics())


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
def landing(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "landing.html",
        {
            "request": request,
            "posthog_key": os.environ.get("POSTHOG_API_KEY", ""),
            "posthog_host": os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"),
        },
    )


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

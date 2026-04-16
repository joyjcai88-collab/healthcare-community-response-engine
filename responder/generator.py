"""Draft generation via the Anthropic API."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict

from .prompts import FEW_SHOTS, PROMPT_VERSION, SYSTEM, build_user_prompt, pick_cta

logger = logging.getLogger(__name__)

OPUS_MODEL = "claude-opus-4-6"
HAIKU_MODEL = "claude-haiku-4-5-20251001"


def _pick_model(urgency_score: float) -> str:
    return OPUS_MODEL if urgency_score >= 0.6 else HAIKU_MODEL


def generate(
    title: str,
    text: str,
    urgency_score: float = 0.5,
    topic: str = "other",
    steering_note: str | None = None,
    model: str | None = None,
) -> Dict:
    """Return {draft_text, model, prompt_version, generated_at}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    chosen_model = model or _pick_model(urgency_score)
    cta = pick_cta()

    user_prompt = build_user_prompt(title=title, text=text, cta=cta)
    if steering_note:
        user_prompt += f"\n\nReviewer note for this regeneration:\n{steering_note}"

    messages = list(FEW_SHOTS) + [{"role": "user", "content": user_prompt}]

    msg = client.messages.create(
        model=chosen_model,
        max_tokens=400,
        system=SYSTEM,
        messages=messages,
    )
    draft_text = "".join(
        block.text for block in msg.content if getattr(block, "type", "") == "text"
    ).strip()

    return {
        "draft_text": draft_text,
        "model": chosen_model,
        "prompt_version": PROMPT_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "topic": topic,
    }

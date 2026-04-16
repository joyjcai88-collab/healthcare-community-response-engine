"""Two-stage urgency classifier: cheap keyword pre-filter + Claude scoring."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

KEYWORDS = [
    r"\bfever\b",
    r"\brash\b",
    r"\bnot eating\b",
    r"\bwon'?t eat\b",
    r"\brefus(?:ing|es) (?:to )?eat\b",
    r"\bshould i go to (?:the )?er\b",
    r"\bis this normal\b",
    r"\bhelp\b",
    r"\blethargic\b",
    r"\bvomit",
    r"\bdiarrhea\b",
    r"\bdehydrat",
    r"\btrouble breathing\b",
    r"\bhives\b",
    r"\bcrying inconsolably\b",
    r"\bnot sleeping\b",
    r"\bblood\b",
]
_KEYWORD_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)

TOPICS = [
    "infant_fever",
    "rash",
    "feeding",
    "sleep",
    "respiratory",
    "gi_symptoms",
    "behavior",
    "general_anxiety",
    "other",
]


def keyword_match(text: str) -> bool:
    if not text:
        return False
    return bool(_KEYWORD_RE.search(text))


def _heuristic_urgency(text: str) -> float:
    """Rough fallback when Claude isn't available."""
    t = (text or "").lower()
    score = 0.3
    for hit in ["er", "emergency", "lethargic", "trouble breathing", "blood", "dehydrat"]:
        if hit in t:
            score += 0.2
    if "<3 months" in t or "newborn" in t or "weeks old" in t:
        score += 0.1
    return min(score, 1.0)


_SYSTEM = """You triage parenting posts. Return JSON only.
Fields:
  urgency_score (0-1, float): how acutely the parent needs medical guidance.
  topic (one of: infant_fever, rash, feeding, sleep, respiratory, gi_symptoms, behavior, general_anxiety, other)
  engagement_level (low|med|high): likelihood the parent will engage with a helpful reply.
Be calibrated. Do not diagnose. Do not exaggerate urgency."""


def classify(text: str, title: str = "") -> Dict:
    """Return {urgency_score, topic, engagement_level}. Falls back if no API key."""
    combined = f"{title}\n\n{text}".strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "urgency_score": _heuristic_urgency(combined),
            "topic": "other",
            "engagement_level": "med",
        }

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": combined[:4000]}],
        )
        raw = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        )
        data = _extract_json(raw)
        return {
            "urgency_score": float(data.get("urgency_score", 0.3)),
            "topic": data.get("topic", "other"),
            "engagement_level": data.get("engagement_level", "med"),
        }
    except Exception as exc:
        logger.warning("Classifier fallback (%s)", exc)
        return {
            "urgency_score": _heuristic_urgency(combined),
            "topic": "other",
            "engagement_level": "med",
        }


def _extract_json(raw: str) -> Dict:
    raw = raw.strip()
    # Strip code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).rstrip("`").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}

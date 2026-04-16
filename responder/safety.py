"""Heuristic safety filter for generated drafts.

Flags (does not auto-discard):
  - diagnostic verbs
  - urgency exaggeration
  - multiple CTAs to summer health
  - exclamation marks
  - exceeds length cap
"""

from __future__ import annotations

import re
from typing import Dict, List

DIAGNOSTIC_PATTERNS = [
    r"\byou have\b",
    r"\byour baby has\b",
    r"\byour child has\b",
    r"\bthis is (?:an? )?(?:infection|virus|allergy|disease|condition)\b",
    r"\bdiagnos(?:e|is|ed)\b",
    r"\bsounds like (?:an? )?(?:infection|virus|allergy|sepsis|meningitis)\b",
]

URGENCY_PATTERNS = [
    r"\bemergency\b",
    r"\bdangerous\b",
    r"\bimmediately call\b",
    r"\bcall 911\b",
    r"\blife.threatening\b",
    r"\bcritical\b",
]

# We allow exactly one mention of Summer Health (the CTA).
SUMMER_HEALTH_RE = re.compile(r"\bsummer\s*health\b", re.IGNORECASE)

MAX_SENTENCES = 5
MAX_CHARS = 900


def _matches(patterns: List[str], text: str) -> List[str]:
    hits = []
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            hits.append(p)
    return hits


def check(draft_text: str) -> Dict:
    violations: List[str] = []
    text = draft_text or ""

    for pat in _matches(DIAGNOSTIC_PATTERNS, text):
        violations.append(f"diagnostic_language:{pat}")

    for pat in _matches(URGENCY_PATTERNS, text):
        violations.append(f"urgency_exaggeration:{pat}")

    sh_hits = SUMMER_HEALTH_RE.findall(text)
    if len(sh_hits) > 1:
        violations.append("multiple_ctas")
    if len(sh_hits) == 0:
        violations.append("missing_cta")

    if "!" in text:
        violations.append("exclamation_mark")

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if len(sentences) > MAX_SENTENCES:
        violations.append(f"too_many_sentences:{len(sentences)}")

    if len(text) > MAX_CHARS:
        violations.append(f"too_long:{len(text)}")

    return {"passed": len(violations) == 0, "violations": violations}

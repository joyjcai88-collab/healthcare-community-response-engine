"""Mock Reddit post source for demos.

Yields hand-crafted parent-posts that look like r/Parenting et al. Useful when:
  - you don't have Reddit API credentials yet
  - you're pitching and need posts to appear on command
  - you want deterministic content for screenshots / video

Integrates with the same pipeline as the real Reddit ingester: rows are written
through `database.upsert_post` and `database.insert_draft`, so the dashboard
cannot tell the difference.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator, List


# (title, body, subreddit, author, minutes_ago)
_POOL: List[tuple] = [
    (
        "Baby has 101.4F fever — should I go to ER?",
        "My 4 month old has had a fever of 101.4 since this morning. He's still nursing ok and sleeping but I'm scared. No other symptoms. Should I take him in or wait it out?",
        "NewParents", "anxious_mom_44", 12,
    ),
    (
        "Splotchy red rash on cheeks and chest — normal?",
        "My 6 week old woke up with a red splotchy rash on her cheeks and chest. She seems fine otherwise, eating and pooping normally. Is this newborn acne or something else?",
        "beyondthebump", "firsttimemom_22", 38,
    ),
    (
        "2yo hasn't eaten solid food in 3 days",
        "My 2yo has barely eaten in 3 days. He's drinking water and milk but won't touch any food. No fever. Is this normal toddler stuff or should I worry? He's otherwise acting himself.",
        "Parenting", "dad_of_two_99", 55,
    ),
    (
        "9 month old waking every 45 minutes — is this normal?",
        "We're in month 3 of 45-minute wakings at night. Tried wake-windows, schedules, everything. I haven't slept more than 2 hours in a row since December. Is this a phase or is something wrong?",
        "sleeptrain", "exhausted_parent_01", 82,
    ),
    (
        "Coughing that sounds like a bark — croup?",
        "My 18mo started a weird barking cough tonight. Breathing seems fine, no fever. Read about croup online but not sure. Should I be worried?",
        "Parenting", "crouplife", 21,
    ),
    (
        "Newborn grunting and straining — gas?",
        "My 3 week old grunts and strains like crazy, especially at night. Poops fine. Pediatrician said it's normal but I can't tell if she's in pain. Anyone else?",
        "beyondthebump", "nicu_grad_mom", 130,
    ),
    (
        "Toddler biting at daycare — how to handle?",
        "My 20 month old has started biting other kids at daycare. Daycare is patient but I don't know what to do. Is this a developmental phase?",
        "Parenting", "toddler_trouble", 200,
    ),
    (
        "Is this normal newborn breathing?",
        "My 5 day old breathes in these weird irregular bursts when sleeping — fast, fast, pause, fast. No blue color, no distress. Pediatrician was closed when I noticed. Should I go to urgent care?",
        "NewParents", "newdad_anxious", 18,
    ),
    (
        "Breastfeeding hurts so bad — something wrong?",
        "My LO is 2 weeks old and every latch feels like needles. Cracked nipples, blood, the works. Lactation consultant says latch looks fine. Is this just how it is or is something off?",
        "breastfeeding", "bleeding_and_tired", 70,
    ),
    (
        "Help — baby keeps pulling at ears",
        "My 10 month old has been pulling at her right ear and crying for two days. Low fever (100.1). Is this definitely an ear infection or could it be teething?",
        "Parenting", "momof1_firstear", 33,
    ),
    (
        "Green mucus for 5 days — antibiotics time?",
        "3yo has had green mucus and a mild cough for 5 days. No fever. Still eating and playing but grumpy. Do I need to take her in?",
        "Parenting", "pragmatic_parent", 95,
    ),
    (
        "Diaper rash that won't clear up",
        "Using every cream, changing every 2 hours, air-drying — and my 8mo still has a bright red raised rash for a week. Is this yeast?",
        "beyondthebump", "cloth_diaper_convert", 160,
    ),
]


def pull_n(n: int = 3) -> Iterator[dict]:
    """Yield N random mock posts as dicts matching the real ingest schema.

    Each call generates fresh external_ids so re-runs produce new queue items.
    """
    picks = random.sample(_POOL, k=min(n, len(_POOL)))
    now = datetime.now(tz=timezone.utc)
    for title, body, sub, author, minutes_ago in picks:
        created = now - timedelta(minutes=minutes_ago, seconds=random.randint(0, 59))
        yield {
            "platform": "reddit",
            "external_id": f"t3_mock_{uuid.uuid4().hex[:10]}",
            "subreddit": sub,
            "author": author,
            "title": title,
            "text": body,
            "permalink": f"https://www.reddit.com/r/{sub}/comments/mock",
            "created_utc": created.isoformat(),
        }


def pull_one() -> dict:
    return next(pull_n(1))

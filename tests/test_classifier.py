import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.classifier import keyword_match, _heuristic_urgency


def test_keyword_match_positive():
    assert keyword_match("My baby has a fever of 102")
    assert keyword_match("There's a weird rash on his arm")
    assert keyword_match("She's not eating since yesterday")
    assert keyword_match("Should I go to ER?")
    assert keyword_match("Is this normal??")
    assert keyword_match("Help, my baby is lethargic")


def test_keyword_match_negative():
    assert not keyword_match("Just sharing a cute moment with my toddler")
    assert not keyword_match("What car seat do you recommend?")
    assert not keyword_match("")


def test_heuristic_urgency_escalates_on_high_signal_words():
    low = _heuristic_urgency("just curious about routines")
    high = _heuristic_urgency("baby is lethargic and has trouble breathing")
    assert high > low
    assert 0 <= low <= 1
    assert 0 <= high <= 1

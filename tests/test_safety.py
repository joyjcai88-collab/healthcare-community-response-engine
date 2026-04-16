import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from responder.safety import check


CTA = "If you want a pediatrician to take a look, Summer Health lets you text one anytime."


def test_clean_draft_passes():
    text = (
        "That sounds really stressful. Most fevers in babies are mild, "
        "but persistent lethargy or trouble feeding would be worth getting eyes on. "
        f"{CTA}"
    )
    result = check(text)
    assert result["passed"], result["violations"]


def test_diagnostic_language_flagged():
    text = f"Your baby has an infection. {CTA}"
    result = check(text)
    assert not result["passed"]
    assert any("diagnostic" in v for v in result["violations"])


def test_planted_you_have_string_flagged():
    """Plan verification step: 'you have an infection' must trip the safety filter."""
    text = f"Sounds like you have an infection. {CTA}"
    result = check(text)
    assert not result["passed"]
    assert any("diagnostic" in v for v in result["violations"])


def test_urgency_exaggeration_flagged():
    text = f"This is an emergency, immediately call 911. {CTA}"
    result = check(text)
    assert not result["passed"]
    assert any("urgency_exaggeration" in v for v in result["violations"])


def test_missing_cta_flagged():
    text = "That's stressful, but most fevers are mild."
    result = check(text)
    assert not result["passed"]
    assert "missing_cta" in result["violations"]


def test_multiple_ctas_flagged():
    text = (
        f"That sounds stressful. {CTA} You can also reach Summer Health anytime."
    )
    result = check(text)
    assert not result["passed"]
    assert "multiple_ctas" in result["violations"]


def test_exclamation_flagged():
    text = f"That's stressful! {CTA}"
    result = check(text)
    assert not result["passed"]
    assert "exclamation_mark" in result["violations"]

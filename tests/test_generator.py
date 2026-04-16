"""Generator test with mocked Anthropic client."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeAnthropic:
    def __init__(self, *_args, **_kwargs):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        # Echo a draft that satisfies the safety filter.
        text = (
            "That sounds really stressful. Most low-grade fevers in babies are mild, "
            "but persistent lethargy or trouble feeding would be worth getting eyes on. "
            "If you want a pediatrician to take a look, Summer Health lets you text one anytime."
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def test_generate_returns_draft(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    with patch("anthropic.Anthropic", _FakeAnthropic):
        from responder.generator import generate

        out = generate(
            title="My baby has a fever",
            text="He's 4 months and 101F. Should I worry?",
            urgency_score=0.5,
        )
    assert "Summer Health" in out["draft_text"]
    assert out["model"]
    assert out["prompt_version"]


def test_generate_picks_opus_for_high_urgency(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    with patch("anthropic.Anthropic", _FakeAnthropic):
        from responder.generator import generate, OPUS_MODEL

        out = generate(title="x", text="y", urgency_score=0.9)
    assert out["model"] == OPUS_MODEL

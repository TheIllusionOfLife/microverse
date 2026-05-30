"""BeliefSummarizer — ADR 0007 Phase 1 (Stage C).

An out-of-world LLM pass (Elder-shaped) that summarizes an agent's recent
activity into one short, first-person belief/commitment line. NOT called
inside ``agent.think()``, so the single-model invariant for the action
loop is preserved. All tests mock the LLM so the default suite stays
offline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from microverse import config
from microverse.agents.belief import BeliefSummarizer
from microverse.memory.episodic import Event
from microverse.ops.metrics import Metrics


def _events() -> list[Event]:
    return [
        Event(
            id=1, ts=1.0, actor="Aki", action="craft", target=None, payload={"artifact": "a bowl"}
        ),
        Event(
            id=2, ts=2.0, actor="Aki", action="speak", target="Cy", payload={"thought": "share?"}
        ),
    ]


def _chat(content: str) -> Any:
    return {"content": content, "thinking": ""}


def test_summarize_returns_cleaned_belief() -> None:
    metrics = Metrics(":memory:")
    with patch(
        "microverse.agents.belief.chat",
        return_value=_chat("  I believe slow work outlasts fast work.  "),
    ):
        out = BeliefSummarizer().summarize(
            agent_name="Aki", role="artisan", events=_events(), prior="", metrics=metrics
        )
    assert out == "I believe slow work outlasts fast work."
    metrics.close()


def test_summarize_strips_thinking_markers() -> None:
    metrics = Metrics(":memory:")
    leaky = "<think>internal scratch</think>The grain tells me where to cut."
    with patch("microverse.agents.belief.chat", return_value=_chat(leaky)):
        out = BeliefSummarizer().summarize(
            agent_name="Aki", role="artisan", events=_events(), prior="", metrics=metrics
        )
    assert out is not None
    assert "<think>" not in out
    assert "internal scratch" not in out
    metrics.close()


def test_summarize_caps_length() -> None:
    metrics = Metrics(":memory:")
    long = "belief " * 200
    with patch("microverse.agents.belief.chat", return_value=_chat(long)):
        out = BeliefSummarizer().summarize(
            agent_name="Aki", role="artisan", events=_events(), prior="", metrics=metrics
        )
    assert out is not None
    assert len(out) <= config.BELIEF_MAX_CHARS
    metrics.close()


def test_summarize_returns_none_on_chat_failure() -> None:
    metrics = Metrics(":memory:")
    with patch("microverse.agents.belief.chat", side_effect=RuntimeError("ollama down")):
        out = BeliefSummarizer().summarize(
            agent_name="Aki",
            role="artisan",
            events=_events(),
            prior="prior belief",
            metrics=metrics,
        )
    assert out is None  # caller keeps the prior belief
    assert metrics.get("belief_chat_failure") == 1
    metrics.close()


def test_summarize_returns_none_on_empty_content() -> None:
    metrics = Metrics(":memory:")
    with patch("microverse.agents.belief.chat", return_value=_chat("   ")):
        out = BeliefSummarizer().summarize(
            agent_name="Aki", role="artisan", events=_events(), prior="", metrics=metrics
        )
    assert out is None
    metrics.close()

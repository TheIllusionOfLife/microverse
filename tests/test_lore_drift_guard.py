"""Elder lore compression with Jaccard drift guard.

Phase 3b contract:
  - ``Elder.compress_lore(prior_lore, events)`` calls the LLM to
    rewrite the world's mythic lore. The new output must share at
    least ``MIN_JACCARD`` (default 0.5) of its tokens with the prior
    lore — large drift means the model is hallucinating away from
    canon.
  - On low jaccard, retry once with a "preserve continuity" hint.
  - On second failure, keep the prior lore and bump the
    ``lore_drift_block`` metric so the watchdog can see the issue.
  - The pipeline never raises.
"""

from __future__ import annotations

from unittest.mock import patch

from microverse.agents.elder import Elder, lore_jaccard
from microverse.memory.episodic import Event
from microverse.ops.metrics import Metrics


def _events(n: int = 3) -> list[Event]:
    return [
        Event(
            id=i,
            ts=float(i),
            actor="aki",
            action="craft",
            target=None,
            payload={"thought": f"made artifact number {i}"},
        )
        for i in range(n)
    ]


def test_jaccard_is_one_for_identical_text():
    a = "the village by the river"
    assert lore_jaccard(a, a) == 1.0


def test_jaccard_is_zero_for_disjoint_text():
    assert lore_jaccard("alpha beta gamma", "delta epsilon zeta") == 0.0


def test_jaccard_is_one_for_two_empty_strings():
    """Edge: vacuously equal — no drift signal."""
    assert lore_jaccard("", "") == 1.0


def test_jaccard_handles_punctuation_and_case():
    """Tokenizer should be punctuation-stripping, case-folding."""
    assert lore_jaccard("Wood, stone — water!", "wood stone water") == 1.0


def test_compress_lore_accepts_when_drift_within_budget():
    metrics = Metrics(":memory:")
    prior = "The village by the river thrives on craft and harvest"
    new = "The river village thrives through craft and the autumn harvest"
    canned = {"content": new, "thinking": "", "raw": {}}
    with patch("microverse.agents.elder.chat", return_value=canned):
        out = Elder(name="Old").compress_lore(prior, _events(), metrics=metrics)
    assert out == new
    assert metrics.get("lore_drift_block") == 0


def test_compress_lore_retries_with_continuity_hint_then_accepts():
    metrics = Metrics(":memory:")
    prior = "The village stands beside an ancient river. Stonework guards the harvest."
    drifty = "Once upon a time in a galaxy far far away, robots danced."
    settled = "The village stands beside an ancient river; stonework still guards the harvest."

    seq = [
        {"content": drifty, "thinking": "", "raw": {}},
        {"content": settled, "thinking": "", "raw": {}},
    ]
    call_count = [0]

    def _chat(**_kwargs):
        out = seq[call_count[0]]
        call_count[0] += 1
        return out

    with patch("microverse.agents.elder.chat", side_effect=_chat):
        out = Elder(name="Old").compress_lore(prior, _events(), metrics=metrics)

    assert out == settled
    assert call_count[0] == 2  # one normal call + one retry
    assert metrics.get("lore_drift_block") == 0


def test_compress_lore_blocks_after_two_failures():
    metrics = Metrics(":memory:")
    prior = "The forge sings in the morning when the river runs slow."
    drifty = "Hexapods dominate the surface in the year 2087."

    canned = {"content": drifty, "thinking": "", "raw": {}}
    with patch("microverse.agents.elder.chat", return_value=canned):
        out = Elder(name="Old").compress_lore(prior, _events(), metrics=metrics)

    assert out == prior  # original kept
    assert metrics.get("lore_drift_block") == 1


def test_compress_lore_with_empty_prior_accepts_anything():
    """A fresh world has no prior to drift from."""
    metrics = Metrics(":memory:")
    canned = {"content": "First lore: a young river village", "thinking": "", "raw": {}}
    with patch("microverse.agents.elder.chat", return_value=canned):
        out = Elder(name="Old").compress_lore("", _events(), metrics=metrics)
    assert out == "First lore: a young river village"
    assert metrics.get("lore_drift_block") == 0


def test_compress_lore_handles_chat_exception():
    """If the LLM call raises, keep the prior and bump the metric —
    don't let the Elder crash the run loop."""
    metrics = Metrics(":memory:")
    prior = "Old lore"
    with patch("microverse.agents.elder.chat", side_effect=TimeoutError("hung")):
        out = Elder(name="Old").compress_lore(prior, _events(), metrics=metrics)
    assert out == prior
    assert metrics.get("lore_drift_block") == 1


def test_elder_role_is_elder():
    assert Elder(name="Old").role == "elder"


def test_jaccard_filters_stop_words_and_short_tokens():
    """Two semantically unrelated passages that share function words
    must NOT clear the threshold via stop-word overlap alone."""
    a = "the village is in the river valley"
    b = "the rocket is in the orbital station"
    # Both share {the, is, in, the} = stop words. After filtering,
    # signal tokens are disjoint: {village, river, valley} vs
    # {rocket, orbital, station}.
    assert lore_jaccard(a, b) == 0.0


def test_granular_metrics_distinguish_drift_from_chat_failure():
    """Drift block and chat failure must bump separate counters so the
    Phase 4 watchdog can tell them apart."""
    metrics = Metrics(":memory:")
    prior = "the village by the ancient river under stonework arches"
    drifty = "the rockets land on Mars in the year 2087"
    canned = {"content": drifty, "thinking": "", "raw": {}}
    with patch("microverse.agents.elder.chat", return_value=canned):
        Elder(name="Old").compress_lore(prior, _events(), metrics=metrics)
    assert metrics.get("lore_drift_block") == 1
    assert metrics.get("lore_chat_failure") == 0
    assert metrics.get("lore_compress_accepted") == 0

    metrics2 = Metrics(":memory:")
    with patch("microverse.agents.elder.chat", side_effect=TimeoutError("hung")):
        Elder(name="Old").compress_lore(prior, _events(), metrics=metrics2)
    assert metrics2.get("lore_chat_failure") == 2  # round 1 + round 2
    assert metrics2.get("lore_drift_block") == 1
    assert metrics2.get("lore_compress_accepted") == 0


def test_round1_success_bumps_compress_accepted():
    metrics = Metrics(":memory:")
    prior = "the village by the river under stonework arches"
    new = "the river village still endures, with new stonework arches"
    with patch(
        "microverse.agents.elder.chat",
        return_value={"content": new, "thinking": "", "raw": {}},
    ):
        Elder(name="Old").compress_lore(prior, _events(), metrics=metrics)
    assert metrics.get("lore_compress_accepted") == 1
    assert metrics.get("lore_compress_retry_accepted") == 0
    assert metrics.get("lore_drift_block") == 0

"""Trader: ranks an artifact buffer by novelty/utility/completeness.

Phase 2 contract:
  - ``Trader.rank(candidates)`` returns a list of ``Score`` ordered to
    match ``candidates`` (one score per candidate, by index).
  - Score fields: ``artifact_id`` (int index), ``score`` (0.0-1.0),
    ``rationale`` (short text).
  - Uses factual sampling (low temperature) so judgments are stable
    across rounds, not creative.
  - Robust to malformed JSON: any artifact missing a score gets a
    default 0.0, never a crash.
"""

from __future__ import annotations

from unittest.mock import patch

from microverse.agents.harvester import ArtifactCandidate
from microverse.agents.trader import Score, Trader


def _candidates() -> list[ArtifactCandidate]:
    return [
        ArtifactCandidate(actor="aki", action="craft", artifact="a wooden bowl", ts=0.0),
        ArtifactCandidate(actor="aki", action="craft", artifact="a stone hammer", ts=1.0),
        ArtifactCandidate(actor="aki", action="craft", artifact="a leather pouch", ts=2.0),
    ]


def test_trader_role_is_trader():
    t = Trader(name="Bo")
    assert t.role == "trader"


def test_trader_uses_factual_sampling():
    from microverse.config import SAMPLING_FACTUAL

    t = Trader(name="Bo")
    assert t.sampling == SAMPLING_FACTUAL


def test_rank_returns_one_score_per_candidate_in_index_order():
    canned = {
        "content": (
            '[{"artifact_id": 0, "score": 0.9, "rationale": "good"}, '
            '{"artifact_id": 1, "score": 0.3, "rationale": "ok"}, '
            '{"artifact_id": 2, "score": 0.7, "rationale": "fine"}]'
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned) as mock_chat:
        scores = Trader(name="Bo").rank(_candidates())

    assert len(scores) == 3
    assert [s.artifact_id for s in scores] == [0, 1, 2]
    assert [round(s.score, 1) for s in scores] == [0.9, 0.3, 0.7]
    # Sanity: factual sampling + JSON format requested.
    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("format") == "json"
    assert kwargs.get("options", {}).get("temperature", 1.0) == 0.6


def test_rank_clamps_out_of_range_scores():
    canned = {
        "content": (
            '[{"artifact_id": 0, "score": 1.7, "rationale": "x"}, '
            '{"artifact_id": 1, "score": -0.4, "rationale": "y"}, '
            '{"artifact_id": 2, "score": 0.5, "rationale": "z"}]'
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert scores[0].score == 1.0
    assert scores[1].score == 0.0
    assert scores[2].score == 0.5


def test_rank_fills_missing_artifacts_with_zero():
    """If the model only returns scores for some candidates, missing
    indices get score=0 — never a crash, never silent dropping."""
    canned = {
        "content": '[{"artifact_id": 1, "score": 0.8, "rationale": "ok"}]',
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert len(scores) == 3
    assert scores[0].score == 0.0
    assert scores[1].score == 0.8
    assert scores[2].score == 0.0


def test_rank_handles_completely_garbage_response():
    canned = {"content": "not json at all", "thinking": "", "raw": {}}
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert len(scores) == 3
    assert all(s.score == 0.0 for s in scores)


def test_rank_handles_repaired_json():
    canned = {
        "content": (
            "Here you go: "
            '[{"artifact_id": 0, "score": 0.5, "rationale": "x"},'
            '{"artifact_id": 1, "score": 0.5, "rationale": "y"},'
            '{"artifact_id": 2, "score": 0.5, "rationale": "z"},]'  # trailing comma
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert all(s.score == 0.5 for s in scores)


def test_rank_empty_candidates_returns_empty():
    t = Trader(name="Bo")
    # No chat call should be made.
    with patch("microverse.agents.trader.chat") as mock_chat:
        scores = t.rank([])
    assert scores == []
    mock_chat.assert_not_called()


def test_score_pydantic_model_validates():
    s = Score(artifact_id=0, score=0.5, rationale="x")
    assert s.score == 0.5


def test_rank_unwraps_object_wrapped_list():
    """gemma4 sometimes wraps the score list in an object — the parser
    must accept ``{"scores": [...]}`` and similar single-list-value
    objects, not just bare arrays."""
    canned = {
        "content": (
            '{"scores": ['
            '{"artifact_id": 0, "score": 0.4, "rationale": "x"},'
            '{"artifact_id": 1, "score": 0.7, "rationale": "y"},'
            '{"artifact_id": 2, "score": 0.9, "rationale": "z"}]}'
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert [round(s.score, 1) for s in scores] == [0.4, 0.7, 0.9]


def test_rank_prefers_known_key_over_first_list():
    """When a wrapped response has multiple lists (e.g. metadata +
    scores), the parser must pick the one keyed `scores`, not just
    whichever happens to come first."""
    canned = {
        "content": (
            "{"
            '"metadata": ["explanation line 1", "line 2"],'
            '"scores": ['
            '{"artifact_id": 0, "score": 0.2, "rationale": "x"},'
            '{"artifact_id": 1, "score": 0.5, "rationale": "y"},'
            '{"artifact_id": 2, "score": 0.8, "rationale": "z"}]'
            "}"
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert [round(s.score, 1) for s in scores] == [0.2, 0.5, 0.8]


def test_rank_falls_back_to_unique_score_shaped_list():
    """If no known wrapping key is present but exactly one value is a
    list of dicts with `artifact_id`, that list is taken."""
    canned = {
        "content": (
            "{"
            '"explanation": ["I judged each artifact carefully"],'
            '"verdict": ['
            '{"artifact_id": 0, "score": 0.6, "rationale": "x"},'
            '{"artifact_id": 1, "score": 0.7, "rationale": "y"},'
            '{"artifact_id": 2, "score": 0.8, "rationale": "z"}]'
            "}"
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.trader.chat", return_value=canned):
        scores = Trader(name="Bo").rank(_candidates())
    assert [round(s.score, 1) for s in scores] == [0.6, 0.7, 0.8]

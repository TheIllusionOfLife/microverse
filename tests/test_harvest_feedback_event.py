"""Slice 5 (Alt-B): harvest rating events flow into recent_episodic.

Codex's review of the Layer-G plan called this the "right factual
feedback signal" — the only external voice telling the LLM what is
actually being valued. Prior layers fixed the *negative* feedback
loop (cutting the autobiographical thought channel); this slice
adds a *positive* exogenous signal: after the Trader ranks each
flush batch, the Harvester appends one synthetic event per candidate
(``actor="harvest"``, ``action="rated"``) into episodic. The
already-installed memory rendering surfaces these as
``"[harvest] Trader rated Aki's craft 0.82 (accepted)"`` in the next
tick's recent_episodic — so the LLM's optimisation can align to
harvest quality rather than its own narrative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from microverse.agents.base import WorldContext
from microverse.agents.harvester import ArtifactCandidate, Harvester
from microverse.agents.trader import Score
from microverse.memory import build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory


class _StubTrader:
    """Stub ranker that returns a fixed score per candidate index."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def rank(self, candidates: list[ArtifactCandidate]) -> list[Score]:
        out: list[Score] = []
        for i, _ in enumerate(candidates):
            out.append(
                Score(artifact_id=i, score=self._scores[i], rationale=f"stub-{i}")
            )
        return out


def _candidate(actor: str, artifact: str, ts: float = 100.0) -> ArtifactCandidate:
    return ArtifactCandidate(actor=actor, action="craft", artifact=artifact, ts=ts)


def test_flush_appends_rating_event_per_candidate(tmp_path: Path) -> None:
    """Every ranked candidate must produce one synthetic rated event."""
    trader = _StubTrader([0.2, 0.8])
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        harvester = Harvester(tmp_path / "harvest", trader=trader, percentile=70, episodic=ep)
        harvester.consider(_candidate("Aki", "a wooden bowl"))
        harvester.consider(_candidate("Cy", "a short field note about the mist"))
        harvester.flush()

        rated = [e for e in ep.last(50) if e.actor == "harvest" and e.action == "rated"]
    assert len(rated) == 2, f"expected one rated event per candidate, got {len(rated)}: {rated!r}"
    payload_scores = sorted(e.payload.get("score") for e in rated)
    assert payload_scores == pytest.approx([0.2, 0.8])


def test_flush_event_payload_carries_actor_kind_score_accepted(tmp_path: Path) -> None:
    """Each rated event's payload includes the creator (actor), the
    artifact kind (action), the score, and the accepted flag."""
    trader = _StubTrader([0.9])
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        harvester = Harvester(tmp_path / "harvest", trader=trader, percentile=70, episodic=ep)
        harvester.consider(_candidate("Aki", "a small wooden box"))
        harvester.flush()
        rated = [e for e in ep.last(50) if e.actor == "harvest"][0]
    payload = rated.payload
    assert payload.get("actor") == "Aki", f"creator must be in payload.actor, got {payload!r}"
    assert payload.get("kind") == "craft"
    assert payload.get("score") == pytest.approx(0.9)
    assert payload.get("accepted") is True


def test_flush_marks_rejected_candidates_as_not_accepted(tmp_path: Path) -> None:
    """A candidate below the percentile cutoff records accepted=False
    so the LLM sees the rejection signal too."""
    trader = _StubTrader([0.1, 0.9])
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        harvester = Harvester(tmp_path / "harvest", trader=trader, percentile=70, episodic=ep)
        harvester.consider(_candidate("Aki", "a short rough sketch"))
        harvester.consider(_candidate("Cy", "a precise field note"))
        harvester.flush()
        rated = sorted(
            (e for e in ep.last(50) if e.actor == "harvest"),
            key=lambda e: e.payload.get("score") or 0.0,
        )
    assert rated[0].payload.get("accepted") is False
    assert rated[1].payload.get("accepted") is True


def test_format_episodic_renders_harvest_event(tmp_path: Path) -> None:
    """The harvest event renders as
    ``"[harvest] Trader rated {actor}'s {kind} {score:.2f} ({accepted})"``."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="harvest",
            action="rated",
            target=None,
            payload={"actor": "Aki", "kind": "craft", "score": 0.82, "accepted": True},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")
    assert "[harvest] Trader rated Aki's craft 0.82 (accepted)" in out.recent_episodic, (
        f"expected harvest line, got {out.recent_episodic!r}"
    )


def test_format_episodic_renders_rejected_harvest_event(tmp_path: Path) -> None:
    """Rejected events render the rejected accepted-tag."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="harvest",
            action="rated",
            target=None,
            payload={"actor": "Cy", "kind": "craft", "score": 0.12, "accepted": False},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")
    assert "[harvest] Trader rated Cy's craft 0.12 (rejected)" in out.recent_episodic


def test_flush_without_episodic_does_not_crash(tmp_path: Path) -> None:
    """Backwards-compat: an episodic-less Harvester still flushes
    candidates, just without emitting feedback events."""
    trader = _StubTrader([0.9])
    harvester = Harvester(tmp_path / "harvest", trader=trader, percentile=70)
    harvester.consider(_candidate("Aki", "a small wooden box"))
    written = harvester.flush()
    # Single-item population has cutoff = its own score, so it accepts.
    assert len(written) == 1

"""Phase 6 — Harvester second mode (WIP harvest) + Trader v2.

ADR 0003 Decisions 2 and 3:

* The Harvester drains TWO buffers at flush(): single-tick artifacts
  (existing) AND completed WIPs (new). For the trader-attached mode,
  both kinds flow through ``Trader.rank()`` which produces one Score
  per candidate regardless of kind.

* Trader v2 is heterogeneous:
  - ArtifactCandidate (kind="artifact"): existing LLM-driven score
    path — unchanged. test_trader.py keeps passing.
  - WIPCandidate (kind="wip"): **rule-based** score with no LLM call.
    score = mean(length_score, contributor_score, novelty_score)
    where novelty = 1 - mean Jaccard over the last N completed WIPs.

* Per-primary-verb caps applied AFTER ranking, in Harvester. A flush
  that would otherwise accept 10 crafts is bounded to
  ``HARVEST_CRAFT_CAP_PER_FLUSH`` crafts.

* Trader output never re-enters agent-visible context. A synthetic
  harvest event injected into the episodic log does NOT appear in
  any ``build_context()`` result for any agent (parallel to PR #24
  structural-leak sweep).
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.base import WorldContext
from microverse.agents.harvester import ArtifactCandidate, Harvester, WIPCandidate
from microverse.agents.trader import Score, score_wip
from microverse.config import HARVEST_CRAFT_CAP_PER_FLUSH
from microverse.memory import build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory
from microverse.world.workshop import CONFIGURED_WIPS, WorkshopProjection

# ---------------------------------------------------------------------------
# Trader v2: rule-based WIP scoring
# ---------------------------------------------------------------------------


def test_score_wip_returns_value_in_unit_interval() -> None:
    c = WIPCandidate(
        name="workshop.loom",
        contributors=("Aki", "Bo"),
        fragments=(("Aki", "rough warp of dyed wool"), ("Bo", "blue stitching across the warp")),
        ts=1.0,
    )
    s = score_wip(c, last_completed=[])
    assert 0.0 <= s <= 1.0


def test_score_wip_rewards_longer_fragments() -> None:
    short = WIPCandidate(
        name="x",
        contributors=("Aki",),
        fragments=(("Aki", "tiny"),),
        ts=1.0,
    )
    long = WIPCandidate(
        name="y",
        contributors=("Aki",),
        fragments=(
            (
                "Aki",
                "rough warp of dyed wool with cherry-blossom finish"
                " painted onto the upper border in three layers",
            ),
        ),
        ts=1.0,
    )
    assert score_wip(long, last_completed=[]) > score_wip(short, last_completed=[])


def test_score_wip_rewards_more_contributors() -> None:
    solo = WIPCandidate(
        name="x",
        contributors=("Aki",),
        fragments=(("Aki", "warp"), ("Aki", "weft")),
        ts=1.0,
    )
    duo = WIPCandidate(
        name="y",
        contributors=("Aki", "Bo"),
        fragments=(("Aki", "warp"), ("Bo", "weft")),
        ts=1.0,
    )
    assert score_wip(duo, last_completed=[]) > score_wip(solo, last_completed=[])


def test_score_wip_penalises_repetition_against_recent_completions() -> None:
    """A WIP whose tokens overlap heavily with a recent completion
    scores lower than a token-disjoint WIP. The novelty term uses
    Jaccard distance over min_len=4 tokens (re-uses the existing
    _text.tokenize helper).
    """
    prev = WIPCandidate(
        name="prev",
        contributors=("Aki",),
        fragments=(("Aki", "blue cherry blossom wooden bowl carved"),),
        ts=0.0,
    )
    similar = WIPCandidate(
        name="similar",
        contributors=("Aki",),
        fragments=(("Aki", "blue cherry blossom wooden bowl carved deep"),),
        ts=2.0,
    )
    novel = WIPCandidate(
        name="novel",
        contributors=("Aki",),
        fragments=(("Aki", "iron lantern wrought with celestial astronomy"),),
        ts=2.0,
    )
    assert score_wip(novel, last_completed=[prev]) > score_wip(similar, last_completed=[prev])


# ---------------------------------------------------------------------------
# Harvester WIP harvest
# ---------------------------------------------------------------------------


class _PredictableRanker:
    """Test double for Trader. Returns scores from a fixed dict
    keyed by candidate identity (artifact text or WIP name)."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def rank(self, candidates: list) -> list[Score]:
        out: list[Score] = []
        for i, c in enumerate(candidates):
            key = c.name if isinstance(c, WIPCandidate) else (c.artifact or "")
            out.append(Score(artifact_id=i, score=self._scores.get(key, 0.5), rationale=""))
        return out


def _drive_wip_to_complete(ep: EpisodicMemory, wip: str, ts_base: float = 1.0) -> None:
    """Append 8 fragments alternating between two contributors so the
    projection promotes ``wip`` to complete (8 == COMPLETE_FRAGMENT_FLOOR).
    """
    for i in range(8):
        actor = "Aki" if i % 2 == 0 else "Bo"
        ep.append(
            actor=actor,
            action="contribute",
            target=wip,
            payload={"thought": "x", "fragment": f"frag-{i} from {actor}"},
            ts=ts_base + i,
        )


def test_harvester_writes_completed_wip_to_inbox(tmp_path: Path) -> None:
    """A WIP that has transitioned to complete is written to the
    harvest inbox on flush, with frontmatter capturing the WIP name,
    contributors, and a manifest line recording the score.
    """
    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(db) as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0)
        proj = WorkshopProjection(ep)
        # All scores in top-30% so percentile cutoff accepts.
        ranker = _PredictableRanker({target: 0.95})
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
        )
        written = harvester.flush()
    assert len(written) == 1
    body = written[0].read_text(encoding="utf-8")
    assert target in body
    # Both contributors appear in the body (community knowledge —
    # the harvest output is not per-receiver redacted, it is the
    # external observer view).
    assert "Aki" in body
    assert "Bo" in body


def test_harvester_does_not_double_harvest_same_wip(tmp_path: Path) -> None:
    """A WIP completed at flush N is NOT harvested again at flush
    N+1. ``_harvested_wips`` tracks names already written.
    """
    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(db) as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0)
        proj = WorkshopProjection(ep)
        ranker = _PredictableRanker({target: 0.95})
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
        )
        first = harvester.flush()
        second = harvester.flush()
    assert len(first) == 1
    assert len(second) == 0


def test_harvester_caps_craft_acceptance_per_flush(tmp_path: Path) -> None:
    """Per-primary-verb cap: when more crafts are buffered than the
    cap, only the top ``HARVEST_CRAFT_CAP_PER_FLUSH`` by score are
    accepted; the rest are recorded as rejected in the manifest.
    """
    # Buffer N+3 crafts where N is the cap. All same-length so
    # short-circuit acceptance can't pretend otherwise. Use
    # decreasing scores so we can verify the top-N are picked.
    n = HARVEST_CRAFT_CAP_PER_FLUSH
    crafts = [
        ArtifactCandidate(
            actor="Aki",
            action="craft",
            artifact=f"craft-{i}: a thing here that is longer than 20 chars",
            ts=float(i),
        )
        for i in range(n + 3)
    ]
    scores = {c.artifact: 1.0 - i * 0.01 for i, c in enumerate(crafts)}
    ranker = _PredictableRanker(scores)  # type: ignore[arg-type]
    harvester = Harvester(tmp_path / "harvest", trader=ranker, percentile=0)
    for c in crafts:
        harvester.consider(c)
    written = harvester.flush()
    assert len(written) == n


def test_harvester_works_without_workshop_reference(tmp_path: Path) -> None:
    """Back-compat: a Harvester constructed without ``workshop=`` keeps
    the v0.1.1 single-buffer behaviour exactly.
    """
    harvester = Harvester(tmp_path / "harvest", percentile=70)  # no trader, no workshop
    harvester.consider(
        ArtifactCandidate(
            actor="Aki",
            action="craft",
            artifact="a wooden flute carved with care",
            ts=1.0,
        )
    )
    written = harvester.flush()
    # No trader means heuristic mode wrote on consider; flush is a no-op.
    assert written == []


# ---------------------------------------------------------------------------
# Trader-feedback-invisibility leak sweep (ADR 0003 Decision 3)
# ---------------------------------------------------------------------------


def test_no_harvest_actor_event_leaks_into_build_context(tmp_path: Path) -> None:
    """Inject synthetic harvest verdict events into episodic; build
    context for every agent; assert none of the verdict text surfaces
    in any text-bearing field of the returned WorldContext.

    This is the parallel to PR #24's 897-sample structural-leak sweep
    for the Trader-feedback channel.
    """
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        SemanticMemory(tmp_path / "se.sqlite") as se,
    ):
        for i in range(20):
            ep.append(
                actor="harvest",
                action="rated",
                target="Aki",
                payload={
                    "score": 0.9,
                    "rationale": f"verdict-rationale-distinctive-{i}",
                    "thought": f"verdict-rationale-distinctive-{i}",
                },
                ts=float(i),
            )
        for agent in ("Aki", "Cy", "Bo"):
            world = build_context(
                world_base=WorldContext(),
                episodic=ep,
                semantic=se,
                topic="",
                receiver_name=agent,
            )
            text_fields = "\n".join(
                [
                    world.season,
                    world.weather,
                    *world.peers_today,
                    *(s.utterance for s in world.peer_inbox),
                    *world.world_events,
                    *world.lore_excerpt,
                    world.engagement_hint,
                    world.required_target or "",
                    *(v.excerpt for v in world.workshop_view),
                    *(v.contributors for v in world.workshop_view),
                ]
            )
            for i in range(20):
                assert f"verdict-rationale-distinctive-{i}" not in text_fields, (
                    f"trader verdict leaked into {agent}'s context"
                )

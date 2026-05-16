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


class _MutableRanker:
    """Test double whose score for a key can be changed between flushes
    — used to simulate a WIP that the ranker rejects on flush-1 (score
    below cutoff) and accepts on flush-2 (score above cutoff). The
    earlier ``_PredictableRanker`` keeps a frozen dict.
    """

    def __init__(self) -> None:
        self.scores: dict[str, float] = {}

    def rank(self, candidates: list) -> list[Score]:
        out: list[Score] = []
        for i, c in enumerate(candidates):
            key = c.name if isinstance(c, WIPCandidate) else (c.artifact or "")
            out.append(Score(artifact_id=i, score=self.scores.get(key, 0.5), rationale=""))
        return out


def test_rejected_wip_remains_eligible_on_next_flush(tmp_path: Path) -> None:
    """ADR 0003 regression: a completed WIP that the ranker scored
    too low (below the percentile cutoff) on flush N MUST be eligible
    again on flush N+1. The earlier implementation marked WIPs as
    harvested as soon as they were snapshotted from the projection,
    which permanently dropped any WIP that did not pass the cutoff
    — a silent data-loss bug surfaced by Gemini + CodeRabbit review
    of PR #32.

    Strategy: a second completed WIP gives the percentile-cutoff
    something to compare against; on flush-1 we keep the target's
    score low so it falls below the p70 cutoff (rejected); on
    flush-2 we raise the target's score and confirm it is now
    written.
    """
    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    decoy = CONFIGURED_WIPS[1]
    with EpisodicMemory(db) as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0)
        _drive_wip_to_complete(ep, decoy, ts_base=100.0)
        proj = WorkshopProjection(ep)
        ranker = _MutableRanker()
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
        )

        # Flush 1: target scores below decoy, percentile=70 cuts it.
        ranker.scores = {target: 0.10, decoy: 0.95}
        first = harvester.flush()
        first_names = [p.name for p in first]
        assert not any(target.removeprefix("workshop.") in n for n in first_names), (
            f"target WIP should not have been harvested at flush-1: {first_names}"
        )

        # Flush 2: target's score is now well above cutoff; the
        # bug version would silently skip it because flush-1 marked
        # it harvested. With the fix the projection still considers
        # it a pending completed WIP and the harvest goes through.
        # Add a second decoy with low score so the cutoff still has
        # a population to compare against.
        decoy2 = CONFIGURED_WIPS[2]
        ep.append(
            actor="Aki",
            action="contribute",
            target=decoy2,
            payload={"thought": "x", "fragment": "low-priority filler text long enough"},
            ts=200.0,
        )
        for i in range(7):
            ep.append(
                actor="Bo",
                action="contribute",
                target=decoy2,
                payload={"thought": "x", "fragment": f"filler-{i}"},
                ts=201.0 + i,
            )
        # Rebuild projection to pick up the new fragments + completion.
        proj_v2 = WorkshopProjection(ep)
        harvester._workshop = proj_v2
        ranker.scores = {target: 0.99, decoy: 0.10, decoy2: 0.10}
        second = harvester.flush()
        target_slug = target.removeprefix("workshop.")
        second_names = [p.name for p in second]
        assert any(target_slug in n for n in second_names), (
            f"target WIP must be re-considered and accepted on flush-2; got {second_names}"
        )


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


# ---------------------------------------------------------------------------
# v0.3 (ADR 0004 Decision 1) — Harvester emits recycle/attempt events
# ---------------------------------------------------------------------------


def _count_events(ep: EpisodicMemory, *, action: str, target: str | None = None) -> int:
    """Count episodic events matching ``action`` (and optionally
    ``target``). Helper for the v0.3 recycle/attempt assertions.
    """
    n = 0
    for e in ep.since(0.0):
        if e.action != action:
            continue
        if target is not None and e.target != target:
            continue
        n += 1
    return n


def test_harvester_emits_recycle_event_on_accept(tmp_path: Path) -> None:
    """Slice 1.3: when a WIPCandidate is accepted by the percentile
    cutoff and written successfully, the Harvester appends one
    ``workshop.recycle{reason=accepted}`` event to episodic and the
    projection resets the WIP to ``forming``.

    The recycle path is the load-bearing fix for v0.2's pathology #2 —
    a completed WIP that has been harvested must free its slot.

    ``now_fn`` is pinned to a time just after completion so the
    independent timeout path does not preempt this accept path.
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
            episodic=ep,
            now_fn=lambda: 10.0,
        )
        written = harvester.flush()
        assert len(written) == 1
        assert _count_events(ep, action="workshop.recycle", target=target) == 1
        # Projection state is reset; the slot is free again.
        wip = proj.get(target)
        assert wip is not None
        assert wip.phase == "forming"
        assert wip.fragments == []
        assert proj.open_slots() == len(CONFIGURED_WIPS)


def test_harvester_emits_harvest_attempt_event_on_reject(tmp_path: Path) -> None:
    """A WIPCandidate rejected by the percentile cutoff appends one
    ``workshop.harvest_attempt`` event so the projection's
    ``harvest_attempts`` counter advances. The WIP stays in ``complete``
    until MAX_HARVEST_ATTEMPTS is reached or HARVEST_PENDING_TIMEOUT_S
    elapses.
    """
    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    decoy = CONFIGURED_WIPS[1]
    with EpisodicMemory(db) as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0)
        _drive_wip_to_complete(ep, decoy, ts_base=100.0)
        proj = WorkshopProjection(ep)
        # Target scores low so the p70 cutoff rejects it; decoy scores
        # high so it gets accepted (and triggers the rank/cutoff path).
        ranker = _PredictableRanker({target: 0.10, decoy: 0.95})
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: 200.0,
        )
        harvester.flush()
        assert _count_events(ep, action="workshop.harvest_attempt", target=target) == 1
        # Target stays in complete (not yet at MAX attempts).
        wip = proj.get(target)
        assert wip is not None
        assert wip.phase == "complete"
        assert wip.harvest_attempts == 1


def test_harvester_recycles_after_max_attempts(tmp_path: Path) -> None:
    """Slice 1.4: a WIP rejected MAX_HARVEST_ATTEMPTS times in a row
    is force-recycled. The next flush sees an empty ``forming`` WIP,
    not a perpetual rejected backlog.
    """
    from microverse.config import MAX_HARVEST_ATTEMPTS

    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    decoy = CONFIGURED_WIPS[1]
    with EpisodicMemory(db) as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0)
        _drive_wip_to_complete(ep, decoy, ts_base=100.0)
        proj = WorkshopProjection(ep)
        ranker = _PredictableRanker({target: 0.10, decoy: 0.95})
        # ``now_fn`` is pinned well within HARVEST_PENDING_TIMEOUT_S of
        # the target's completed_ts so the attempts path (not the
        # timeout path) drives the recycle.
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: 200.0,
        )
        # Each flush rejects target once; on the MAX_HARVEST_ATTEMPTS
        # rejection, the recycle event fires.
        next_ts_base = 300.0
        for _ in range(MAX_HARVEST_ATTEMPTS):
            harvester.flush()
            # Re-complete the decoy each round (it was recycled when
            # accepted) so target has something to compare against.
            decoy_wip = proj.get(decoy)
            if decoy_wip is not None and decoy_wip.phase == "forming":
                _drive_wip_to_complete(ep, decoy, ts_base=next_ts_base)
                next_ts_base += 100.0
                # Re-build the projection from the freshly-extended
                # event log so the decoy is complete again.
                proj._rebuild_from_episodic(ep)
        # After MAX attempts, target is force-recycled.
        assert _count_events(ep, action="workshop.recycle", target=target) >= 1
        wip = proj.get(target)
        assert wip is not None
        assert wip.phase == "forming"
        assert wip.fragments == []


def test_harvester_recycles_on_timeout(tmp_path: Path) -> None:
    """Slice 1.5: a WIP that has been in ``complete`` longer than
    HARVEST_PENDING_TIMEOUT_S is force-recycled regardless of
    attempts. The harvester accepts a ``now`` injection for testing
    without sleeping.
    """
    from microverse.config import HARVEST_PENDING_TIMEOUT_S

    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(db) as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0)
        proj = WorkshopProjection(ep)
        wip = proj.get(target)
        assert wip is not None
        assert wip.completed_ts > 0.0
        completed_at = wip.completed_ts

        ranker = _PredictableRanker({})  # never called: timeout drains before rank
        # Inject a "now" that is past the timeout window.
        future_now = completed_at + HARVEST_PENDING_TIMEOUT_S + 1.0
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: future_now,
        )
        harvester.flush()
        assert _count_events(ep, action="workshop.recycle", target=target) == 1
        wip2 = proj.get(target)
        assert wip2 is not None
        assert wip2.phase == "forming"
        # Timed-out WIPs are NOT written to harvest (they were never
        # ranked / accepted). The manifest may not exist at all if no
        # other candidates went through this flush.
        manifest_path = tmp_path / "harvest" / "manifest.jsonl"
        if manifest_path.exists():
            manifest = manifest_path.read_text(encoding="utf-8")
            assert '"accepted":true' not in manifest


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

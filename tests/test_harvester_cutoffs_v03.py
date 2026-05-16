"""v0.3 (ADR 0004 Decision 4) — separate Trader cutoffs for artifacts
and WIPs, plus a load-bearing contributor subfloor.

Slice 4.1: a flush mixing artifacts and WIPs uses two cutoffs.
Slice 4.2: a WIP at 0.55 with 2 contributors → accepted.
Slice 4.3: a WIP with <2 contributors → rejected with the
            ``wip_contributor_subfloor`` metric regardless of score.
Slice 4.4: a WIP at 0.54 with 2 contributors → rejected.

The contributor subfloor is the load-bearing guard: the actual goal
is cross-agent dialogue, not "long fragments." A solo padded WIP
clearing the absolute floor by length alone is not the artifact we
want to harvest.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.harvester import ArtifactCandidate, Harvester, WIPCandidate
from microverse.agents.trader import Score
from microverse.config import WIP_ACCEPTANCE_FLOOR
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.world.workshop import CONFIGURED_WIPS, WorkshopProjection


class _FixedRanker:
    """Returns a fixed score per candidate-name key. WIPs key on
    ``cand.name``; artifacts key on ``cand.artifact``.
    """

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def rank(self, candidates: list) -> list[Score]:
        out: list[Score] = []
        for i, c in enumerate(candidates):
            key = c.name if isinstance(c, WIPCandidate) else (c.artifact or "")
            out.append(Score(artifact_id=i, score=self._scores.get(key, 0.5), rationale=""))
        return out


def _drive_wip_to_complete(ep: EpisodicMemory, wip: str, *, ts_base: float, solo: bool) -> None:
    """Append 8 fragments to complete ``wip``. When ``solo=True`` all
    fragments come from Aki; when False they alternate Aki/Bo for two
    contributors.
    """
    for i in range(8):
        actor = "Aki" if (solo or i % 2 == 0) else "Bo"
        ep.append(
            actor=actor,
            action="contribute",
            target=wip,
            payload={"thought": "x", "fragment": f"frag-{i}-from-{actor}-padded-to-length"},
            ts=ts_base + i,
        )


def test_wip_acceptance_floor_constant_is_055() -> None:
    """ADR 0004 Decision 4: absolute floor sits at 0.55. The contributor
    subfloor is the structurally load-bearing guard; the floor itself
    is defence-in-depth.
    """
    assert WIP_ACCEPTANCE_FLOOR == 0.55


def test_wip_above_floor_with_two_contributors_accepted(tmp_path: Path) -> None:
    """A WIP scored at 0.60 with two distinct contributors clears
    both the absolute floor and the contributor subfloor → accepted.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0, solo=False)
        proj = WorkshopProjection(ep)
        ranker = _FixedRanker({target: 0.60})
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


def test_wip_below_floor_rejected(tmp_path: Path) -> None:
    """A WIP scored at 0.54 (just below the absolute floor) with two
    contributors is rejected.
    """
    target = CONFIGURED_WIPS[0]
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0, solo=False)
        proj = WorkshopProjection(ep)
        ranker = _FixedRanker({target: 0.54})
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: 10.0,
            metrics=metrics,
        )
        written = harvester.flush()
    assert len(written) == 0


def test_solo_wip_rejected_by_contributor_subfloor(tmp_path: Path) -> None:
    """A WIP with one contributor is rejected even when its score is
    well above the absolute floor. Bumps ``wip_contributor_subfloor``
    so operators can distinguish subfloor rejections from low-score
    rejections.
    """
    target = CONFIGURED_WIPS[0]
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0, solo=True)
        proj = WorkshopProjection(ep)
        wip = proj.get(target)
        assert wip is not None
        assert len(wip.contributors()) == 1  # solo by construction
        ranker = _FixedRanker({target: 0.95})  # well above the floor
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: 10.0,
            metrics=metrics,
        )
        written = harvester.flush()
    assert len(written) == 0
    assert metrics.get("wip_contributor_subfloor") >= 1


def test_artifacts_keep_percentile_cutoff(tmp_path: Path) -> None:
    """ADR 0004 Decision 4: the artifact pipeline does NOT change.
    Artifacts still use the percentile cutoff; the absolute floor is
    WIP-only.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # No WIPs in this test — only artifacts.
        proj = WorkshopProjection(ep)
        ranker = _FixedRanker({"artifact-low": 0.30, "artifact-mid": 0.55, "artifact-high": 0.90})
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: 10.0,
        )
        for k in ("artifact-low", "artifact-mid", "artifact-high"):
            harvester.consider(ArtifactCandidate(actor="Aki", action="craft", artifact=k, ts=1.0))
        written = harvester.flush()
    # p70 over [0.30, 0.55, 0.90] → cutoff is the 0.55 element →
    # accepts items >= 0.55. The 0.55 and 0.90 items both pass.
    assert len(written) >= 1


def test_mixed_flush_applies_two_cutoffs(tmp_path: Path) -> None:
    """A mixed flush: a WIP scored at 0.466 (below the WIP floor) and
    a high-scoring artifact. Confirms the WIP cutoff is INDEPENDENT
    of the artifact ranking — v0.2's conflation bug is the historical
    motivation for this test.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _drive_wip_to_complete(ep, target, ts_base=1.0, solo=False)
        proj = WorkshopProjection(ep)
        ranker = _FixedRanker({target: 0.466, "craft-artifact-text-long-enough": 0.95})
        harvester = Harvester(
            tmp_path / "harvest",
            trader=ranker,
            workshop=proj,
            percentile=70,
            episodic=ep,
            now_fn=lambda: 10.0,
        )
        harvester.consider(
            ArtifactCandidate(
                actor="Aki",
                action="craft",
                artifact="craft-artifact-text-long-enough",
                ts=1.0,
            )
        )
        written = harvester.flush()
    # The artifact passes (top p70 of a 2-item population is the max).
    # The WIP is rejected (0.466 < 0.55 floor).
    written_names = [str(p) for p in written]
    assert any("craft-artifact-text" in n for n in written_names)
    assert not any(target.removeprefix("workshop.") in n for n in written_names)

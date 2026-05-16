"""Watchdog: failure-mode detectors over the episodic log.

Phase 4a contract:
  - ``compute_diversity(events)`` returns 1 - mean pairwise Jaccard
    of agent action strings; 1.0 means maximally diverse.
  - ``Watchdog(metrics, episodic, scheduler).check()`` runs:
      runaway:    N consecutive identical actions per agent
      stagnation: recent artifact rate below threshold
      echo:       diversity below threshold → spawn a Stranger
      meta-leak:  leftover marker for the parse-time guard
    and bumps a metric counter per finding.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.ops.watchdog import Watchdog, compute_diversity
from microverse.world.scheduler import WeightedScheduler


class _StubAgent(Agent):
    role = "stub"
    persona_template = ""
    sampling: dict[str, float | int] = {}  # noqa: RUF012  pyright: ignore[reportGeneralTypeIssues]

    def think(self, world: WorldContext) -> Action:
        return Action(action=ActionKind.REST)


def _seed_actions(mem: EpisodicMemory, actor_actions: list[tuple[str, str]]) -> None:
    for actor, action in actor_actions:
        mem.append(actor=actor, action=action, target=None, payload={"thought": action})


def test_diversity_one_for_all_unique_actions():
    actions = ["craft a wooden bowl", "study the river", "speak to bo", "rest by the hearth"]
    assert compute_diversity(actions) > 0.8


def test_diversity_zero_for_identical_actions():
    actions = ["rest by the hearth"] * 5
    assert compute_diversity(actions) == 0.0


def test_diversity_empty_returns_one():
    """No data = no problem (assume diverse)."""
    assert compute_diversity([]) == 1.0


def test_diversity_single_returns_one():
    assert compute_diversity(["alone"]) == 1.0


def test_runaway_detected_for_consecutive_identical_actions(tmp_path: Path, metrics: Metrics):
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(
            ep,
            [("aki", "craft")] * 5 + [("bo", "speak")],
        )
        Watchdog(metrics=metrics, episodic=ep, scheduler=sched).check()
    assert metrics.get("watchdog_runaway", agent="aki") >= 1


def test_no_runaway_when_actions_vary(tmp_path: Path, metrics: Metrics):
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(ep, [("aki", "craft"), ("aki", "rest"), ("aki", "speak")])
        Watchdog(metrics=metrics, episodic=ep, scheduler=sched).check()
    assert metrics.get("watchdog_runaway", agent="aki") == 0


def test_echo_chamber_triggers_stranger_spawn(tmp_path: Path, metrics: Metrics):
    """When diversity drops below the threshold, the watchdog asks the
    scheduler to register a fresh Stranger agent."""
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # All identical actions across multiple actors → near-zero diversity.
        _seed_actions(
            ep,
            [("aki", "craft a wooden bowl")] * 10,
        )
        wd = Watchdog(metrics=metrics, episodic=ep, scheduler=sched, diversity_floor=0.35)
        wd.check()
    assert metrics.get("watchdog_echo_chamber") >= 1
    # A Stranger was registered in the scheduler.
    names = [a.name for a in sched.agents]
    assert any(n.startswith("stranger") for n in names)


def test_spawned_stranger_inherits_workshop_projection(tmp_path: Path, metrics: Metrics):
    """PR #33 review (Codex): Strangers registered mid-run by the
    Watchdog must inherit the same WorkshopProjection the startup
    roster holds, so ``parse_action`` hard-folds contributes to
    complete WIPs for every agent regardless of spawn time. Without
    this, a Stranger's first tick would have ``_workshop=None`` and
    could write into a locked WIP.
    """
    from microverse.world.workshop import WorkshopProjection

    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(ep, [("aki", "craft a wooden bowl")] * 10)
        workshop = WorkshopProjection(ep)
        wd = Watchdog(
            metrics=metrics,
            episodic=ep,
            scheduler=sched,
            workshop=workshop,
            diversity_floor=0.35,
        )
        wd.check()
    strangers = [a for a in sched.agents if a.name.startswith("stranger")]
    assert strangers, "expected at least one stranger to be spawned"
    for s in strangers:
        # Public surface: ``parse_action`` reads ``agent._workshop``
        # after ``attach_workshop`` stores it there.
        assert getattr(s, "_workshop", None) is workshop


def test_echo_chamber_quiet_when_diversity_high(tmp_path: Path, metrics: Metrics):
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(
            ep,
            [
                ("aki", "craft wooden bowl"),
                ("aki", "study the river current"),
                ("aki", "speak to the silver fish"),
                ("aki", "rest beside the willow"),
            ],
        )
        Watchdog(metrics=metrics, episodic=ep, scheduler=sched, diversity_floor=0.35).check()
    assert metrics.get("watchdog_echo_chamber") == 0


def test_stranger_pool_capped(tmp_path: Path, metrics: Metrics):
    """Repeated echo-chamber detections must not spawn unbounded Strangers."""
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(ep, [("aki", "craft a wooden bowl")] * 10)
        wd = Watchdog(
            metrics=metrics,
            episodic=ep,
            scheduler=sched,
            diversity_floor=0.35,
            max_strangers=2,
        )
        for _ in range(5):
            wd.check()
    strangers = [a for a in sched.agents if a.role == "stranger"]
    assert len(strangers) == 2  # cap honored
    assert metrics.get("watchdog_stranger_cap_hit") >= 1


def test_meta_leak_detector_bumps_per_actor(tmp_path: Path, metrics: Metrics):
    """The watchdog scans recent agent payloads for in-world meta-
    references and bumps a per-actor counter."""
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(
            actor="aki",
            action="speak",
            target=None,
            payload={"thought": "I am an AI inside this simulation"},
        )
        ep.append(
            actor="aki",
            action="speak",
            target=None,
            payload={"thought": "ordinary thought"},
        )
        Watchdog(metrics=metrics, episodic=ep, scheduler=sched).check()
    assert metrics.get("watchdog_meta_leak", agent="aki") >= 1


def test_stagnation_detected_when_no_recent_artifacts(tmp_path: Path, metrics: Metrics):
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # 50 rest actions, no artifacts.
        for _ in range(50):
            ep.append(actor="aki", action="rest", target=None, payload={"artifact": None})
        Watchdog(
            metrics=metrics,
            episodic=ep,
            scheduler=sched,
            stagnation_floor=1,
        ).check()
    assert metrics.get("watchdog_stagnation") >= 1


def test_check_excludes_harvest_events_from_agent_scope(tmp_path: Path, metrics: Metrics):
    """Layer-G Alt-B emits ``actor='harvest', action='rated'`` events
    in batches (one per ranked candidate during ``Harvester.flush()``).
    These are exogenous feedback, not agent actions — the watchdog
    must not flag a flush burst as runaway, stagnation, or
    echo-chamber, and must not spawn a Stranger from it.
    """
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # Six harvest 'rated' events back-to-back: a Trader-flush burst.
        for i in range(6):
            ep.append(
                actor="harvest",
                action="rated",
                target=None,
                payload={
                    "actor": "aki",
                    "kind": "craft",
                    "score": 0.5 + i / 100,
                    "accepted": True,
                },
            )
        Watchdog(
            metrics=metrics,
            episodic=ep,
            scheduler=sched,
            runaway_max_consecutive=4,
            diversity_floor=0.35,
        ).check()
    assert metrics.get("watchdog_runaway", agent="harvest") == 0, (
        "harvest events must not contribute to runaway detection"
    )
    assert metrics.get("watchdog_echo_chamber") == 0, (
        "harvest 'rated' batches must not trigger echo chamber"
    )
    strangers = [a for a in sched.agents if getattr(a, "role", None) == "stranger"]
    assert strangers == [], "no Stranger spawn from a harvest flush burst"

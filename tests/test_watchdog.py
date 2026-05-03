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


def test_runaway_detected_for_consecutive_identical_actions(tmp_path: Path):
    metrics = Metrics(":memory:")
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(
            ep,
            [("aki", "craft")] * 5 + [("bo", "speak")],
        )
        Watchdog(metrics=metrics, episodic=ep, scheduler=sched).check()
    assert metrics.get("watchdog_runaway", agent="aki") >= 1


def test_no_runaway_when_actions_vary(tmp_path: Path):
    metrics = Metrics(":memory:")
    sched = WeightedScheduler()
    sched.register(_StubAgent("aki"))
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_actions(ep, [("aki", "craft"), ("aki", "rest"), ("aki", "speak")])
        Watchdog(metrics=metrics, episodic=ep, scheduler=sched).check()
    assert metrics.get("watchdog_runaway", agent="aki") == 0


def test_echo_chamber_triggers_stranger_spawn(tmp_path: Path):
    """When diversity drops below the threshold, the watchdog asks the
    scheduler to register a fresh Stranger agent."""
    metrics = Metrics(":memory:")
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


def test_echo_chamber_quiet_when_diversity_high(tmp_path: Path):
    metrics = Metrics(":memory:")
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


def test_stagnation_detected_when_no_recent_artifacts(tmp_path: Path):
    metrics = Metrics(":memory:")
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

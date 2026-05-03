"""Tests for microverse.world.scheduler.RoundRobinScheduler."""

from __future__ import annotations

from typing import ClassVar

import pytest

from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.world.scheduler import RoundRobinScheduler


class _StubAgent(Agent):
    role = "stub"
    persona_template = ""
    sampling: ClassVar[dict[str, float | int]] = {}

    def think(self, world: WorldContext) -> Action:
        return Action(action=ActionKind.REST)


def test_empty_scheduler_raises():
    sched = RoundRobinScheduler()
    with pytest.raises(LookupError):
        sched.next()


def test_single_agent_returns_same_each_tick():
    sched = RoundRobinScheduler()
    a = _StubAgent("solo")
    sched.register(a)
    assert sched.next() is a
    assert sched.next() is a


def test_round_robin_cycles_in_registration_order():
    sched = RoundRobinScheduler()
    a = _StubAgent("a")
    b = _StubAgent("b")
    c = _StubAgent("c")
    for ag in (a, b, c):
        sched.register(ag)
    picks = [sched.next() for _ in range(7)]
    assert picks == [a, b, c, a, b, c, a]


def test_register_duplicate_name_rejected():
    sched = RoundRobinScheduler()
    sched.register(_StubAgent("a"))
    with pytest.raises(ValueError):
        sched.register(_StubAgent("a"))


def test_unregister_removes_from_rotation():
    sched = RoundRobinScheduler()
    a = _StubAgent("a")
    b = _StubAgent("b")
    sched.register(a)
    sched.register(b)
    sched.next()  # pick a
    sched.unregister("a")
    # rotation continues with the remaining agent.
    assert sched.next() is b
    assert sched.next() is b


def test_agents_property_lists_all_registered():
    sched = RoundRobinScheduler()
    a = _StubAgent("a")
    b = _StubAgent("b")
    sched.register(a)
    sched.register(b)
    assert sched.agents == (a, b)


def test_round_robin_satisfies_scheduler_protocol():
    """RoundRobinScheduler must satisfy the runtime-checkable Scheduler
    Protocol so future swaps (e.g. WeightedScheduler in Phase 2) are
    typecheck-verified, not runtime-discovered."""
    from microverse.world.scheduler import Scheduler

    sched = RoundRobinScheduler()
    assert isinstance(sched, Scheduler)


# ---------------------------------------------------------------------
# WeightedScheduler (Phase 2): pick by soul_tokens, deterministic with
# a seeded RNG so tests can assert distributions.
# ---------------------------------------------------------------------


def _new_weighted(seed: int = 0):
    import random as _random

    from microverse.world.scheduler import WeightedScheduler

    return WeightedScheduler(rng=_random.Random(seed))


def test_weighted_empty_raises():
    sched = _new_weighted()
    with pytest.raises(LookupError):
        sched.next()


def test_weighted_single_agent_always_returned():
    sched = _new_weighted()
    a = _StubAgent("solo")
    sched.register(a)
    for _ in range(20):
        assert sched.next() is a


def test_weighted_distribution_follows_soul_tokens():
    """With weights [10, 1] over 1100 picks, the heavier agent should
    be chosen roughly 10x as often as the lighter one. Allow a 25%
    margin around the expected ratio so the test is flake-resistant."""
    sched = _new_weighted(seed=42)
    a = _StubAgent("a")
    a.soul_tokens = 10
    b = _StubAgent("b")
    b.soul_tokens = 1
    sched.register(a)
    sched.register(b)

    counts = {"a": 0, "b": 0}
    for _ in range(1100):
        counts[sched.next().name] += 1

    ratio = counts["a"] / counts["b"]
    assert 7.5 <= ratio <= 12.5, counts


def test_weighted_zero_soul_tokens_still_eligible():
    """An agent with soul_tokens=0 must still be reachable (we use
    max(tokens, 1) as the floor) so a fresh-spawned agent isn't
    permanently silenced before its first tick."""
    sched = _new_weighted(seed=1)
    a = _StubAgent("a")
    a.soul_tokens = 0
    b = _StubAgent("b")
    b.soul_tokens = 0
    sched.register(a)
    sched.register(b)
    seen = {sched.next().name for _ in range(50)}
    assert seen == {"a", "b"}


def test_weighted_satisfies_scheduler_protocol():
    from microverse.world.scheduler import Scheduler

    sched = _new_weighted()
    assert isinstance(sched, Scheduler)


def test_weighted_unregister_removes_from_pool():
    sched = _new_weighted(seed=2)
    a = _StubAgent("a")
    b = _StubAgent("b")
    sched.register(a)
    sched.register(b)
    sched.unregister("a")
    for _ in range(10):
        assert sched.next() is b


def test_weighted_register_duplicate_rejected():
    sched = _new_weighted()
    sched.register(_StubAgent("a"))
    with pytest.raises(ValueError):
        sched.register(_StubAgent("a"))

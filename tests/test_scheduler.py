"""Tests for microverse.world.scheduler.RoundRobinScheduler."""

from __future__ import annotations

import pytest

from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.world.scheduler import RoundRobinScheduler


class _StubAgent(Agent):
    role = "stub"
    persona_template = ""
    sampling: dict[str, float | int] = {}

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

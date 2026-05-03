"""Tick scheduler.

Phase 1 ships ``RoundRobinScheduler`` — deterministic rotation in
registration order. Phase 2 adds ``WeightedScheduler``, a randomized
pick weighted by ``soul_tokens`` so dying agents get fewer turns and
fresh ``Stranger`` immigrants (Phase 4) get baseline visibility. Both
satisfy the ``Scheduler`` Protocol so the tick loop is unchanged.
"""

from __future__ import annotations

import random
from typing import Protocol, runtime_checkable

from microverse.agents.base import Agent


@runtime_checkable
class Scheduler(Protocol):
    """The interface the tick loop expects from a scheduler.

    Implementations must accept agents at any time, return the next
    agent to act, and support deregistration so the watchdog can pull
    a misbehaving agent out of rotation.
    """

    @property
    def agents(self) -> tuple[Agent, ...]: ...

    def register(self, agent: Agent) -> None: ...

    def unregister(self, name: str) -> None: ...

    def next(self) -> Agent: ...


class RoundRobinScheduler:
    """Deterministic round-robin over registered agents."""

    def __init__(self) -> None:
        self._agents: list[Agent] = []
        self._cursor = 0

    @property
    def agents(self) -> tuple[Agent, ...]:
        return tuple(self._agents)

    def register(self, agent: Agent) -> None:
        if any(a.name == agent.name for a in self._agents):
            raise ValueError(f"duplicate agent name: {agent.name!r}")
        self._agents.append(agent)

    def unregister(self, name: str) -> None:
        idx = next(
            (i for i, a in enumerate(self._agents) if a.name == name),
            None,
        )
        if idx is None:
            raise LookupError(f"no agent named {name!r}")
        self._agents.pop(idx)
        if self._cursor > idx:
            self._cursor -= 1
        if self._agents and self._cursor >= len(self._agents):
            self._cursor %= len(self._agents)

    def next(self) -> Agent:
        if not self._agents:
            raise LookupError("scheduler has no registered agents")
        agent = self._agents[self._cursor]
        self._cursor = (self._cursor + 1) % len(self._agents)
        return agent


class WeightedScheduler:
    """Random pick weighted by ``soul_tokens``.

    A fresh agent (``soul_tokens=0``) still has a non-zero floor so it
    isn't silenced before its first tick — Phase 4's Stranger immigrant
    relies on this to gain a foothold. RNG is injectable so tests can
    assert distributions without flakes.
    """

    def __init__(self, *, rng: random.Random | None = None) -> None:
        self._agents: list[Agent] = []
        self._rng = rng if rng is not None else random.Random()

    @property
    def agents(self) -> tuple[Agent, ...]:
        return tuple(self._agents)

    def register(self, agent: Agent) -> None:
        if any(a.name == agent.name for a in self._agents):
            raise ValueError(f"duplicate agent name: {agent.name!r}")
        self._agents.append(agent)

    def unregister(self, name: str) -> None:
        idx = next(
            (i for i, a in enumerate(self._agents) if a.name == name),
            None,
        )
        if idx is None:
            raise LookupError(f"no agent named {name!r}")
        self._agents.pop(idx)

    def next(self) -> Agent:
        if not self._agents:
            raise LookupError("scheduler has no registered agents")
        # max(_, 1) so soul_tokens=0 still gets a baseline turn — the
        # watchdog reduces tokens, but never silences entirely.
        weights = [max(a.soul_tokens, 1) for a in self._agents]
        return self._rng.choices(self._agents, weights=weights, k=1)[0]

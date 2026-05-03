"""Tick scheduler.

Phase 1 ships a simple ``RoundRobinScheduler`` — pick agents in
registration order. Phase 2 swaps in a weighted scheduler keyed by
``soul_tokens``; the public ``Scheduler.next()`` interface stays the
same so the tick loop is unchanged.
"""

from __future__ import annotations

from microverse.agents.base import Agent


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

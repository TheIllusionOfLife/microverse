"""Watchdog: failure-mode detectors over the episodic log.

Phase 4a contract:
  - ``compute_diversity(actions)`` — 1 - mean pairwise Jaccard over
    the action strings. Empty/single-input returns 1.0 (no signal,
    assume diverse so the echo-chamber detector doesn't trigger
    spuriously on cold start).
  - ``Watchdog(metrics, episodic, scheduler).check()`` runs detectors:
      * runaway_max_consecutive   — N identical actions per agent in a row
      * stagnation_floor          — recent artifact rate floor
      * diversity_floor           — echo chamber → spawn Stranger
    Each detector bumps a metric so the Phase 4b dashboard can report.

Detectors are read-only with one exception: echo-chamber registers a
new Stranger in the scheduler. That mutation is intentional — the
watchdog is the authority on rehab.
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING

from microverse._text import jaccard, tokenize

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory
    from microverse.ops.metrics import Metrics
    from microverse.world.scheduler import Scheduler

_logger = logging.getLogger(__name__)


def compute_diversity(actions: list[str]) -> float:
    """1 - mean pairwise Jaccard. 1.0 = maximally diverse."""
    n = len(actions)
    if n < 2:
        return 1.0
    token_sets = [tokenize(a, min_len=3) for a in actions]
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += jaccard(token_sets[i], token_sets[j])
            pairs += 1
    mean = total / pairs if pairs else 0.0
    return 1.0 - mean


class Watchdog:
    """Read the recent episodic log; bump metrics; spawn Strangers."""

    def __init__(
        self,
        *,
        metrics: Metrics,
        episodic: EpisodicMemory,
        scheduler: Scheduler,
        runaway_max_consecutive: int = 4,
        stagnation_window: int = 50,
        stagnation_floor: int = 1,
        diversity_floor: float = 0.35,
        diversity_window: int = 20,
        max_strangers: int = 3,
    ) -> None:
        self._metrics = metrics
        self._episodic = episodic
        self._scheduler = scheduler
        self._runaway_max = runaway_max_consecutive
        self._stagnation_window = stagnation_window
        self._stagnation_floor = stagnation_floor
        self._diversity_floor = diversity_floor
        self._diversity_window = diversity_window
        self._max_strangers = max_strangers

    def check(self) -> None:
        recent = self._episodic.last(max(self._stagnation_window, self._diversity_window))
        # Skip world-emitted weather events — they aren't agent actions.
        agent_events = [e for e in recent if e.actor != "world"]

        self._check_runaway(agent_events)
        self._check_stagnation(agent_events)
        self._check_echo_chamber(agent_events)
        self._check_meta_leak(agent_events)

    def _check_meta_leak(self, events: list) -> None:
        # Lazy import to avoid a watchdog → agents → ... cycle.
        from microverse.agents.base import has_meta_leak

        for e in events:
            payload = e.payload or {}
            thought = str(payload.get("thought") or "")
            artifact = str(payload.get("artifact") or "")
            if has_meta_leak(thought) or has_meta_leak(artifact):
                self._metrics.bump("watchdog_meta_leak", agent=e.actor)

    def _check_runaway(self, events: list) -> None:
        # events are newest-first. Walk per-agent and count the longest
        # leading streak of identical actions.
        by_actor: dict[str, list[str]] = {}
        for e in events:
            by_actor.setdefault(e.actor, []).append(e.action)
        for actor, actions in by_actor.items():
            streak = 1
            for a, b in itertools.pairwise(actions):
                if a == b:
                    streak += 1
                else:
                    break
            if streak >= self._runaway_max:
                self._metrics.bump("watchdog_runaway", agent=actor)

    def _check_stagnation(self, events: list) -> None:
        window = events[: self._stagnation_window]
        artifacts = sum(1 for e in window if (e.payload or {}).get("artifact"))
        if window and artifacts < self._stagnation_floor:
            self._metrics.bump("watchdog_stagnation")

    def _check_echo_chamber(self, events: list) -> None:
        window = events[: self._diversity_window]
        actions_text = [f"{e.action} {(e.payload or {}).get('thought', '') or ''}" for e in window]
        diversity = compute_diversity(actions_text)
        # Record the current diversity snapshot (scaled to integer %)
        # so Phase 4b's dashboard can verify the "mean diversity ≥ 0.35"
        # acceptance criterion from the metrics DB.
        if len(actions_text) >= 2:
            self._metrics.set_value("watchdog_diversity_pct", round(diversity * 100))
        if len(actions_text) >= 2 and diversity < self._diversity_floor:
            self._metrics.bump("watchdog_echo_chamber")
            self._spawn_stranger()

    def _spawn_stranger(self) -> None:
        # Cap the Stranger pool: if we already have ``max_strangers``
        # registered, the existing ones haven't done their job — adding
        # more would amplify LLM volume without improving diversity.
        # ``getattr`` defensive: ``Agent.role`` is annotated but not
        # enforced at runtime, so a malformed registered agent would
        # otherwise crash the stranger spawn path.
        existing = sum(1 for a in self._scheduler.agents if getattr(a, "role", None) == "stranger")
        if existing >= self._max_strangers:
            self._metrics.bump("watchdog_stranger_cap_hit")
            return

        # Lazy import to avoid a watchdog → agents → ... cycle.
        from microverse.agents.stranger import Stranger

        stranger = Stranger(metrics=self._metrics)
        try:
            self._scheduler.register(stranger)
        except ValueError:
            # Name collision — extremely unlikely with ms+uuid names,
            # but harmless if it happens.
            _logger.info("Stranger %r already registered; skipping", stranger.name)

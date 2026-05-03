"""Seeded weather/event scheduler for the microverse.

Phase 4a: agents need *physics*. The clock periodically writes
``weather.drought``, ``weather.comet``, ``weather.festival`` (etc.)
events into the episodic log so the build_context layer surfaces them
to inhabitants — who interpret the events as natural phenomena, not as
script. The schedule is RNG-driven but seedable, so a `--seed`-pinned
run is fully deterministic from agents through to weather.

Contract:
  - ``WorldClock(seed, mean_interval)`` constructs a stream.
  - ``advance(episodic, ticks_elapsed)`` consumes ``ticks_elapsed`` from
    an internal countdown; when the countdown hits zero, write one
    event and re-roll the next interval.

Events appear with ``actor='world'`` and ``action='weather.<kind>'``
so build_context can filter them in or out, and so they don't pollute
diversity metrics computed over agent actions.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory

KNOWN_EVENTS: frozenset[str] = frozenset(
    {
        "weather.drought",
        "weather.comet",
        "weather.festival",
        "weather.storm",
        "weather.bloom",
    }
)


class WorldClock:
    """Deterministic-when-seeded weather scheduler.

    ``seed=None`` (the default) draws OS entropy — same convention as
    the agent RNG, so an unseeded production run gets a fresh weather
    sequence each launch instead of always replaying the same one.
    Pass an explicit int (including ``0``) to make the run reproducible.
    """

    def __init__(self, seed: int | None = None, *, mean_interval: int = 200) -> None:
        if mean_interval <= 0:
            raise ValueError("mean_interval must be positive")
        # `random.Random(None)` seeds from os.urandom, which is what we
        # want for an unseeded run; `random.Random(0)` is fully fixed.
        self._rng = random.Random(seed)
        self._mean_interval = mean_interval
        self._kinds = sorted(KNOWN_EVENTS)
        self._countdown = self._next_interval()

    def _next_interval(self) -> int:
        # Uniform around mean. Avoid 0 so we can't fire the same tick twice.
        lo = max(1, self._mean_interval // 2)
        hi = self._mean_interval * 2
        return self._rng.randint(lo, hi)

    def advance(self, episodic: EpisodicMemory, *, ticks_elapsed: int) -> int:
        """Consume ``ticks_elapsed`` ticks; emit any events that fire.

        Returns the number of events emitted (usually 0 or 1 per call).
        """
        if ticks_elapsed <= 0:
            return 0
        emitted = 0
        remaining = ticks_elapsed
        while remaining >= self._countdown:
            remaining -= self._countdown
            kind = self._rng.choice(self._kinds)
            short = kind.removeprefix("weather.")
            episodic.append(
                actor="world",
                action=kind,
                target=None,
                payload={"kind": short, "source": "WorldClock"},
            )
            emitted += 1
            self._countdown = self._next_interval()
        self._countdown -= remaining
        return emitted

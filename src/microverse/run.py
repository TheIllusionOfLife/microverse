"""Microverse tick loop entrypoint.

Run with::

    uv run python -m microverse.run                   # infinite, real Ollama
    uv run python -m microverse.run --ticks 30        # bounded
    uv run python -m microverse.run --tempo 0         # no sleep between ticks
    uv run python -m microverse.run --seed 42

Environment overrides:
    MICROVERSE_DATA      override data/ directory (episodic, metrics, snapshots)
    MICROVERSE_HARVEST   override harvest/ directory (artifact inbox)

SIGINT exits cleanly. SIGKILL is recoverable — the SQLite-WAL contract
in :mod:`microverse.memory.episodic` guarantees no committed event is
lost; the in-flight tick is simply discarded.

Phase 2 wiring:
  - ``Artisan`` is the only agent registered in the ``WeightedScheduler``
    (seeded if ``--seed`` given). ``Trader`` is constructed but
    intentionally not scheduled; it ranks artifacts only when
    ``Harvester.flush()`` calls its ``rank()``.
  - Harvester is constructed with the Trader so ``consider()`` buffers
    candidates and ``flush()`` applies p70 percentile selection. The
    tick loop calls ``flush()`` every ``HARVEST_FLUSH_EVERY`` ticks.
  - Cold-backup snapshots taken every ``SNAPSHOT_EVERY`` ticks via
    :func:`microverse.world.snapshot.maybe_snapshot`.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from microverse import config
from microverse.agents.artisan import Artisan
from microverse.agents.base import Action, Agent, WorldContext
from microverse.agents.harvester import ArtifactCandidate, Harvester
from microverse.agents.trader import Trader
from microverse.config import MAX_TICKS_DEFAULT
from microverse.memory import build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory
from microverse.ops.metrics import Metrics
from microverse.ops.watchdog import Watchdog
from microverse.world.clock import WorldClock
from microverse.world.scheduler import WeightedScheduler
from microverse.world.snapshot import SnapshotBusyError, maybe_snapshot

_logger = logging.getLogger(__name__)

# Phase 2 cadences.
HARVEST_FLUSH_EVERY = 50  # ticks between Trader-driven harvest flushes
SNAPSHOT_EVERY = 1000  # cold backups; WAL handles real durability

# Phase 4a cadences.
WATCHDOG_EVERY = 25  # ticks between watchdog sweeps
WORLD_CLOCK_MEAN_INTERVAL = 100  # mean ticks between weather events


def _all_agents_paused(metrics: Metrics, agents: Sequence[Agent]) -> bool:
    """True iff every registered agent is currently paused.

    Used by the tick loop to decide when to fire the deadlock-break
    rehab path. Checking *all* (rather than relying on a skip-count
    heuristic) prevents a single persistently-failing agent from
    pulling the entire roster's counters back to zero, which would
    mask legitimate per-agent failures.
    """
    return all(metrics.should_pause(a.name) for a in agents)


def _derive_topic(episodic: EpisodicMemory, agent: Agent) -> str:
    """Pick a scene-topic for FTS5 lore retrieval.

    Strategy: use the most recent ``weather.*`` event's kind as a topic
    word (so during a drought, the agent's lore_excerpt prefers
    drought-tagged lore). If no weather has happened yet, fall back to
    the agent's role plus the agent's name so lore retrieval is at
    least seeded with something.
    """
    for e in episodic.last(50):
        if e.actor == "world" and e.action.startswith("weather."):
            return f"{e.action.removeprefix('weather.')} {agent.role}"
    return f"{agent.role} {agent.name}"


def _commit_action(episodic: EpisodicMemory, agent: Agent, action: Action) -> int:
    return episodic.append(
        actor=agent.name,
        action=action.action.value,
        target=action.target,
        payload={
            "thought": action.thought,
            "artifact": action.artifact,
            "role": agent.role,
        },
    )


def _maybe_harvest(harvester: Harvester, agent: Agent, action: Action) -> None:
    if not action.artifact:
        return
    candidate = ArtifactCandidate(
        actor=agent.name,
        action=action.action.value,
        artifact=action.artifact,
        ts=time.time(),
    )
    harvester.consider(candidate)


def run(
    *,
    ticks: int | None = None,
    seed: int | None = None,
    tempo: float | None = None,
    data_dir: str | Path | None = None,
    harvest_dir: str | Path | None = None,
) -> int:
    """Run the tick loop. Returns the number of ticks executed."""
    rng = random.Random(seed) if seed is not None else random.Random()

    data_dir = (
        Path(data_dir) if data_dir is not None else Path(os.environ.get("MICROVERSE_DATA", "data"))
    )
    harvest_dir = (
        Path(harvest_dir)
        if harvest_dir is not None
        else Path(os.environ.get("MICROVERSE_HARVEST", "harvest"))
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    harvest_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = data_dir / "snapshots"

    episodic = EpisodicMemory(data_dir / "episodic.sqlite")
    semantic = SemanticMemory(data_dir / "semantic.sqlite")
    metrics = Metrics(data_dir / "metrics.sqlite", auto_flush_every=10)

    trader = Trader(name="Bo", soul_tokens=30)
    harvester = Harvester(harvest_dir, trader=trader, percentile=70)

    sched = WeightedScheduler(rng=rng)
    sched.register(Artisan(name="Aki", metrics=metrics, soul_tokens=100))
    # Trader scheduling is internal — it ranks the buffer at flush time,
    # not as a tick action. We don't register it in the scheduler.

    clock = WorldClock(seed=seed, mean_interval=WORLD_CLOCK_MEAN_INTERVAL)
    watchdog = Watchdog(metrics=metrics, episodic=episodic, scheduler=sched)

    stop = {"requested": False}

    def _on_signal(_signum: int, _frame: object) -> None:
        stop["requested"] = True

    # Catch SIGINT (Ctrl-C) AND SIGTERM (e.g. `timeout`, supervisor kills,
    # systemd stop) so the finally block runs and the harvester buffer
    # gets a final flush. SIGKILL is still recoverable via WAL — the
    # in-flight tick is discarded but committed events are intact.
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    max_ticks = ticks if ticks is not None else MAX_TICKS_DEFAULT
    executed = 0
    consecutive_skips = 0
    # Bounded by config.MAX_CONSECUTIVE_DEADLOCK_BREAKS — see run.py:230
    # for the exit path. Reset on any successful think() at line 222.
    deadlock_breaks_since_success = 0

    def _safe(label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except Exception:
            _logger.exception("%s failed", label)

    try:
        while executed < max_ticks and not stop["requested"]:
            agent = sched.next()
            if metrics.should_pause(agent.name):
                consecutive_skips += 1
                # Two-stage guard: the skip counter throttles how often
                # we even consider rehab (cheap O(1)), but the actual
                # reset only fires when *every* registered agent is
                # paused. Otherwise a single persistently-failing agent
                # would pull un-paused agents' counters back to zero,
                # masking their real failures.
                if consecutive_skips > max(len(sched.agents), 1) * 3:
                    if _all_agents_paused(metrics, sched.agents):
                        time.sleep(max(tempo or 1.0, 1.0))
                        for a in sched.agents:
                            if metrics.should_pause(a.name):
                                metrics.reset("consecutive_fail", agent=a.name)
                        deadlock_breaks_since_success += 1
                        if deadlock_breaks_since_success >= config.MAX_CONSECUTIVE_DEADLOCK_BREAKS:
                            metrics.bump("deadlock_break_exit")
                            _logger.error(
                                "all agents stuck after %d consecutive deadlock-breaks — exiting",
                                deadlock_breaks_since_success,
                            )
                            break
                    consecutive_skips = 0
                continue
            consecutive_skips = 0
            # Topic depends on agent.role, so derive per-tick: Watchdog
            # may spawn Strangers mid-run and a cached topic from the
            # initial agent would mis-tag their lore retrieval.
            topic = _derive_topic(episodic, agent)
            world = build_context(
                world_base=WorldContext(),
                episodic=episodic,
                semantic=semantic,
                topic=topic,
            )
            try:
                action = agent.think(world)
            except Exception:
                _logger.exception("think() failed for agent %s", agent.name)
                metrics.bump("llm_timeout")
                metrics.bump("consecutive_fail", agent=agent.name)
                time.sleep(min(max(tempo or 1.0, 1.0), 5.0))
                continue
            metrics.reset("consecutive_fail", agent=agent.name)
            deadlock_breaks_since_success = 0
            _commit_action(episodic, agent, action)
            _maybe_harvest(harvester, agent, action)
            executed += 1

            # World clock + watchdog: cheap, run every tick / every Nth.
            _safe("WorldClock.advance", lambda: clock.advance(episodic, ticks_elapsed=1))
            if executed % WATCHDOG_EVERY == 0:
                _safe("watchdog.check", watchdog.check)

            if executed % HARVEST_FLUSH_EVERY == 0:
                try:
                    harvester.flush()
                except Exception:
                    _logger.exception("harvester.flush failed")
                    metrics.bump("harvest_flush_fail")
            try:
                maybe_snapshot(
                    executed,
                    interval=SNAPSHOT_EVERY,
                    data_dir=data_dir,
                    snapshots_dir=snapshots_dir,
                )
            except SnapshotBusyError:
                # A competing writer prevented wal_checkpoint(TRUNCATE)
                # from completing cleanly. Skip this interval rather
                # than ship a possibly-torn archive; the next interval
                # will try again.
                _logger.warning("snapshot skipped: WAL checkpoint busy")
                metrics.bump("snapshot_skip_busy")

            sleep_s = tempo if tempo is not None else agent.tempo()
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        # Final flush so any buffered candidates from the last partial
        # batch still go through Trader on shutdown. If it fails (the
        # most common cause: a hung Ollama call interrupted by SIGTERM),
        # bump a counter BEFORE closing metrics so the failure is
        # visible in the metrics db. The bump itself is wrapped because
        # metrics may already be in a bad state.
        try:
            harvester.flush()
        except Exception:
            _logger.exception("final harvester.flush failed")
            _safe("metrics.bump after flush failure", lambda: metrics.bump("harvest_flush_fail"))
        _safe("metrics.close", metrics.close)
        _safe("episodic.close", episodic.close)
        _safe("semantic.close", semantic.close)

    return executed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="microverse.run", description=__doc__)
    p.add_argument("--ticks", type=int, default=None, help="Run for N ticks then exit.")
    p.add_argument("--seed", type=int, default=None, help="RNG seed.")
    p.add_argument(
        "--tempo",
        type=float,
        default=None,
        help="Override per-agent sleep (seconds). Use 0 for no sleep (tests).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run(ticks=args.ticks, seed=args.seed, tempo=args.tempo)


if __name__ == "__main__":
    sys.exit(0 if main() >= 0 else 1)

"""Microverse tick loop entrypoint.

Run with::

    uv run python -m microverse.run                   # infinite, real Ollama
    uv run python -m microverse.run --ticks 30        # bounded
    uv run python -m microverse.run --tempo 0         # no sleep between ticks
    uv run python -m microverse.run --seed 42

Environment overrides:
    MICROVERSE_DATA      override data/ directory (episodic, metrics)
    MICROVERSE_HARVEST   override harvest/ directory (artifact inbox)

SIGINT exits cleanly. SIGKILL is recoverable — the SQLite-WAL contract
in :mod:`microverse.memory.episodic` guarantees no committed event is
lost; the in-flight tick is simply discarded.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import sys
import time
from pathlib import Path

from microverse.agents.artisan import Artisan
from microverse.agents.base import Action, Agent, WorldContext
from microverse.agents.harvester import ArtifactCandidate, Harvester
from microverse.config import MAX_TICKS_DEFAULT
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.world.scheduler import RoundRobinScheduler

_logger = logging.getLogger(__name__)


def _build_world(_episodic: EpisodicMemory) -> WorldContext:
    # Phase 1 minimal — Phase 3a fills this with episodic_recent + lore.
    return WorldContext()


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
    if seed is not None:
        random.seed(seed)

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

    episodic = EpisodicMemory(data_dir / "episodic.sqlite")
    metrics = Metrics(data_dir / "metrics.sqlite", auto_flush_every=10)
    harvester = Harvester(harvest_dir)

    sched = RoundRobinScheduler()
    sched.register(Artisan(name="Aki", metrics=metrics))

    stop = {"requested": False}

    def _on_sigint(_signum: int, _frame: object) -> None:
        stop["requested"] = True

    signal.signal(signal.SIGINT, _on_sigint)

    max_ticks = ticks if ticks is not None else MAX_TICKS_DEFAULT
    executed = 0
    consecutive_skips = 0
    try:
        # Loop on `executed` rather than iteration count so paused
        # agents don't consume the budget. Bound consecutive skips so
        # an all-paused world doesn't hot-spin: after one full rotation
        # of skips we sleep and *reset* consecutive_fail for every
        # agent — the watchdog stub's auto-rehab — giving them another
        # chance instead of looping forever.
        while executed < max_ticks and not stop["requested"]:
            agent = sched.next()
            if metrics.should_pause(agent.name):
                consecutive_skips += 1
                if consecutive_skips > max(len(sched.agents), 1) * 3:
                    time.sleep(max(tempo or 1.0, 1.0))
                    for a in sched.agents:
                        metrics.reset("consecutive_fail", agent=a.name)
                    consecutive_skips = 0
                continue
            consecutive_skips = 0
            world = _build_world(episodic)
            try:
                action = agent.think(world)
            except Exception:
                # Hung/crashed Ollama call: count it, push the agent
                # toward pause, do not crash the whole run.
                _logger.exception("think() failed for agent %s", agent.name)
                metrics.bump("llm_timeout")
                metrics.bump("consecutive_fail", agent=agent.name)
                # Throttle so a persistent failure doesn't spin.
                time.sleep(min(max(tempo or 1.0, 1.0), 5.0))
                continue
            # Defense-in-depth reset: parse_action already resets on
            # json_ok/json_repaired paths, but if a future caller path
            # leaves consecutive_fail elevated after a *successful*
            # think(), we still want the next failure to count from 1.
            metrics.reset("consecutive_fail", agent=agent.name)
            _commit_action(episodic, agent, action)
            _maybe_harvest(harvester, agent, action)
            executed += 1
            sleep_s = tempo if tempo is not None else agent.tempo()
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        # Close each resource independently — a failure in one must
        # not skip the rest.
        try:
            metrics.close()  # flushes pending bumps internally
        except Exception:
            _logger.exception("metrics.close failed")
        try:
            episodic.close()
        except Exception:
            _logger.exception("episodic.close failed")

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

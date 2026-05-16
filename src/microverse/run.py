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
from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.agents.harvester import ArtifactCandidate, Harvester
from microverse.agents.scholar import Scholar
from microverse.agents.trader import Trader
from microverse.memory import _build_peer_inbox, _build_world_events, build_context
from microverse.memory.episodic import EpisodicMemory, Event
from microverse.memory.semantic import SemanticMemory
from microverse.ops.metrics import Metrics
from microverse.ops.watchdog import Watchdog
from microverse.world.clock import WorldClock
from microverse.world.scheduler import WeightedScheduler
from microverse.world.snapshot import SnapshotBusyError, maybe_snapshot
from microverse.world.workshop import WorkshopProjection

_logger = logging.getLogger(__name__)

# Phase 2 cadences.
HARVEST_FLUSH_EVERY = 50  # ticks between Trader-driven harvest flushes
SNAPSHOT_EVERY = 1000  # cold backups; WAL handles real durability

# Phase 4a cadences.
WATCHDOG_EVERY = 25  # ticks between watchdog sweeps
WORLD_CLOCK_MEAN_INTERVAL = 100  # mean ticks between weather events


def _build_roster(metrics: Metrics, *, solo: bool = False) -> list[Agent]:
    """Build the default tick-loop roster.

    Layer-G slice 4 (R2.c): default = Aki (Artisan, soul_tokens=100) +
    a Scholar resident (soul_tokens=70). The Scholar's lower weight
    keeps Aki the primary creator; the Scholar provides peer presence
    and observational output so the engagement gate (slice 3) has a
    real partner. ``solo=True`` reproduces the legacy single-Artisan
    regime for regression soaks.
    """
    aki = Artisan(name="Aki", metrics=metrics, soul_tokens=100)
    if solo:
        return [aki]
    cy = Scholar(name="Cy", metrics=metrics, soul_tokens=70)
    return [aki, cy]


def _all_agents_paused(metrics: Metrics, agents: Sequence[Agent]) -> bool:
    """True iff every registered agent is currently paused.

    Used by the tick loop to decide when to fire the deadlock-break
    rehab path. Checking *all* (rather than relying on a skip-count
    heuristic) prevents a single persistently-failing agent from
    pulling the entire roster's counters back to zero, which would
    mask legitimate per-agent failures.
    """
    return all(metrics.should_pause(a.name) for a in agents)


def _compute_peers(
    scheduler: WeightedScheduler,
    episodic: EpisodicMemory,
    agent: Agent,
    lookback: int = 200,
) -> tuple[str, ...]:
    """Compute the peer set for an agent's per-tick ``WorldContext``.

    Layer-G slice 2 (R2.a): the prior code constructed
    ``WorldContext()`` with no peers, so the persona rendered "You
    have not spoken with anyone today" every tick — reinforcing the
    solitary-narrator frame the silent-craftsperson attractor lives
    inside.

    Two sources, deduped, in roster-then-history order:
      1. Currently-registered scheduler agents minus self (always-on
         residency — peers exist whether or not they have spoken).
      2. Recent speak partners in episodic within ``lookback`` events
         (so a Stranger immigrant who has addressed self, or a
         self-spoken target who has since departed, still counts as
         an eligible engagement target).

    ``actor == "world"`` is excluded — weather is not a peer.
    """
    peers: list[str] = []
    seen: set[str] = {agent.name, "world"}

    for a in scheduler.agents:
        if a.name not in seen:
            peers.append(a.name)
            seen.add(a.name)

    # Path-3 (Codex review HIGH): only ``e.target == agent.name`` is
    # a permissible peer source. The prior ``e.actor == agent.name``
    # branch (peers I have spoken TO before) embedded the agent's own
    # speak history into the peers list — even names-only is
    # autobiographical leak. A peer who has addressed self is
    # demonstrably present; a peer self has addressed once and never
    # heard back is just self-history.
    for e in episodic.last(lookback):
        if e.action != "speak":
            continue
        if e.target != agent.name or not e.actor:
            continue
        if e.actor not in seen:
            peers.append(e.actor)
            seen.add(e.actor)

    return tuple(peers)


def _maybe_engagement_target(
    episodic: EpisodicMemory,
    *,
    agent_name: str,
    peers: tuple[str, ...],
    rng: random.Random,
    interval: int,
) -> str | None:
    """Pick a peer the agent must address this tick, or None.

    Layer-G slice 3 (R2.b): the engagement gate. The post-Layer-F 24h
    soak showed Aki silently crafting hundreds of ticks in a row with
    no targeted speaks at all — Layer F bound the artifact channel
    but the LLM rerouted into pure asocial production. The gate is
    the missing balancing loop.

    Walks ``episodic`` newest-first counting ONLY the agent's own
    actions. If any of its last ``interval`` actions was a speak with
    a non-null target, the gate is reset (return None). If the agent
    has fewer than ``interval`` total actions, it is in warmup and
    the gate does not fire. Otherwise picks a peer from ``peers`` via
    ``rng`` and returns it.

    The lookback into episodic is ``interval * 4`` events to find
    enough of the agent's own actions even when other agents are
    mixing in. Cap is intentional — a stale long-departed targeted
    speak from before the 4*K window does not save the agent from
    the gate.
    """
    if not peers:
        return None
    own_seen = 0
    lookback = max(interval * 4, 100)
    for e in episodic.last(lookback):
        if e.actor != agent_name:
            continue
        own_seen += 1
        if e.action == "speak" and e.target:
            return None
        if own_seen >= interval:
            break
    if own_seen < interval:
        return None
    return rng.choice(peers)


def _last_weather_kind(episodic: EpisodicMemory) -> str | None:
    """Return the kind of the most recent ``weather.*`` event, or
    ``None`` if no such event has been written yet. Shared by
    ``_derive_topic`` (FTS5 seed) and ``_derive_weather`` (display
    string in ``WorldContext``); each picks its own fallback.
    """
    for e in episodic.last(50):
        if e.actor == "world" and e.action.startswith("weather."):
            return e.action.removeprefix("weather.")
    return None


def _derive_topic(episodic: EpisodicMemory, agent: Agent) -> str:
    """Pick a scene-topic for FTS5 lore retrieval.

    Path-3 / Slice 6 (Codex review HIGH): the topic is a function of
    *world state*, not of the receiving agent. The prior fallback
    ``f"{agent.role} {agent.name}"`` seeded FTS5 with the agent's own
    name, pattern-matching lore tagged with that name and
    re-introducing self-history through the lore channel.

    Strategy now: use the most recent ``weather.*`` kind as the topic
    when present; otherwise return an empty topic (callers /
    ``build_context`` skip the FTS5 query when topic is blank). The
    agent's name and role never reach FTS5.
    """
    del agent  # signature kept for forward-compat; agent identity must not leak into lore.
    return _last_weather_kind(episodic) or ""


def _derive_weather(episodic: EpisodicMemory) -> str:
    """Current display weather for ``WorldContext.weather``.

    Path-3 (CodeRabbit review HIGH): the prior code never populated
    ``WorldContext.weather``, so persona templates always rendered
    the static default ``"clear"`` — defeating the world_events
    visibility the Path-3 contract promises. The current weather is
    a function of the most recent ``weather.*`` event in the
    episodic log. Falls back to ``"clear"`` when no weather event
    has been written (cold start / fresh data dir).
    """
    return _last_weather_kind(episodic) or "clear"


def _build_per_tick_world_base(
    *,
    episodic: EpisodicMemory,
    agent: Agent,
    peers: tuple[str, ...],
    last_tick_ts: float,
    engagement_hint: str = "",
    required_target: str | None = None,
    metrics: Metrics | None = None,
) -> WorldContext:
    """Assemble the per-tick ``world_base`` for ``build_context``.

    Path-3 stateless-tick contract: each tick builds a fresh
    ``WorldContext`` carrying only the bounded peer + world view
    since the agent's last own-tick. Self-history never enters the
    prompt; the LLM gets ``persona + weather + peers_today
    + peer_inbox + world_events + engagement nudge``.

    ``WorldContext.season`` is intentionally NOT populated here:
    v0.1 does not model a calendar. The field carries its static
    default and persona templates render it as a flavor stub. The
    contract is documented on ``WorldContext`` itself; reserved as
    a v0.2 hook.

    The ``last_tick_ts`` watermark is sourced from a per-agent dict
    in ``run()`` so the inbox/world view drains across ticks
    (one-shot semantics).
    """
    return WorldContext(
        weather=_derive_weather(episodic),
        peers_today=peers,
        peer_inbox=_build_peer_inbox(
            episodic,
            agent_name=agent.name,
            since_ts=last_tick_ts,
            metrics=metrics,
        ),
        world_events=_build_world_events(episodic, since_ts=last_tick_ts),
        engagement_hint=engagement_hint,
        required_target=required_target,
    )


def _commit_action(
    episodic: EpisodicMemory,
    agent: Agent,
    action: Action,
    *,
    workshop: WorkshopProjection | None = None,
) -> int:
    """Append the action to the episodic log and, when it is a
    workshop ``contribute``, mirror it into the WorkshopProjection.

    The contribute payload carries the ``fragment`` field that the
    projection reads (and that the workshop view renders); the
    episodic event also records ``thought`` and ``artifact`` for
    parity with other actions so the audit trail is identical.
    """
    payload: dict[str, object] = {
        "thought": action.thought,
        "artifact": action.artifact,
        "role": agent.role,
    }
    target: str | None = action.target
    if action.action == ActionKind.CONTRIBUTE:
        # contribute_to is the workshop WIP name; target on episodic
        # carries that so the projection can match without payload
        # inspection. The fragment text rides in ``fragment`` for the
        # projection AND in ``artifact`` so the manifest / dashboard
        # paths see the same text the persona did.
        target = action.contribute_to
        payload["fragment"] = action.artifact
        payload["contribute_to"] = action.contribute_to
    ts = time.time()
    event_id = episodic.append(
        actor=agent.name,
        action=action.action.value,
        target=target,
        payload=payload,
        ts=ts,
    )
    if action.action == ActionKind.CONTRIBUTE and workshop is not None:
        workshop.on_contribute_event(
            Event(
                id=event_id,
                ts=ts,
                actor=agent.name,
                action=action.action.value,
                target=target,
                payload=payload,
            )
        )
    return event_id


def _maybe_harvest(harvester: Harvester, agent: Agent, action: Action) -> None:
    if not action.artifact:
        return
    if action.action == ActionKind.CONTRIBUTE:
        # Workshop fragments are harvested as part of the completed
        # WIP, not as standalone artifacts. The Harvester pulls them
        # at flush time from the WorkshopProjection.
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
    solo: bool = False,
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
    workshop = WorkshopProjection(episodic)
    harvester = Harvester(
        harvest_dir,
        trader=trader,
        percentile=70,
        workshop=workshop,
        episodic=episodic,
        metrics=metrics,
    )

    sched = WeightedScheduler(rng=rng)
    for agent in _build_roster(metrics, solo=solo):
        # v0.3 (ADR 0004 Decision 3): the agent's parse_action looks
        # up the projection to hard-fold contributes targeting a
        # complete WIP. Attach before registering so the first tick
        # already sees it.
        agent.attach_workshop(workshop)
        sched.register(agent)
    # Trader scheduling is internal — it ranks the buffer at flush time,
    # not as a tick action. We don't register it in the scheduler.

    clock = WorldClock(seed=seed, mean_interval=WORLD_CLOCK_MEAN_INTERVAL)
    watchdog = Watchdog(
        metrics=metrics,
        episodic=episodic,
        scheduler=sched,
        workshop=workshop,
    )

    stop = {"requested": False}

    def _on_signal(_signum: int, _frame: object) -> None:
        stop["requested"] = True

    # Catch SIGINT (Ctrl-C) AND SIGTERM (e.g. `timeout`, supervisor kills,
    # systemd stop) so the finally block runs and the harvester buffer
    # gets a final flush. SIGKILL is still recoverable via WAL — the
    # in-flight tick is discarded but committed events are intact.
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    max_ticks = ticks if ticks is not None else config.MAX_TICKS_DEFAULT
    executed = 0
    consecutive_skips = 0
    # Bounded by config.MAX_CONSECUTIVE_DEADLOCK_BREAKS; the exit path
    # below bumps deadlock_break_exit and breaks the loop. Reset to 0
    # in the success branch right after metrics.reset(consecutive_fail).
    deadlock_breaks_since_success = 0

    # Path-3 watermark: per-agent ``ts`` of the last own-tick. The
    # peer_inbox / world_events helpers filter on ``ts > last_tick_ts``
    # (strict; ``setdefault`` below seeds the agent's first-seen
    # tick) so each agent sees only events that happened since they
    # last ran. Watermarks are in-memory only; on restart every
    # agent's view starts fresh from the new launch time. Mid-run
    # arrivals (e.g. Strangers spawned by Watchdog) are seeded at
    # their first encounter via ``setdefault(agent.name, time.time())``
    # below — they do NOT inherit the run-start watermark, which
    # would have leaked all world events since process launch into
    # their first inbox.
    last_tick_ts: dict[str, float] = {}

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
            peers = _compute_peers(sched, episodic, agent)
            required_target = _maybe_engagement_target(
                episodic,
                agent_name=agent.name,
                peers=peers,
                rng=rng,
                interval=config.PEER_ENGAGEMENT_INTERVAL,
            )
            engagement_hint = (
                f"You must address {required_target} this tick." if required_target else ""
            )
            if required_target:
                metrics.bump("engagement_gate_fired", agent=agent.name)
            # Path-3: pull the agent's watermark. ``setdefault`` seeds
            # first-encounter to ``time.time()`` so a mid-run Stranger
            # does not see all weather/world events since process
            # start on their first tick.
            agent_last_ts = last_tick_ts.setdefault(agent.name, time.time())
            world_base = _build_per_tick_world_base(
                episodic=episodic,
                agent=agent,
                peers=peers,
                last_tick_ts=agent_last_ts,
                engagement_hint=engagement_hint,
                required_target=required_target,
                metrics=metrics,
            )
            world = build_context(
                world_base=world_base,
                episodic=episodic,
                semantic=semantic,
                topic=topic,
                receiver_name=agent.name,
                workshop=workshop,
                metrics=metrics,
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
            _commit_action(episodic, agent, action, workshop=workshop)
            _maybe_harvest(harvester, agent, action)
            # Path-3: advance the agent's watermark so the NEXT call
            # to ``_build_per_tick_world_base`` for this agent drains
            # the events it has now seen. ``time.time()`` matches the
            # ``EpisodicMemory.append`` default-ts contract.
            last_tick_ts[agent.name] = time.time()
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
            except Exception:
                # Anything else (sqlite3.OperationalError "disk I/O
                # error", a transient FS failure, OS-level truncate
                # racing tar) must NOT kill the loop. WAL is the
                # durability boundary; snapshots are cold backups, so
                # missing one interval is acceptable. A 24h soak
                # crashed here once when this branch only caught
                # SnapshotBusyError.
                _logger.exception("snapshot failed")
                metrics.bump("snapshot_fail")

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
    p.add_argument(
        "--solo",
        action="store_true",
        help="Run with the legacy single-Artisan roster (no Scholar resident).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run(ticks=args.ticks, seed=args.seed, tempo=args.tempo, solo=args.solo)


if __name__ == "__main__":
    sys.exit(0 if main() >= 0 else 1)

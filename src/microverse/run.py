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
    :func:`microverse.world.snapshot.take_snapshot`, gated by a
    :class:`~microverse.world.snapshot.SnapshotGuard` circuit breaker.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path

from microverse import config
from microverse.agents.artisan import Artisan
from microverse.agents.base import Action, ActionKind, Agent, RelationFact, SelfView, WorldContext
from microverse.agents.belief import BeliefSummarizer
from microverse.agents.harvester import ArtifactCandidate, Harvester
from microverse.agents.scholar import Scholar
from microverse.agents.trader import Trader
from microverse.memory import _build_peer_inbox, _build_world_events, build_context
from microverse.memory.episodic import EpisodicMemory, Event
from microverse.memory.identity import IdentityStore
from microverse.memory.semantic import SemanticMemory
from microverse.ops.metrics import Metrics
from microverse.ops.watchdog import Watchdog
from microverse.world.clock import WorldClock
from microverse.world.economy import EnergyLedger, build_cost_table
from microverse.world.relationships import derive_relationships
from microverse.world.scene import SceneRunner
from microverse.world.scheduler import WeightedScheduler
from microverse.world.snapshot import (
    SnapshotBusyError,
    SnapshotGuard,
    prune_snapshots,
    take_snapshot,
)
from microverse.world.workshop import WorkshopProjection

_logger = logging.getLogger(__name__)

# Phase 2 cadences.
HARVEST_FLUSH_EVERY = 50  # ticks between Trader-driven harvest flushes
SNAPSHOT_EVERY = 1000  # cold backups; WAL handles real durability

# v1.1 (ADR 0007 Phase 1): how many new events to accumulate before the
# full-history relationship ledger is recomputed. Bounds the per-tick
# cost of the derive-on-read ledger over long soaks.
REL_LEDGER_REFRESH_EVENTS = 25

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


def _compute_novelty_hint(episodic: EpisodicMemory, agent: Agent) -> tuple[str, str, str]:
    """Phase D: when an agent's recent top-verb share crosses the
    dominance threshold, return a tuple ``(hint_text, dominant_verb,
    suggested_verb)``. All three strings are empty when no hint is
    active.

    The hint text is the human-readable line the persona renders;
    the verb strings are the *structured* form the agent's
    ``_maybe_diversify`` consumes directly (no string parsing —
    see Gemini PR review on #38).
    """
    from microverse.world.diversity import recent_verb_distribution, suggest_underused_verb

    # ``contribute`` is excluded from BOTH the distribution and the
    # substitution candidates. Scene contributes are coerced workshop
    # actions, not free verb choices: counting them pins the dominant
    # verb to ``contribute`` (a scene-heavy agent is ~90% contributes),
    # which the lever can never substitute (apply_diversity_lever's
    # CONTRIBUTE carve-out), so the lever silently never fires. The
    # 5.5-day soak proved this — diversity_lever_substituted stayed 0.
    available = ["speak", "craft", "study", "rest"]
    dist = recent_verb_distribution(episodic, agent.name, lookback=200, exclude={"contribute"})
    suggested = suggest_underused_verb(dist, available)
    if suggested is None:
        return ("", "", "")
    total = sum(dist.values())
    if total == 0:
        return ("", "", "")
    top_verb, _top_count = dist.most_common(1)[0]
    hint = f"You have leaned heavily on {top_verb} lately; consider {suggested}."
    return (hint, top_verb, suggested)


def _compute_energy_hint(energy: EnergyLedger | None, agent: Agent) -> str:
    """ADR 0008 spike: one-line scarcity signal for the persona, mirroring
    ``novelty_hint``. Empty when the economy is off (so flag-off prompts are
    byte-identical) or when reserves are ample. When some productive verbs are
    unaffordable it names the verbs out of reach and the verb that still comes
    easily (the role's cheap specialty under the role-advantage table; a stable
    tie under the flat control), so the model can choose affordably on its own
    — the perception channel that lets the CHOSEN-verb stream actually move.

    Gated behind ``_ECONOMY_SUBSTITUTE`` (modes 1/flat/sub) so the prompt-level
    pressure is paired with the substitution lever: the ``throttle`` ablation
    stays a CLEAN scene-gate-only arm with no prompt hint (Codex review).
    """
    if energy is None or not config._ECONOMY_SUBSTITUTE:
        return ""
    unaffordable = [
        v for v in _ENERGY_HINT_PRODUCTIVE if not energy.can_afford(agent.name, agent.role, v)
    ]
    if not unaffordable:
        return ""
    # Preconditions already checked above; dispatch the selector directly rather
    # than re-running them through _energy_hint_verb (CodeRabbit/Gemini review).
    cheapest = _select_easy_verb(energy, agent)
    out_of_reach = ", ".join(unaffordable)
    if cheapest:
        return (
            f"Your reserves are low: {out_of_reach} feel out of reach right now, "
            f"but {cheapest} still comes easily."
        )
    return f"Your reserves are spent; rest before attempting {out_of_reach}."


# Verbs whose unaffordability triggers the scarcity hint (``rest`` is always free
# and so never "out of reach"). Shared by the hint text and the named-verb helper.
_ENERGY_HINT_PRODUCTIVE = ("speak", "craft", "study", "travel", "contribute")


def _select_easy_verb(energy: EnergyLedger, agent: Agent) -> str | None:
    """The mode-aware "comes easily" verb, assuming the caller has already
    confirmed the hint fires (energy present, substitution mode, some verb out of
    reach). Single source of truth for the selector so the hint text and the R4
    conflict counter never disagree.

    ``adv`` names the agent's TRUE cheapest affordable verb including its payload
    specialty (craft), so each role is nudged toward its own advantage.
    ``sub``/``1``/``flat`` keep the legacy selector (excludes the payload verbs)
    so their prior reads stay byte-reproducible for the A/B. The executor's
    substitution target is unchanged in every mode (still payload-free)."""
    if config.ECONOMY_MODE == "adv":
        return energy.cheapest_affordable_perceived(agent.name, agent.role)
    return energy.cheapest_affordable_productive(agent.name, agent.role)


def _energy_hint_verb(energy: EnergyLedger | None, agent: Agent) -> str | None:
    """Gated wrapper of :func:`_select_easy_verb`: the verb
    :func:`_compute_energy_hint` names as "comes easily", or ``None`` when the
    economy is off, reserves are ample, or even the cheapest productive verb is
    unaffordable (then the hint shows the "rest before ..." message and names no
    easy verb). Exposed so the run loop can detect the novelty/energy hint
    conflict (ADR 0009 R4) without re-parsing the hint string."""
    if energy is None or not config._ECONOMY_SUBSTITUTE:
        return None
    unaffordable = any(
        not energy.can_afford(agent.name, agent.role, v) for v in _ENERGY_HINT_PRODUCTIVE
    )
    if not unaffordable:
        return None
    return _select_easy_verb(energy, agent)


def _hints_conflict(energy_easy_verb: str | None, novelty_dominant_verb: str) -> bool:
    """ADR 0009 R4: the honest ``energy_hint`` nudges toward the agent's cheap
    specialty while ``novelty_hint`` steers AWAY from whatever verb has come to
    dominate the recent mix. They contradict when the energy hint names the very
    verb novelty is discouraging (the now-dominant specialty). ``False`` when no
    easy verb is named or novelty is inactive (empty dominant verb)."""
    return bool(energy_easy_verb) and energy_easy_verb == novelty_dominant_verb


def _lazy_attach_energy(agent: Agent, energy: EnergyLedger | None) -> None:
    """Attach the ledger to an agent that lacks one (ADR 0008 spike).

    The startup roster is attached once at construction, but the Watchdog can
    register a Stranger mid-run (``ops/watchdog.py``) without an EnergyLedger.
    Such a Stranger would keep ``_energy is None`` and so escape the
    substitution lever, executing unaffordable verbs while the resident agents
    are constrained — biasing the A/B and Gate 9 (review). Attaching it here on
    its first scheduled tick restores parity with the startup roster. No-op when
    the economy is off, in a non-substitution mode, or already attached."""
    if energy is not None and config._ECONOMY_SUBSTITUTE and agent._energy is None:
        agent.attach_energy(energy)


def _replay_energy_events(episodic: EpisodicMemory) -> Iterator[tuple[str, str, str]]:
    """Stream chronological ``(actor, role, verb)`` for committed agent actions,
    used to reconstruct the EnergyLedger on restart (ADR 0008 spike). Role is
    read from each event's payload (written by ``_commit_action``); world/scene/
    harvester pseudo-actors and namespaced events are skipped. A generator over
    ``iter_chronological`` so a multi-week log is never fully materialized in
    memory (review). Best-effort and approximate — see
    ``EnergyLedger.reconstruct_from_events``."""
    verbset = {k.value for k in ActionKind}
    for ev in episodic.iter_chronological():
        if ev.actor in ("world", "scene", "harvester") or ev.action not in verbset:
            continue
        role = str(ev.payload.get("role", ""))
        if role:
            yield (ev.actor, role, ev.action)


class _RelationshipLedgerCache:
    """Throttle full-history relationship derivation.

    ``derive_relationships`` aggregates the entire (append-only) episodic
    log, so calling it on every agent on every tick makes total work grow
    quadratically with run length — a real cost for the multi-week soaks
    this project targets. Relationship counts drift slowly, so a few ticks
    of staleness in the prompt is harmless. This cache recomputes the
    whole roster's ledgers only when the event count has grown by
    ``refresh_events`` since the last refresh (full-history semantics
    preserved; just sampled). A peer not yet in the cache (e.g. a Stranger
    that just arrived) is derived on demand.
    """

    def __init__(self, episodic: EpisodicMemory, *, refresh_events: int) -> None:
        self._episodic = episodic
        self._refresh = max(refresh_events, 1)
        self._count_at = -1
        self._peers_at: tuple[str, ...] | None = None
        self._by_agent: dict[str, tuple[RelationFact, ...]] = {}

    def get(self, agent_name: str, known_peers: tuple[str, ...]) -> tuple[RelationFact, ...]:
        count = self._episodic.count()
        # Refresh when enough new events have accrued OR the roster changed
        # (a Watchdog-spawned Stranger must show up promptly, not after the
        # next event threshold).
        stale = (
            self._count_at < 0
            or count - self._count_at >= self._refresh
            or known_peers != self._peers_at
        )
        if stale:
            self._by_agent = {
                peer: derive_relationships(self._episodic, agent_name=peer, known_peers=known_peers)
                for peer in known_peers
            }
            self._count_at = count
            self._peers_at = known_peers
        if agent_name not in self._by_agent:
            self._by_agent[agent_name] = derive_relationships(
                self._episodic, agent_name=agent_name, known_peers=known_peers
            )
        return self._by_agent[agent_name]


def _build_self_view(
    episodic: EpisodicMemory,
    agent: Agent,
    *,
    known_peers: tuple[str, ...],
    beliefs: str = "",
    relationships: tuple[RelationFact, ...] | None = None,
) -> SelfView:
    """Assemble the agent's persistent self-record (ADR 0007 Phase 1).

    Static ``traits`` come from ``config.TRAITS_BY_ROLE``; the
    ``relationships`` ledger is derived on-read from the full episodic
    history (whitelisted against the live roster) unless a precomputed
    tuple is supplied (the run loop passes a throttle-cached one).
    ``beliefs`` is the periodically summarized line (Stage C) — empty
    until the first summarization. This is the EXPLICIT Path-3 carve-out:
    structured identity only, never the agent's own fragment prose.
    """
    rels = (
        relationships
        if relationships is not None
        else derive_relationships(episodic, agent_name=agent.name, known_peers=known_peers)
    )
    return SelfView(
        traits=config.TRAITS_BY_ROLE.get(agent.role, ()),
        relationships=rels,
        beliefs=beliefs,
    )


def _recent_agent_events(
    episodic: EpisodicMemory,
    agent_name: str,
    *,
    lookback: int,
) -> list[Event]:
    """The most recent ``lookback`` events the agent took or that were
    addressed to it, in chronological order. Feeds the belief summarizer.
    """
    own = episodic.involving(agent_name, limit=lookback)
    own.reverse()  # episodic.involving is newest-first; flip to chronological
    return own


def _maybe_update_beliefs(
    *,
    executed: int,
    agents: Sequence[Agent],
    episodic: EpisodicMemory,
    identity_store: IdentityStore | None,
    summarizer: BeliefSummarizer | None,
    metrics: Metrics,
) -> None:
    """Every ``config.BELIEF_UPDATE_INTERVAL`` ticks, re-summarize each
    agent's recent activity into a belief line and persist it (ADR 0007
    Phase 1, Stage C). Out-of-world LLM pass — not inside ``think()``.

    On a failed/empty summary the prior belief is kept (the summarizer
    returns ``None``). A no-op when the belief system is not wired
    (identity_store/summarizer ``None``) or the cadence has not elapsed.
    """
    if identity_store is None or summarizer is None:
        return
    interval = config.BELIEF_UPDATE_INTERVAL
    if interval <= 0 or executed == 0 or executed % interval != 0:
        return
    known_peers = tuple(a.name for a in agents)
    for agent in agents:
        events = _recent_agent_events(episodic, agent.name, lookback=config.BELIEF_LOOKBACK)
        new_belief = summarizer.summarize(
            agent_name=agent.name,
            role=agent.role,
            events=events,
            prior=identity_store.get(agent.name),
            metrics=metrics,
            known_peers=known_peers,
        )
        if new_belief:
            identity_store.put(agent.name, new_belief)
            metrics.bump("belief_updated", agent=agent.name)


def _build_per_tick_world_base(
    *,
    episodic: EpisodicMemory,
    agent: Agent,
    peers: tuple[str, ...],
    last_tick_ts: float,
    engagement_hint: str = "",
    required_target: str | None = None,
    metrics: Metrics | None = None,
    novelty_hint: str = "",
    novelty_dominant_verb: str = "",
    novelty_suggested_verb: str = "",
    energy_hint: str = "",
    self_view: SelfView | None = None,
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
        novelty_hint=novelty_hint,
        novelty_dominant_verb=novelty_dominant_verb,
        novelty_suggested_verb=novelty_suggested_verb,
        energy_hint=energy_hint,
        self_view=self_view if self_view is not None else SelfView(),
    )


def _commit_action(
    episodic: EpisodicMemory,
    agent: Agent,
    action: Action,
    *,
    workshop: WorkshopProjection | None = None,
    extra_payload: Mapping[str, object] | None = None,
) -> int:
    """Append the action to the episodic log and, when it is a
    workshop ``contribute``, mirror it into the WorkshopProjection.

    The contribute payload carries the ``fragment`` field that the
    projection reads (and that the workshop view renders); the
    episodic event also records ``thought`` and ``artifact`` for
    parity with other actions so the audit trail is identical.

    ``extra_payload`` merges additional keys (ADR 0008 spike telemetry —
    ``parsed_verb``). The run loop passes it only in substitution-enabled
    economy modes, so a flag-off run writes a byte-identical payload.
    """
    payload: dict[str, object] = {
        "thought": action.thought,
        "artifact": action.artifact,
        "role": agent.role,
    }
    if extra_payload:
        payload.update(extra_payload)
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
        # Phase C (ADR 0005 D3): scene linkage. When the action was
        # produced inside a scene micro-loop, these fields tag the
        # contribute so downstream consumers (gate-7 producer, dashboard,
        # kill-drill verifier) can group turns by scene_id. Plain
        # contributes outside scenes carry both as None.
        if action.scene_id is not None:
            payload["scene_id"] = action.scene_id
        if action.turn_index is not None:
            payload["turn_index"] = action.turn_index
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


def _run_periodic_maintenance(
    *,
    executed: int,
    episodic: EpisodicMemory,
    metrics: Metrics,
    data_dir: Path,
    snapshots_dir: Path,
    snapshot_guard: SnapshotGuard,
) -> None:
    """Phase A: episodic optimize + snapshot take + prune.

    Runs after every tick (both scene and single-tick paths). Each
    sub-step gates itself on cadence, so the common case is a no-op
    that only stat()-checks the tick counter.

    ``snapshot_guard`` is a circuit breaker: a persistent snapshot
    failure (e.g. SQLITE_IOERR on ``wal_checkpoint(TRUNCATE)`` seen in
    a 5.5-day soak, 9106 times) otherwise floods the log with identical
    tracebacks and perturbs the WAL/-shm enough to make ad-hoc readers
    see a stale ``MAX(ts)`` — a false stall. After a few consecutive
    failures the breaker trips and snapshots are skipped for the rest
    of the run; the WAL remains the durability boundary.
    """
    if (
        config.EPISODIC_OPTIMIZE_EVERY > 0
        and executed > 0
        and executed % config.EPISODIC_OPTIMIZE_EVERY == 0
    ):
        try:
            episodic.optimize()
        except Exception:
            _logger.exception("episodic.optimize failed")
            metrics.bump("episodic_optimize_fail")

    snapshot_due = SNAPSHOT_EVERY > 0 and executed > 0 and executed % SNAPSHOT_EVERY == 0
    if not snapshot_due or snapshot_guard.disabled:
        return
    try:
        snap_path = take_snapshot(data_dir, snapshots_dir)
        snapshot_guard.record_success()
        if snap_path is not None:
            try:
                prune_snapshots(
                    snapshots_dir,
                    max_count=config.SNAPSHOT_RETENTION_COUNT,
                    max_bytes=config.SNAPSHOT_RETENTION_BYTES,
                )
            except Exception:
                _logger.exception("snapshot prune failed")
                metrics.bump("snapshot_prune_fail")
    except SnapshotBusyError:
        # Transient writer contention — expected, clears on its own.
        # Does NOT count toward the breaker.
        _logger.warning("snapshot skipped: WAL checkpoint busy")
        metrics.bump("snapshot_skip_busy")
    except Exception as e:
        # Hard failure (e.g. SQLITE_IOERR). Log at WARNING without a
        # full traceback so a persistent fault does not flood the soak
        # log thousands of times, and feed the breaker.
        metrics.bump("snapshot_fail")
        _logger.warning("snapshot failed (%s): %s", type(e).__name__, e)
        if snapshot_guard.record_failure():
            _logger.warning(
                "snapshot disabled after %d consecutive failures; "
                "WAL remains the durability boundary",
                snapshot_guard.max_consecutive_failures,
            )
            metrics.bump("snapshot_disabled")


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
    # All four SQLite connections are opened before the run loop's
    # try/finally below is entered. Own their cleanup with an ExitStack so
    # that if ANY constructor during startup (IdentityStore, the
    # WorkshopProjection replay, Harvester, Watchdog, ...) raises, every
    # connection opened so far is closed instead of leaking. The same stack
    # is closed in the loop's finally on the normal path.
    db_cleanup = ExitStack()
    db_cleanup.callback(metrics.close)
    db_cleanup.callback(semantic.close)
    db_cleanup.callback(episodic.close)
    try:
        # v1.1 (ADR 0007 Phase 1, Stage C): persistent belief store + the
        # out-of-world summarizer that refreshes it on a cadence. The store
        # is a materialized cache over the WAL log (regenerable); beliefs
        # survive a clean restart rather than resetting to empty.
        identity_store = IdentityStore(data_dir / "identity.sqlite")
        db_cleanup.callback(identity_store.close)
        belief_summarizer = BeliefSummarizer()

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

        # ADR 0008 spike: the shared action-economy ledger. Constructed only
        # when the economy is enabled (mode != "0"); otherwise None, so every
        # deduct/regen/scene-gate/substitution site below is a no-op and the
        # run reproduces pre-spike behavior exactly. Reconstructed from the WAL
        # so a restart approximates an uninterrupted run (energy is never
        # persisted; the WAL stays the durability boundary). Attached to agents
        # for the substitution lever only in substitution-enabled modes; the
        # scene gate uses ``energy`` directly regardless.
        if config.ECONOMY_MODE not in config.VALID_ECONOMY_MODES:
            # Fail fast: an unrecognized MICROVERSE_ECONOMY (e.g. a typo) would
            # otherwise silently run an unlabeled no-op arm (ECONOMY_ENABLED but
            # neither gate nor substitution), corrupting the A/B (review).
            raise ValueError(
                f"MICROVERSE_ECONOMY={config.ECONOMY_MODE!r} is not a valid economy mode; "
                f"expected one of {sorted(config.VALID_ECONOMY_MODES)}"
            )
        energy: EnergyLedger | None = None
        if config.ECONOMY_ENABLED:
            energy = EnergyLedger.fresh(
                [a.name for a in sched.agents],
                max_energy=config.ENERGY_MAX,
                regen_per_tick=config.ENERGY_REGEN_PER_TICK,
                cost_table=build_cost_table(config.ECONOMY_MODE),
            )
            energy.reconstruct_from_events(_replay_energy_events(episodic))
            if config._ECONOMY_SUBSTITUTE:
                for agent in sched.agents:
                    agent.attach_energy(energy)

        # v1.1: throttle-cache for the full-history relationship ledger so
        # it is not recomputed from scratch on every agent on every tick.
        rel_ledger = _RelationshipLedgerCache(episodic, refresh_events=REL_LEDGER_REFRESH_EVENTS)

        clock = WorldClock(seed=seed, mean_interval=WORLD_CLOCK_MEAN_INTERVAL)
        watchdog = Watchdog(
            metrics=metrics,
            episodic=episodic,
            scheduler=sched,
            workshop=workshop,
        )
    except Exception:
        # Startup failed before the run loop's try/finally; release every
        # connection registered above rather than leaking it.
        db_cleanup.close()
        raise

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
    # Cold-backup snapshot circuit breaker. Trips after a few
    # consecutive failures so a persistent SQLITE_IOERR on
    # wal_checkpoint(TRUNCATE) cannot flood the log or starve readers.
    snapshot_guard = SnapshotGuard()
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

    # ADR 0005 §"Empirical risks" / Decision 1 guard: track the
    # distribution of contribute_to targets across the run so the
    # operator can detect the rerouting failure mode (all contributes
    # collapse onto the lowest-fragment open WIP after complete WIPs
    # are hidden). Published as ``wip_target_concentration`` integer
    # gauge (x100) every ``HARVEST_FLUSH_EVERY`` ticks; acceptance
    # threshold is < 70 (i.e. no single WIP holds >70% of recent
    # contribute targets).
    wip_target_counts: Counter[str] = Counter()

    # Phase B (ADR 0005 Decision 2): transition-triggered harvest flush.
    # Tracks ticks since the most recent flush so the throttle (≥5 ticks
    # between transition-triggered flushes) is respected without losing
    # the 50-tick timer ceiling.
    transition_flush_throttle_ticks = 5
    ticks_since_last_flush = 0

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
            # ADR 0008 spike: a Watchdog-spawned Stranger registers mid-run
            # without an EnergyLedger; attach it here so the lever + hints apply
            # to it like the startup roster (no-op when economy off / already
            # attached). Must precede think() / the scene gate this tick.
            _lazy_attach_energy(agent, energy)
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
            # Phase D: compute novelty_hint based on the agent's recent
            # verb mix. Surface only when dominance > threshold; the
            # persona renders the text and the agent's _maybe_diversify
            # reads the structured verbs directly (no string parsing).
            novelty_hint, novelty_dominant_verb, novelty_suggested_verb = _compute_novelty_hint(
                episodic, agent
            )
            # ADR 0009 R4: count ticks where the honest energy_hint nudges toward
            # the very verb the novelty hint is steering away from (the agent's
            # now-dominant specialty). High counts mean the two levers fight, which
            # would cap chosen-verb specialization; the live Stage-4 read inspects it.
            if _hints_conflict(_energy_hint_verb(energy, agent), novelty_dominant_verb):
                metrics.bump("novelty_energy_hint_conflict", agent=agent.name)
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
                novelty_hint=novelty_hint,
                novelty_dominant_verb=novelty_dominant_verb,
                novelty_suggested_verb=novelty_suggested_verb,
                energy_hint=_compute_energy_hint(energy, agent),
                self_view=_build_self_view(
                    episodic,
                    agent,
                    known_peers=tuple(a.name for a in sched.agents),
                    beliefs=identity_store.get(agent.name),
                    relationships=rel_ledger.get(agent.name, tuple(a.name for a in sched.agents)),
                ),
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
            # Phase C (ADR 0005 D3): scene gate. With probability
            # SCENE_GATE_P, route this agent's tick into a 3-turn scene
            # against an open WIP instead of a single-tick action.
            # Scenes are the load-bearing change for gate 1 (peer
            # reference) — turn 2 and turn 3 see prior turns as
            # explicit prompt INPUT, not via coercion. The scene's
            # three turns count as this agent's tick for scheduler
            # bookkeeping.
            other_peers = [p for p in sched.agents if p.name != agent.name]
            open_wip = next(
                (w for w in workshop.wips() if w.phase != "complete"),
                None,
            )
            # ADR 0008 spike: in scene-gate economy modes (1/flat/throttle) an
            # agent can only OPEN a scene if it can afford a contribute. This
            # is the only economy interaction with scenes — evaluated BEFORE
            # any scene.open is emitted, so there are no partial/orphaned
            # scenes and the ADR 0006 scene contract is untouched. The three
            # scene contributes are paid at commit, never substituted.
            scene_energy_ok = (
                energy is None
                or not config._ECONOMY_SCENE_GATE
                or energy.can_afford(agent.name, agent.role, "contribute")
            )
            # The scene roll is consumed BEFORE the energy precondition so the
            # seeded rng stream stays identical across economy arms (Codex
            # review): an OFF run draws exactly when baseline did, and an ON run
            # draws the same — energy only suppresses the scene, it never skips
            # the draw. ``scene_energy_ok`` is last so OFF (always True) is
            # rng-identical to the pre-spike loop.
            scene_eligible = (
                config.SCENE_GATE_P > 0
                and open_wip is not None
                and len(other_peers) >= config.SCENE_MIN_PEERS
                and rng.random() < config.SCENE_GATE_P
                and scene_energy_ok
            )
            if scene_eligible and open_wip is not None:
                # Scene path. Build a SceneRunner with closures over
                # current-tick state. The world_factory rebuilds
                # WorldContext per turn so the just-committed prior
                # turn shows up in scene_context (and through
                # workshop_view on rebuild).
                def _scene_world_factory(
                    *,
                    agent: Agent,
                    scene_context: tuple,
                    scene_wip_name: str = "",
                ) -> WorldContext:
                    sc_topic = _derive_topic(episodic, agent)
                    sc_peers = _compute_peers(sched, episodic, agent)
                    sc_last_ts = last_tick_ts.setdefault(agent.name, time.time())
                    sc_world_base = _build_per_tick_world_base(
                        episodic=episodic,
                        agent=agent,
                        peers=sc_peers,
                        last_tick_ts=sc_last_ts,
                        engagement_hint="",
                        required_target=None,
                        metrics=metrics,
                        # No energy_hint inside a scene (ADR 0006): scene turns
                        # are forced contributes, and the lever already skips
                        # them. A scarcity hint could nudge the author off
                        # contribute and abort the scene, so it must be silent
                        # here too (review). Scene throttling is initiation-only.
                        energy_hint="",
                        self_view=_build_self_view(
                            episodic,
                            agent,
                            known_peers=tuple(a.name for a in sched.agents),
                            beliefs=identity_store.get(agent.name),
                            relationships=rel_ledger.get(
                                agent.name, tuple(a.name for a in sched.agents)
                            ),
                        ),
                    )
                    sc_world = build_context(
                        world_base=sc_world_base,
                        episodic=episodic,
                        semantic=semantic,
                        topic=sc_topic,
                        receiver_name=agent.name,
                        workshop=workshop,
                        metrics=metrics,
                    )
                    from dataclasses import replace

                    return replace(
                        sc_world,
                        scene_context=scene_context,
                        scene_wip_name=scene_wip_name,
                    )

                def _scene_commit(a: Agent, act: Action) -> None:
                    _commit_action(episodic, a, act, workshop=workshop)
                    # ADR 0008 spike: the scene's forced contributes are paid
                    # here (never substituted). No telemetry stamped: a scene
                    # turn is a forced contribute, so parsed == executed. Only
                    # the INITIATOR's contribute affordability gates the scene
                    # (above); turn-2/3 authors are not pre-checked, so their
                    # deduct may clamp at 0 (a known approximation — the
                    # initiation throttle is the lever, not per-turn gating).
                    if energy is not None:
                        energy.deduct(a.name, a.role, act.action.value)
                    if act.action == ActionKind.CONTRIBUTE and act.contribute_to:
                        wip_target_counts[act.contribute_to] += 1
                    _maybe_harvest(harvester, a, act)
                    last_tick_ts[a.name] = time.time()

                scene_runner = SceneRunner(
                    episodic=episodic,
                    commit_action=_scene_commit,
                    world_factory=_scene_world_factory,
                    rng=rng,
                    metrics=metrics,
                )
                try:
                    scene_runner.run(agent, open_wip.name, peers=other_peers)
                except Exception:
                    _logger.exception("scene runner crashed for %s", agent.name)
                    metrics.bump("scene_runner_crash", agent=agent.name)
                metrics.reset("consecutive_fail", agent=agent.name)
                deadlock_breaks_since_success = 0
                executed += 1
                # ADR 0008 spike: whole-roster regen once per tick (fair across
                # the scheduler's weighting; non-acting agents also recover).
                if energy is not None:
                    for a in sched.agents:
                        energy.regen(a.name)
                # Skip single-tick path for this tick.
                # World clock + watchdog still run per-tick below via
                # the shared trailing block.
                _safe(
                    "WorldClock.advance",
                    lambda: clock.advance(episodic, ticks_elapsed=1),
                )
                if executed % WATCHDOG_EVERY == 0:
                    _safe("watchdog.check", watchdog.check)
                # Reuse the Phase B flush logic below by continuing to
                # the bottom of the loop. Peek transitions without
                # draining so a throttle-blocked tick does not lose
                # the edge signal — drain only when actually flushing.
                ticks_since_last_flush += 1
                has_transitions = workshop.has_complete_transitions()
                timer_fire = executed % HARVEST_FLUSH_EVERY == 0
                transition_fire = has_transitions and (
                    ticks_since_last_flush >= transition_flush_throttle_ticks
                )
                if timer_fire or transition_fire:
                    workshop.drain_complete_transitions()
                    try:
                        harvester.flush()
                    except Exception:
                        _logger.exception("harvester.flush failed")
                        metrics.bump("harvest_flush_fail")
                    if timer_fire:
                        metrics.bump("harvest_flush_timer_triggered")
                    if transition_fire and not timer_fire:
                        metrics.bump("harvest_flush_transition_triggered")
                    ticks_since_last_flush = 0
                    total = sum(wip_target_counts.values())
                    if total > 0:
                        peak = max(wip_target_counts.values())
                        concentration = int(peak * 100 / total)
                    else:
                        concentration = 0
                    metrics.set_value("wip_target_concentration", concentration)
                    wip_target_counts.clear()
                # Periodic SQLite hygiene + snapshot pruning must run on
                # the scene path too — a scene-heavy soak otherwise
                # never optimizes the DB or prunes archives.
                _run_periodic_maintenance(
                    executed=executed,
                    episodic=episodic,
                    metrics=metrics,
                    data_dir=data_dir,
                    snapshots_dir=snapshots_dir,
                    snapshot_guard=snapshot_guard,
                )
                _maybe_update_beliefs(
                    executed=executed,
                    agents=sched.agents,
                    episodic=episodic,
                    identity_store=identity_store,
                    summarizer=belief_summarizer,
                    metrics=metrics,
                )
                sleep_s = tempo if tempo is not None else agent.tempo()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

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
            # ADR 0008 spike: stamp the model's pre-economy verb so Gate 9 can
            # compare CHOSEN vs EXECUTED. Stamped ONLY in substitution-enabled
            # modes, so a flag-off run writes a byte-identical payload.
            extra_payload = (
                dict(agent._verb_trace)
                if (energy is not None and config._ECONOMY_SUBSTITUTE)
                else None
            )
            _commit_action(episodic, agent, action, workshop=workshop, extra_payload=extra_payload)
            if energy is not None:
                energy.deduct(agent.name, agent.role, action.action.value)
            if action.action == ActionKind.CONTRIBUTE and action.contribute_to:
                wip_target_counts[action.contribute_to] += 1
            _maybe_harvest(harvester, agent, action)
            # Path-3: advance the agent's watermark so the NEXT call
            # to ``_build_per_tick_world_base`` for this agent drains
            # the events it has now seen. ``time.time()`` matches the
            # ``EpisodicMemory.append`` default-ts contract.
            last_tick_ts[agent.name] = time.time()
            executed += 1
            # ADR 0008 spike: whole-roster regen once per tick.
            if energy is not None:
                for a in sched.agents:
                    energy.regen(a.name)

            # World clock + watchdog: cheap, run every tick / every Nth.
            _safe("WorldClock.advance", lambda: clock.advance(episodic, ticks_elapsed=1))
            if executed % WATCHDOG_EVERY == 0:
                _safe("watchdog.check", watchdog.check)

            # Phase B (ADR 0005 D2): edge-triggered flush on WIP
            # completion. The 50-tick timer is retained as a ceiling
            # so artifact-only flushes still happen in windows with no
            # WIP completions. Peek transitions without draining so a
            # throttle-blocked tick does not lose the edge signal.
            ticks_since_last_flush += 1
            has_transitions = workshop.has_complete_transitions()
            timer_fire = executed % HARVEST_FLUSH_EVERY == 0
            transition_fire = has_transitions and (
                ticks_since_last_flush >= transition_flush_throttle_ticks
            )
            if timer_fire or transition_fire:
                workshop.drain_complete_transitions()
                try:
                    harvester.flush()
                except Exception:
                    _logger.exception("harvester.flush failed")
                    metrics.bump("harvest_flush_fail")
                if timer_fire:
                    metrics.bump("harvest_flush_timer_triggered")
                if transition_fire and not timer_fire:
                    metrics.bump("harvest_flush_transition_triggered")
                ticks_since_last_flush = 0
                # ADR 0005 D1 rerouting guard: publish the current
                # ``wip_target_concentration`` and reset the window.
                # Sampling at the flush boundary aligns the window
                # with the harvester's recycle cadence so each
                # measurement covers one flush-window of contributes.
                # Empty windows publish 0 explicitly so the gauge does
                # not go stale at the previous window's value.
                total = sum(wip_target_counts.values())
                if total > 0:
                    peak = max(wip_target_counts.values())
                    concentration = int(peak * 100 / total)
                else:
                    concentration = 0
                metrics.set_value("wip_target_concentration", concentration)
                wip_target_counts.clear()
            _run_periodic_maintenance(
                executed=executed,
                episodic=episodic,
                metrics=metrics,
                data_dir=data_dir,
                snapshots_dir=snapshots_dir,
                snapshot_guard=snapshot_guard,
            )
            _maybe_update_beliefs(
                executed=executed,
                agents=sched.agents,
                episodic=episodic,
                identity_store=identity_store,
                summarizer=belief_summarizer,
                metrics=metrics,
            )

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
        # Closes all four SQLite connections registered at startup
        # (identity_store, episodic, semantic, metrics) in LIFO order.
        _safe("db_cleanup.close", db_cleanup.close)

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

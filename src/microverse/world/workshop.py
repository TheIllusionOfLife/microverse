"""Shared-workshop projection over the episodic event log.

ADR 0003 contract:

  * The workshop is a **read-model** (projection) over the episodic
    SQLite event log. ``contribute`` events written by agents are the
    sole authoritative write surface; the projection's in-memory
    state is derived from those events.
  * On restart, the projection is rebuilt from the log. The
    in-memory cache is never trusted standalone; snapshots inherit
    durability from the episodic file.
  * The runtime in ``run.py`` constructs one projection per process
    and calls ``on_contribute_event(e)`` after each freshly-committed
    contribute event so the hot path stays O(1).

A ``WIP`` (work-in-progress) has a stable name (configured at module
level — agent-spawnable WIPs are v0.3), an append-only fragment list,
and a phase: ``forming`` → ``developing`` → ``complete``. Phase
transitions are deterministic:

  * ``forming → developing`` when the WIP has at least
    ``DEVELOPING_CONTRIBUTOR_FLOOR`` distinct contributors OR at
    least ``DEVELOPING_FRAGMENT_FLOOR`` fragments.
  * ``developing → complete`` when the WIP reaches
    ``COMPLETE_FRAGMENT_FLOOR`` fragments, or when the
    Watchdog-driven ``stale_to_complete`` path nominates it.

A completed WIP rejects further fragments. The audit trail in
episodic still records the rejected contribute event for human
review.

Per-receiver redaction lives in ``memory/__init__.py`` — the
projection itself stores contributor names verbatim; redaction is a
render-time concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory, Event


# Configured WIPs for v0.2. Agent-spawnable WIPs are out of scope.
CONFIGURED_WIPS: tuple[str, ...] = (
    "workshop.scroll",
    "workshop.loom",
    "workshop.garden_bed",
)

# Phase-transition thresholds. Tuned conservatively so a fresh WIP
# can reach ``developing`` after two distinct contributors or three
# fragments (whichever first), and ``complete`` after eight fragments
# total — slow enough to leave room for multi-tick accretion, fast
# enough that a 24h soak produces at least ~1 completed WIP per hour
# at v0.1.1's tick rate.
DEVELOPING_CONTRIBUTOR_FLOOR = 2
DEVELOPING_FRAGMENT_FLOOR = 3
COMPLETE_FRAGMENT_FLOOR = 8

# Phase enum as a string Literal for cheap comparison.
WIPPhase = Literal["forming", "developing", "complete"]


@dataclass(frozen=True, slots=True)
class Fragment:
    """One contribution to a WIP. Immutable — projections accumulate
    fragments by appending, never by mutating an existing fragment.
    """

    contributor: str
    text: str
    ts: float


@dataclass(frozen=True, slots=True)
class WIPView:
    """Render-ready view of one WIP for a single receiver.

    Built by ``memory._build_workshop_view`` with per-receiver
    redaction: the receiver's own contributor name is masked in
    ``contributors`` and the receiver's own fragment texts are
    replaced by an anonymous marker in ``excerpt``. Other
    contributors and their fragments pass through verbatim.

    The fields are pre-rendered strings, not raw structures, so the
    persona template can render them with simple Jinja conditionals
    without needing iteration logic for redaction.
    """

    name: str
    phase: str
    contributors: str
    excerpt: str


@dataclass
class WIP:
    """One workshop work-in-progress.

    Mutable on the hot path (``on_contribute_event`` appends to
    ``fragments`` and may flip ``phase``) but never observed mutating
    by callers — ``WorkshopProjection.wips()`` returns the live list,
    and the per-tick ``build_context`` snapshots it into immutable
    ``WIPView`` objects.
    """

    name: str
    fragments: list[Fragment] = field(default_factory=list)
    phase: WIPPhase = "forming"
    last_activity_ts: float = 0.0

    def contributors(self) -> tuple[str, ...]:
        """Distinct contributors in first-appearance order."""
        seen: set[str] = set()
        out: list[str] = []
        for f in self.fragments:
            if f.contributor not in seen:
                seen.add(f.contributor)
                out.append(f.contributor)
        return tuple(out)


class WorkshopProjection:
    """Read-model over an ``EpisodicMemory`` of contribute events."""

    def __init__(self, episodic: EpisodicMemory) -> None:
        self._wips: dict[str, WIP] = {name: WIP(name=name) for name in CONFIGURED_WIPS}
        self._rebuild_from_episodic(episodic)

    def _rebuild_from_episodic(self, episodic: EpisodicMemory) -> None:
        """Cold rebuild from the event log. Idempotent.

        Iterates events oldest-first (``since(0.0)`` is ordered
        newest-first, so we reverse) and applies each contribute
        event. Non-contribute events and contribute events targeting
        an unknown WIP are dropped.
        """
        # Reset to empty state in case the caller is rebuilding an
        # existing projection (e.g. after kill-safety drill verifies
        # restart consistency).
        for wip in self._wips.values():
            wip.fragments.clear()
            wip.phase = "forming"
            wip.last_activity_ts = 0.0

        events = list(reversed(episodic.since(0.0)))
        for e in events:
            self._apply(e)

    def on_contribute_event(self, event: Event) -> None:
        """Hot-path: incorporate one freshly-committed event into the
        projection. Called by ``run.py`` immediately after the event
        is appended to episodic.
        """
        self._apply(event)

    def _apply(self, event: Event) -> None:
        if event.action != "contribute":
            return
        wip_name = event.target
        if wip_name is None or wip_name not in self._wips:
            return
        text = str((event.payload or {}).get("fragment") or "").strip()
        if not text:
            return
        wip = self._wips[wip_name]
        if wip.phase == "complete":
            # Audit-only — the event remains in episodic for review,
            # but the projection is closed.
            return
        wip.fragments.append(
            Fragment(contributor=event.actor, text=text, ts=event.ts)
        )
        wip.last_activity_ts = event.ts
        self._recompute_phase(wip)

    def _recompute_phase(self, wip: WIP) -> None:
        n = len(wip.fragments)
        if n >= COMPLETE_FRAGMENT_FLOOR:
            wip.phase = "complete"
            return
        if (
            n >= DEVELOPING_FRAGMENT_FLOOR
            or len(wip.contributors()) >= DEVELOPING_CONTRIBUTOR_FLOOR
        ):
            wip.phase = "developing"
            return
        wip.phase = "forming"

    def wips(self) -> tuple[WIP, ...]:
        """Snapshot of all WIPs in configured order."""
        return tuple(self._wips[name] for name in CONFIGURED_WIPS)

    def get(self, name: str) -> WIP | None:
        return self._wips.get(name)

    def stale_to_complete(self, *, now_ts: float, timeout_s: float) -> list[str]:
        """Names of WIPs that have non-zero activity, are not already
        complete, and whose ``last_activity_ts`` is older than
        ``timeout_s`` relative to ``now_ts``. The Watchdog's
        ``workshop_stale`` detector calls this; the caller is
        responsible for synthesising an explicit-complete event into
        episodic if it wants to actually flip the phase.

        Empty WIPs (no activity) are NOT stale — there is nothing to
        time out at cold start.
        """
        out: list[str] = []
        for wip in self._wips.values():
            if wip.phase == "complete":
                continue
            if not wip.fragments:
                continue
            if now_ts - wip.last_activity_ts > timeout_s:
                out.append(wip.name)
        return out

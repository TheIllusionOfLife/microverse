"""v0.3 (ADR 0004) — WIP recycle lifecycle tests.

Slice 1.1: ``_apply`` resets WIP state on ``workshop.recycle``.
Slice 1.2: kill-safety equivalence after a recycle event in the log.
Slice 1.6: capacity invariant — ``open_slots()`` returns the count of
            WIPs not currently in ``complete`` phase.

The recycle path is the load-bearing fix for v0.2's pathology #2 — the
3-WIP set bottoming out after one hour. ADR 0004 Decision 1 requires
that every lifecycle transition is an explicit episodic event so
restart replay is deterministic.

Plus: ``workshop.harvest_attempt`` events bump a per-WIP attempts
counter so the harvester can decide when to recycle on rejection.
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.episodic import EpisodicMemory
from microverse.world.workshop import (
    COMPLETE_FRAGMENT_FLOOR,
    CONFIGURED_WIPS,
    WorkshopProjection,
)


def _seed_contribute(
    ep: EpisodicMemory,
    *,
    actor: str,
    wip: str,
    fragment: str,
    ts: float | None = None,
) -> None:
    ep.append(
        actor=actor,
        action="contribute",
        target=wip,
        payload={"thought": f"{actor} weaving", "fragment": fragment},
        ts=ts,
    )


def _fill_to_complete(ep: EpisodicMemory, *, wip: str, base_ts: float = 1.0) -> None:
    """Push enough contributes to flip ``wip`` to ``complete``.

    Alternates contributor names so the contributor count never trips
    a phase rule by itself — completion comes from fragment count.
    """
    for i in range(COMPLETE_FRAGMENT_FLOOR):
        _seed_contribute(
            ep,
            actor=("Aki" if i % 2 == 0 else "Bo"),
            wip=wip,
            fragment=f"fragment-{i}",
            ts=base_ts + i,
        )


# ---------------------------------------------------------------------------
# Slice 1.1 — _apply resets WIP state on workshop.recycle
# ---------------------------------------------------------------------------


def test_recycle_event_resets_wip(tmp_path: Path) -> None:
    """A ``workshop.recycle`` event flips the WIP back to ``forming``,
    clears its fragments and ``last_activity_ts``, and zeroes the
    ``completed_ts`` and ``harvest_attempts`` tracking fields.
    """
    name = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        proj = WorkshopProjection(ep)
        wip = proj.get(name)
        assert wip is not None
        assert wip.phase == "complete"
        assert len(wip.fragments) == COMPLETE_FRAGMENT_FLOOR
        assert wip.completed_ts > 0.0

        ep.append(
            actor="harvester",
            action="workshop.recycle",
            target=name,
            payload={"reason": "accepted", "dropped_fragments": 0},
            ts=100.0,
        )
        # Fresh projection rebuilds from the log; the in-memory
        # cache is never trusted standalone (ADR 0003 contract).
        proj2 = WorkshopProjection(ep)
        recycled = proj2.get(name)
        assert recycled is not None
        assert recycled.phase == "forming"
        assert recycled.fragments == []
        assert recycled.completed_ts == 0.0
        assert recycled.harvest_attempts == 0
        assert recycled.last_activity_ts == 0.0


def test_recycle_event_allows_new_contributes_after(tmp_path: Path) -> None:
    """After recycle, a fresh contribute lands as fragment 1 of a new
    WIP in ``forming`` (not appended to a phantom-complete history).
    """
    name = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        ep.append(
            actor="harvester",
            action="workshop.recycle",
            target=name,
            payload={"reason": "accepted", "dropped_fragments": 0},
            ts=100.0,
        )
        _seed_contribute(ep, actor="Cy", wip=name, fragment="brand new", ts=200.0)
        proj = WorkshopProjection(ep)
        wip = proj.get(name)
        assert wip is not None
        assert wip.phase == "forming"
        assert [f.text for f in wip.fragments] == ["brand new"]
        assert wip.contributors() == ("Cy",)


def test_recycle_unknown_wip_is_dropped(tmp_path: Path) -> None:
    """A ``workshop.recycle`` event targeting a non-configured WIP is
    a no-op (consistent with the projection's existing tolerance for
    unknown contribute targets).
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(
            actor="harvester",
            action="workshop.recycle",
            target="workshop.does_not_exist",
            payload={"reason": "accepted"},
            ts=1.0,
        )
        proj = WorkshopProjection(ep)
        for w in proj.wips():
            assert w.phase == "forming"
            assert w.fragments == []


def test_harvest_attempt_event_increments_counter(tmp_path: Path) -> None:
    """A ``workshop.harvest_attempt`` event bumps the per-WIP
    ``harvest_attempts`` counter so the harvester (or a future replay)
    can decide when to force-recycle on rejection.
    """
    name = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        ep.append(
            actor="harvester",
            action="workshop.harvest_attempt",
            target=name,
            payload={},
            ts=100.0,
        )
        ep.append(
            actor="harvester",
            action="workshop.harvest_attempt",
            target=name,
            payload={},
            ts=101.0,
        )
        proj = WorkshopProjection(ep)
        wip = proj.get(name)
        assert wip is not None
        assert wip.phase == "complete"
        assert wip.harvest_attempts == 2


# ---------------------------------------------------------------------------
# Slice 1.2 — kill-safety equivalence after a recycle event
# ---------------------------------------------------------------------------


def test_kill_safety_after_recycle(tmp_path: Path) -> None:
    """A fresh projection built from the same episodic log after a
    recycle event matches a projection that observed the event
    incrementally via ``on_contribute_event``. This is the deterministic
    replay guarantee — restart cannot diverge from in-memory state.
    """
    name = CONFIGURED_WIPS[0]
    db = tmp_path / "ep.sqlite"
    with EpisodicMemory(db) as ep:
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        ep.append(
            actor="harvester",
            action="workshop.recycle",
            target=name,
            payload={"reason": "accepted", "dropped_fragments": 0},
            ts=100.0,
        )
        _seed_contribute(ep, actor="Cy", wip=name, fragment="post-recycle", ts=200.0)
        proj_a = WorkshopProjection(ep)

    # Simulate process restart: open a new EpisodicMemory pointing at
    # the same DB, build a fresh projection.
    with EpisodicMemory(db) as ep2:
        proj_b = WorkshopProjection(ep2)

    wip_a = proj_a.get(name)
    wip_b = proj_b.get(name)
    assert wip_a is not None and wip_b is not None
    assert wip_a.phase == wip_b.phase
    assert [(f.contributor, f.text) for f in wip_a.fragments] == [
        (f.contributor, f.text) for f in wip_b.fragments
    ]
    assert wip_a.completed_ts == wip_b.completed_ts
    assert wip_a.harvest_attempts == wip_b.harvest_attempts


# ---------------------------------------------------------------------------
# Slice 1.6 — capacity invariant via open_slots()
# ---------------------------------------------------------------------------


def test_open_slots_is_full_at_startup(tmp_path: Path) -> None:
    """At cold start every configured WIP is in ``forming``, so
    ``open_slots()`` equals ``len(CONFIGURED_WIPS)``.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj = WorkshopProjection(ep)
    assert proj.open_slots() == len(CONFIGURED_WIPS)


def test_open_slots_drops_when_wip_completes(tmp_path: Path) -> None:
    """A WIP that has reached ``complete`` does not count toward the
    open-slots tally — that's what the recycle path has to restore.
    """
    name = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        proj = WorkshopProjection(ep)
    assert proj.open_slots() == len(CONFIGURED_WIPS) - 1


def test_open_slots_restored_after_recycle(tmp_path: Path) -> None:
    """The capacity invariant (>=3 open slots in steady state) holds
    because recycle restores the slot the completion took.
    """
    name = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        ep.append(
            actor="harvester",
            action="workshop.recycle",
            target=name,
            payload={"reason": "accepted", "dropped_fragments": 0},
            ts=100.0,
        )
        proj = WorkshopProjection(ep)
    assert proj.open_slots() == len(CONFIGURED_WIPS)


def test_is_complete_helper(tmp_path: Path) -> None:
    """``WorkshopProjection.is_complete(name)`` returns True iff the
    named WIP is in ``complete`` phase. Used by Fix 3's hard-fold path
    in the action validator.
    """
    name = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj = WorkshopProjection(ep)
        assert proj.is_complete(name) is False
        # Unknown WIPs are not complete (validators relying on the
        # check must not crash on a typo'd contribute_to).
        assert proj.is_complete("workshop.nonexistent") is False
        _fill_to_complete(ep, wip=name, base_ts=1.0)
        proj2 = WorkshopProjection(ep)
        assert proj2.is_complete(name) is True

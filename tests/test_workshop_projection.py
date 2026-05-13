"""Phase 2 — WorkshopProjection over the episodic event log.

ADR 0003 contract: the workshop is a *projection* (read-model) over
the episodic event log. ``contribute`` events are the sole
authoritative write; the projection's in-memory state is rebuilt
from the log on construction. Snapshot durability is inherited from
the episodic SQLite file; no separate WAL.

Slices covered here (all deterministic, no LLM):
  * 2.1 — WIP / Fragment shape + rebuild_from_episodic.
  * 2.2 — Phase transitions (forming → developing → complete) based
    on contributor count, fragment count, and explicit-complete payload.
  * 2.3 — stale_to_complete clock-driven auto-completion.

The kill-safety drill (slice 2.4) lives in test_workshop_kill_safety.py
so the heavy SQLite-restart machinery doesn't bloat this fast suite.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest

from microverse.memory.episodic import EpisodicMemory
from microverse.world.workshop import (
    COMPLETE_FRAGMENT_FLOOR,
    CONFIGURED_WIPS,
    DEVELOPING_CONTRIBUTOR_FLOOR,
    DEVELOPING_FRAGMENT_FLOOR,
    WIP,
    Fragment,
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


# ---------------------------------------------------------------------------
# Slice 2.1 — shape and rebuild_from_episodic
# ---------------------------------------------------------------------------


def test_fragment_is_frozen_dataclass() -> None:
    frag = Fragment(contributor="Aki", text="rough warp", ts=1.0)
    assert dataclasses.is_dataclass(frag)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frag.text = "different"  # type: ignore[misc]


def test_fragment_requires_all_fields() -> None:
    with pytest.raises(TypeError):
        Fragment(contributor="Aki")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Fragment(text="warp", ts=1.0)  # type: ignore[call-arg]


def test_configured_wips_is_nonempty_tuple_of_strings() -> None:
    """The v0.2 set is configured at module level — agent-spawnable
    WIPs are explicitly out of scope (ADR 0003).
    """
    assert isinstance(CONFIGURED_WIPS, tuple)
    assert len(CONFIGURED_WIPS) >= 1
    for name in CONFIGURED_WIPS:
        assert isinstance(name, str)
        assert name.startswith("workshop.")


def test_empty_log_yields_all_wips_forming(tmp_path: Path) -> None:
    """A fresh data dir contains no contribute events. Every
    configured WIP exists, with zero fragments, in the ``forming``
    phase, at last_activity_ts == 0.0.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj = WorkshopProjection(ep)
    wips = {w.name: w for w in proj.wips()}
    assert set(wips.keys()) == set(CONFIGURED_WIPS)
    for w in wips.values():
        assert w.fragments == []
        assert w.phase == "forming"
        assert w.last_activity_ts == 0.0


def test_rebuild_collects_fragments_for_named_wip(tmp_path: Path) -> None:
    """Each contribute event with ``target`` matching a configured WIP
    becomes one Fragment on that WIP, in chronological order.
    Events targeting an unknown WIP are silently dropped (handled
    upstream by parse_action's WIP-name validation; the projection
    is defensive).
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_contribute(ep, actor="Aki", wip=target, fragment="rough warp", ts=1.0)
        _seed_contribute(ep, actor="Bo", wip=target, fragment="blue stitching", ts=2.0)
        _seed_contribute(ep, actor="Aki", wip="workshop.does-not-exist", fragment="dropped", ts=3.0)
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert [f.text for f in wip.fragments] == ["rough warp", "blue stitching"]
    assert [f.contributor for f in wip.fragments] == ["Aki", "Bo"]
    assert wip.last_activity_ts == 2.0


def test_rebuild_skips_empty_fragments(tmp_path: Path) -> None:
    """A contribute event with no ``fragment`` payload (whitespace,
    missing, None) is recorded for audit in episodic but does NOT
    materialise as a Fragment on the projection.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_contribute(ep, actor="Aki", wip=target, fragment="", ts=1.0)
        _seed_contribute(ep, actor="Bo", wip=target, fragment="   ", ts=2.0)
        ep.append(
            actor="Cy",
            action="contribute",
            target=target,
            payload={"thought": "no fragment"},  # no 'fragment' key at all
            ts=3.0,
        )
        _seed_contribute(ep, actor="Aki", wip=target, fragment="real", ts=4.0)
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert [f.text for f in wip.fragments] == ["real"]


def test_rebuild_ignores_non_contribute_actions(tmp_path: Path) -> None:
    """Speak / craft / study / rest / travel events never produce
    workshop fragments, even if their target is a configured WIP.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        for action in ("speak", "craft", "study", "rest", "travel"):
            ep.append(
                actor="Aki",
                action=action,
                target=target,
                payload={"fragment": "leak"},
                ts=1.0,
            )
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert wip.fragments == []


# ---------------------------------------------------------------------------
# Slice 2.2 — phase transitions (deterministic, table-driven)
# ---------------------------------------------------------------------------


def test_single_fragment_stays_forming(tmp_path: Path) -> None:
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_contribute(ep, actor="Aki", wip=target, fragment="warp", ts=1.0)
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert wip.phase == "forming"


def test_two_contributors_promote_to_developing(tmp_path: Path) -> None:
    """Phase transitions are deterministic: ``developing`` triggers
    when the WIP has at least DEVELOPING_CONTRIBUTOR_FLOOR distinct
    contributors OR at least DEVELOPING_FRAGMENT_FLOOR fragments.
    Two contributors with one fragment each crosses the contributor
    floor.
    """
    assert DEVELOPING_CONTRIBUTOR_FLOOR == 2
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_contribute(ep, actor="Aki", wip=target, fragment="warp", ts=1.0)
        _seed_contribute(ep, actor="Bo", wip=target, fragment="weft", ts=2.0)
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert wip.phase == "developing"


def test_many_fragments_from_one_contributor_promote_to_developing(tmp_path: Path) -> None:
    """Fragment floor is the second route to developing — a single
    contributor adding DEVELOPING_FRAGMENT_FLOOR fragments without
    peers still advances the WIP.
    """
    assert DEVELOPING_FRAGMENT_FLOOR == 3
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        for i in range(DEVELOPING_FRAGMENT_FLOOR):
            _seed_contribute(ep, actor="Aki", wip=target, fragment=f"frag-{i}", ts=float(i))
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert wip.phase == "developing"


def test_complete_fragment_floor_triggers_complete(tmp_path: Path) -> None:
    """The COMPLETE_FRAGMENT_FLOOR threshold ends the WIP; the
    projection no longer accepts further fragments into this WIP.
    """
    assert COMPLETE_FRAGMENT_FLOOR > DEVELOPING_FRAGMENT_FLOOR
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        for i in range(COMPLETE_FRAGMENT_FLOOR):
            actor = "Aki" if i % 2 == 0 else "Bo"
            _seed_contribute(ep, actor=actor, wip=target, fragment=f"frag-{i}", ts=float(i))
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert wip.phase == "complete"
    assert len(wip.fragments) == COMPLETE_FRAGMENT_FLOOR


def test_post_complete_fragments_are_ignored(tmp_path: Path) -> None:
    """A contribute event arriving after the WIP is already complete
    does not append to that WIP. (Operators may wish to read it from
    the event log audit; the projection is closed.)
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # Fill to complete.
        for i in range(COMPLETE_FRAGMENT_FLOOR):
            actor = "Aki" if i % 2 == 0 else "Bo"
            _seed_contribute(ep, actor=actor, wip=target, fragment=f"frag-{i}", ts=float(i))
        # One more after.
        _seed_contribute(
            ep,
            actor="Cy",
            wip=target,
            fragment="too-late",
            ts=float(COMPLETE_FRAGMENT_FLOOR + 1),
        )
        proj = WorkshopProjection(ep)
    wip = next(w for w in proj.wips() if w.name == target)
    assert wip.phase == "complete"
    assert len(wip.fragments) == COMPLETE_FRAGMENT_FLOOR
    for f in wip.fragments:
        assert f.text != "too-late"


# ---------------------------------------------------------------------------
# Slice 2.3 — stale_to_complete (clock-driven, no LLM)
# ---------------------------------------------------------------------------


def test_stale_to_complete_returns_names_past_timeout(tmp_path: Path) -> None:
    """A WIP with non-zero fragments whose last_activity_ts is older
    than ``timeout_s`` is returned by stale_to_complete. WIPs with no
    activity at all (last_activity_ts == 0.0) are NOT considered
    stale; they're untouched.
    """
    target_active = CONFIGURED_WIPS[0]
    target_stale = CONFIGURED_WIPS[1]
    now = time.time()
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_contribute(ep, actor="Aki", wip=target_active, fragment="recent", ts=now - 10.0)
        _seed_contribute(
            ep, actor="Bo", wip=target_stale, fragment="old", ts=now - 7200.0
        )  # 2 hours old
        proj = WorkshopProjection(ep)
    stale = proj.stale_to_complete(now_ts=now, timeout_s=3600.0)
    assert target_stale in stale
    assert target_active not in stale


def test_stale_to_complete_skips_already_complete(tmp_path: Path) -> None:
    """An already-complete WIP is not in the stale set — there is
    nothing to transition.
    """
    target = CONFIGURED_WIPS[0]
    now = time.time()
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        for i in range(COMPLETE_FRAGMENT_FLOOR):
            actor = "Aki" if i % 2 == 0 else "Bo"
            _seed_contribute(
                ep,
                actor=actor,
                wip=target,
                fragment=f"frag-{i}",
                ts=now - 7200.0 + i,
            )
        proj = WorkshopProjection(ep)
    stale = proj.stale_to_complete(now_ts=now, timeout_s=3600.0)
    assert target not in stale


def test_stale_to_complete_skips_empty_wips(tmp_path: Path) -> None:
    """WIPs with no fragments are not stale. Cold-start guard."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj = WorkshopProjection(ep)
    stale = proj.stale_to_complete(now_ts=time.time() + 1e9, timeout_s=3600.0)
    assert stale == []


# ---------------------------------------------------------------------------
# In-memory update path: on_contribute_event matches rebuild_from_episodic
# ---------------------------------------------------------------------------


def test_on_contribute_event_matches_rebuild(tmp_path: Path) -> None:
    """``on_contribute_event`` is the hot-path called per-tick; it
    must produce the same projection state as a cold rebuild from
    the log so a process restart never changes what agents see.
    """
    target = CONFIGURED_WIPS[0]
    events_data = [
        ("Aki", target, "warp", 1.0),
        ("Bo", target, "weft", 2.0),
        ("Aki", target, "blue", 3.0),
    ]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj_hot = WorkshopProjection(ep)
        for actor, wip, fragment, ts in events_data:
            _seed_contribute(ep, actor=actor, wip=wip, fragment=fragment, ts=ts)
            # Fetch the most recent event and feed it.
            recent = ep.last(1)[0]
            proj_hot.on_contribute_event(recent)
        proj_cold = WorkshopProjection(ep)

    hot = next(w for w in proj_hot.wips() if w.name == target)
    cold = next(w for w in proj_cold.wips() if w.name == target)
    assert [(f.contributor, f.text) for f in hot.fragments] == [
        (f.contributor, f.text) for f in cold.fragments
    ]
    assert hot.phase == cold.phase
    assert hot.last_activity_ts == cold.last_activity_ts


def test_wip_dataclass_exposes_contributors(tmp_path: Path) -> None:
    """A WIP exposes its distinct contributor set in insertion order —
    used by the per-receiver redaction in build_context.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_contribute(ep, actor="Aki", wip=target, fragment="a", ts=1.0)
        _seed_contribute(ep, actor="Bo", wip=target, fragment="b", ts=2.0)
        _seed_contribute(ep, actor="Aki", wip=target, fragment="c", ts=3.0)
        proj = WorkshopProjection(ep)
    wip: WIP = next(w for w in proj.wips() if w.name == target)
    assert wip.contributors() == ("Aki", "Bo")

"""Workshop transition tracker — ADR 0005 Decision 2.

When a WIP transitions to phase=complete, WorkshopProjection records
the WIP name in a pending-transition set. The run-loop drains the set
each tick and uses it to trigger an opportunistic harvester flush
(subject to a tick throttle). The set is cleared on drain.
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.episodic import EpisodicMemory
from microverse.world.workshop import WorkshopProjection


def _make_workshop(tmp_path: Path) -> tuple[WorkshopProjection, EpisodicMemory]:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    wp = WorkshopProjection(em)
    return wp, em


def test_drain_empty_when_no_completions(tmp_path: Path) -> None:
    wp, em = _make_workshop(tmp_path)
    try:
        assert wp.drain_complete_transitions() == set()
    finally:
        em.close()


def test_has_complete_transitions_peeks_without_clearing(tmp_path: Path) -> None:
    """has_complete_transitions() must NOT consume the edge signal.
    The run-loop's flush gate needs a peek so a throttle-blocked tick
    can re-check the edge on the next eligible flush.
    """
    wp, em = _make_workshop(tmp_path)
    try:
        from microverse.memory.episodic import Event
        from microverse.world.workshop import COMPLETE_FRAGMENT_FLOOR, CONFIGURED_WIPS

        wip_name = CONFIGURED_WIPS[0]
        text = "x" * 200
        for i in range(COMPLETE_FRAGMENT_FLOOR + 1):
            ev_id = em.append(
                actor=f"agent{i % 3}",
                action="contribute",
                target=wip_name,
                payload={"fragment": text, "thought": "", "artifact": text},
            )
            wp.on_event(
                Event(
                    id=ev_id,
                    ts=0.0,
                    actor=f"agent{i % 3}",
                    action="contribute",
                    target=wip_name,
                    payload={"fragment": text, "thought": "", "artifact": text},
                )
            )

        # Peek does not clear.
        assert wp.has_complete_transitions() is True
        assert wp.has_complete_transitions() is True
        # Drain consumes.
        assert wip_name in wp.drain_complete_transitions()
        assert wp.has_complete_transitions() is False
    finally:
        em.close()


def test_drain_returns_completed_wip(tmp_path: Path) -> None:
    """When a WIP transitions into 'complete', drain returns its name."""
    wp, em = _make_workshop(tmp_path)
    try:
        # Drive enough contributions from distinct contributors to push
        # one WIP into 'complete'. CONFIGURED_WIPS[0] is the first WIP;
        # COMPLETE_FRAGMENT_FLOOR is the activation threshold.
        from microverse.world.workshop import COMPLETE_FRAGMENT_FLOOR, CONFIGURED_WIPS

        wip_name = CONFIGURED_WIPS[0]
        text = "x" * 200  # past MIN_FRAGMENT_CHARS
        for i in range(COMPLETE_FRAGMENT_FLOOR + 1):
            ev_id = em.append(
                actor=f"agent{i % 3}",
                action="contribute",
                target=wip_name,
                payload={"fragment": text, "thought": "", "artifact": text},
            )
            from microverse.memory.episodic import Event

            evt = Event(
                id=ev_id,
                ts=0.0,
                actor=f"agent{i % 3}",
                action="contribute",
                target=wip_name,
                payload={"fragment": text, "thought": "", "artifact": text},
            )
            wp.on_event(evt)

        transitions = wp.drain_complete_transitions()
        assert wip_name in transitions
        # Second drain is empty (cleared on read).
        assert wp.drain_complete_transitions() == set()
    finally:
        em.close()


def test_drain_only_fires_once_per_transition(tmp_path: Path) -> None:
    """Continuing to contribute to a WIP that is already complete must
    NOT re-bump the transition set — the transition is a one-shot
    edge, not a level signal."""
    wp, em = _make_workshop(tmp_path)
    try:
        from microverse.memory.episodic import Event
        from microverse.world.workshop import COMPLETE_FRAGMENT_FLOOR, CONFIGURED_WIPS

        wip_name = CONFIGURED_WIPS[0]
        text = "x" * 200
        for i in range(COMPLETE_FRAGMENT_FLOOR + 1):
            ev_id = em.append(
                actor=f"agent{i % 3}",
                action="contribute",
                target=wip_name,
                payload={"fragment": text, "thought": "", "artifact": text},
            )
            wp.on_event(
                Event(
                    id=ev_id,
                    ts=0.0,
                    actor=f"agent{i % 3}",
                    action="contribute",
                    target=wip_name,
                    payload={"fragment": text, "thought": "", "artifact": text},
                )
            )
        wp.drain_complete_transitions()  # consume the edge

        # Another contribute against the now-complete WIP must not
        # re-trigger the edge.
        ev_id = em.append(
            actor="agent99",
            action="contribute",
            target=wip_name,
            payload={"fragment": text, "thought": "", "artifact": text},
        )
        wp.on_event(
            Event(
                id=ev_id,
                ts=0.0,
                actor="agent99",
                action="contribute",
                target=wip_name,
                payload={"fragment": text, "thought": "", "artifact": text},
            )
        )
        assert wp.drain_complete_transitions() == set()
    finally:
        em.close()

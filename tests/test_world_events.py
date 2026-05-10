"""Path-3 slice 1: ``WorldContext.world_events`` schema.

``world_events`` carries factual world events (weather, season shifts,
stranger arrivals) since the agent's last own-tick. Crucially, it
NEVER carries any agent action — that exclusion is the point. This
slice pins the field's existence and shape; slice 2 adds the
``_build_world_events`` helper that filters episodic on
``actor == "world"``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from microverse.agents.base import WorldContext
from microverse.memory import _build_world_events
from microverse.memory.episodic import EpisodicMemory


def test_world_context_default_world_events_is_empty_tuple() -> None:
    """An agent on cold-start (or with no recent world events) sees
    an empty tuple, not ``None``.
    """
    world = WorldContext()
    assert world.world_events == ()
    assert isinstance(world.world_events, tuple)


def test_world_context_round_trips_world_events() -> None:
    """Order is preserved (newest-first or oldest-first is the
    builder's choice in slice 2; this slice only asserts the
    field accepts and returns the tuple verbatim).
    """
    events = ("[world] weather.storm", "[world] stranger.arrived")
    world = WorldContext(world_events=events)
    assert world.world_events == events
    assert world.world_events[0] == "[world] weather.storm"


def test_world_context_world_events_is_immutable() -> None:
    """Frozen dataclass: reassignment must raise. Bound on accidental
    mutation between assembly and render.
    """
    world = WorldContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        world.world_events = ("[world] weather.storm",)  # type: ignore[misc]


def test_world_context_accepts_both_new_fields_together() -> None:
    """Slice 1 lands schema + slice 2 lands builders; this test pins
    that ``peer_inbox`` and ``world_events`` co-exist and are
    independently settable.
    """
    from microverse.agents.base import PeerSpeech

    world = WorldContext(
        peer_inbox=(PeerSpeech(speaker="Bo", utterance="hi"),),
        world_events=("[world] weather.storm",),
    )
    assert len(world.peer_inbox) == 1
    assert len(world.world_events) == 1


# ---------------------------------------------------------------------------
# Slice 2: ``_build_world_events`` helper.
#
# Filters the episodic log for ``actor == "world"`` events since a
# watermark and renders them as ``"[world] <action>"`` strings.
# Crucially this is the ONLY surface for world-state-change visibility
# in the Path-3 stateless tick: agents don't read self-history, so
# weather and arrival events must come through here.
# ---------------------------------------------------------------------------


def _seed_world_event(ep: EpisodicMemory, *, action: str, ts: float) -> None:
    ep.append(actor="world", action=action, target=None, payload={}, ts=ts)


def _seed_agent_event(ep: EpisodicMemory, *, actor: str, action: str, ts: float) -> None:
    ep.append(
        actor=actor,
        action=action,
        target=None,
        payload={"thought": "x"},
        ts=ts,
    )


def test_build_world_events_filters_by_world_actor(tmp_path: Path) -> None:
    """Only events with ``actor == "world"`` surface; agent actions
    are excluded even when their action string happens to look
    weather-like (defence-in-depth).
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_world_event(ep, action="weather.storm", ts=100.0)
        _seed_agent_event(ep, actor="Aki", action="craft", ts=105.0)
        _seed_agent_event(ep, actor="Bo", action="weather.storm", ts=108.0)
        events = _build_world_events(ep, since_ts=0.0)
    assert events == ("[world] weather.storm",), (
        f"only actor=='world' events surface, got {events!r}"
    )


def test_build_world_events_filters_by_since_ts(tmp_path: Path) -> None:
    """Stale world events (before since_ts) must NOT surface — the
    helper's contract is "world events the agent has not yet seen".
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_world_event(ep, action="weather.drought", ts=50.0)
        _seed_world_event(ep, action="weather.storm", ts=110.0)
        events = _build_world_events(ep, since_ts=100.0)
    assert events == ("[world] weather.storm",), f"stale world event must drop, got {events!r}"


def test_build_world_events_returns_chronological_order(tmp_path: Path) -> None:
    """Multiple world events render oldest-first so the prompt reads
    in causal order (storm → drought clears, etc.).
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_world_event(ep, action="weather.storm", ts=100.0)
        _seed_world_event(ep, action="weather.clear", ts=110.0)
        _seed_world_event(ep, action="stranger.arrived", ts=120.0)
        events = _build_world_events(ep, since_ts=0.0)
    assert events == (
        "[world] weather.storm",
        "[world] weather.clear",
        "[world] stranger.arrived",
    ), f"world events must be chronological, got {events!r}"


def test_build_world_events_empty_when_no_world_events(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_agent_event(ep, actor="Aki", action="craft", ts=100.0)
        events = _build_world_events(ep, since_ts=0.0)
    assert events == ()

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

import pytest

from microverse.agents.base import WorldContext


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

"""Seeded weather/event scheduler — emits drought / comet / festival
into the episodic log on a deterministic, seedable schedule.

Phase 4a contract:
  - ``WorldClock(seed)`` produces the same event sequence across runs.
  - ``advance(into_episodic, ticks_elapsed)`` writes 0 or more
    ``weather.*`` events to the episodic log based on cumulative ticks.
  - Events use the actor name ``"world"`` so they're distinguishable
    from agent actions.
  - The schedule is configurable via constructor: ``mean_interval``
    ticks between events (default 200).
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.episodic import EpisodicMemory
from microverse.world.clock import KNOWN_EVENTS, WorldClock


def test_advance_zero_ticks_writes_nothing(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        clock = WorldClock(seed=0)
        clock.advance(ep, ticks_elapsed=0)
        assert ep.count() == 0


def test_advance_eventually_emits_a_world_event(tmp_path: Path):
    """Given enough ticks (>> mean_interval), at least one event lands."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        clock = WorldClock(seed=42, mean_interval=10)
        for _ in range(50):
            clock.advance(ep, ticks_elapsed=1)
        events = ep.last(100)
        world_events = [e for e in events if e.actor == "world"]
        assert len(world_events) >= 1


def test_emitted_events_are_in_known_set(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        clock = WorldClock(seed=42, mean_interval=5)
        for _ in range(100):
            clock.advance(ep, ticks_elapsed=1)
        events = ep.last(200)
        kinds = {e.action for e in events if e.actor == "world"}
        assert kinds.issubset(KNOWN_EVENTS)
        assert kinds, "expected at least one world event with mean_interval=5"


def test_seed_determines_sequence(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep1.sqlite") as ep1:
        clock1 = WorldClock(seed=7, mean_interval=8)
        for _ in range(60):
            clock1.advance(ep1, ticks_elapsed=1)
        seq1 = [(e.action, e.payload) for e in ep1.last(200) if e.actor == "world"]

    with EpisodicMemory(tmp_path / "ep2.sqlite") as ep2:
        clock2 = WorldClock(seed=7, mean_interval=8)
        for _ in range(60):
            clock2.advance(ep2, ticks_elapsed=1)
        seq2 = [(e.action, e.payload) for e in ep2.last(200) if e.actor == "world"]

    assert seq1 == seq2


def test_different_seeds_diverge(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep1.sqlite") as ep1:
        c1 = WorldClock(seed=1, mean_interval=8)
        for _ in range(60):
            c1.advance(ep1, ticks_elapsed=1)
        seq1 = [e.action for e in ep1.last(200) if e.actor == "world"]

    with EpisodicMemory(tmp_path / "ep2.sqlite") as ep2:
        c2 = WorldClock(seed=2, mean_interval=8)
        for _ in range(60):
            c2.advance(ep2, ticks_elapsed=1)
        seq2 = [e.action for e in ep2.last(200) if e.actor == "world"]

    # Different seeds should at least sometimes produce different sequences.
    assert seq1 != seq2 or seq1 == [] and seq2 == []  # accept "both empty" too


def test_payload_includes_kind(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        clock = WorldClock(seed=1, mean_interval=5)
        for _ in range(40):
            clock.advance(ep, ticks_elapsed=1)
        events = [e for e in ep.last(50) if e.actor == "world"]
        for e in events:
            assert e.payload.get("kind") == e.action.replace("weather.", "")

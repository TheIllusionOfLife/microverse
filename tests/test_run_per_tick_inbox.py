"""Slice 3: per-tick assembly of peer_inbox + world_events in run.py.

The Path-3 stateless tick contract: each tick builds a fresh
``WorldContext`` carrying only the bounded peer + world view since
the agent's last own-tick. This is implemented by:

  1. A new ``_last_tick_ts: dict[str, float]`` watermark in ``run()``
     that records when each agent last ran.
  2. A new ``_build_per_tick_world_base`` helper that composes
     ``peers_today`` + ``peer_inbox`` + ``world_events`` +
     ``engagement_hint`` / ``required_target`` into a fresh
     ``WorldContext`` ready for ``build_context`` to add lore.
  3. A post-tick update bumping the watermark so the NEXT call drains
     events the agent has already seen.

This file pins (1)-(3) directly. The integration test runs a tiny
3-agent scenario and asserts the inbox/world drain semantics work
across multiple ticks.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from microverse.agents.artisan import Artisan
from microverse.agents.base import Agent, PeerSpeech
from microverse.agents.scholar import Scholar
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.run import _build_per_tick_world_base


def _seed_speak(
    ep: EpisodicMemory,
    *,
    actor: str,
    target: str | None,
    thought: str,
    ts: float,
) -> None:
    ep.append(
        actor=actor,
        action="speak",
        target=target,
        payload={"thought": thought, "artifact": None},
        ts=ts,
    )


def _seed_world(ep: EpisodicMemory, *, action: str, ts: float) -> None:
    ep.append(actor="world", action=action, target=None, payload={}, ts=ts)


def _agent(name: str = "Aki") -> Agent:
    return Artisan(name=name)


def test_world_base_populates_peer_inbox_and_world_events(tmp_path: Path) -> None:
    """A peer speak and a world event in the window both surface."""
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Bo", target="Aki", thought="storm coming", ts=100.0)
        _seed_world(ep, action="weather.storm", ts=110.0)
        world = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo", "Cy"),
            last_tick_ts=50.0,
            metrics=metrics,
        )
    assert world.peers_today == ("Bo", "Cy")
    assert len(world.peer_inbox) == 1
    assert world.peer_inbox[0].speaker == "Bo"
    assert world.peer_inbox[0].utterance == "storm coming"
    assert world.world_events == ("[world] weather.storm",)


def test_world_base_drops_events_before_watermark(tmp_path: Path) -> None:
    """Events older than ``last_tick_ts`` must NOT surface — the
    one-shot drain semantics live entirely in the timestamp filter
    so the helper itself stays pure.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Bo", target="Aki", thought="old greeting", ts=50.0)
        _seed_world(ep, action="weather.drought", ts=60.0)
        _seed_speak(ep, actor="Cy", target="Aki", thought="fresh greeting", ts=110.0)
        world = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo", "Cy"),
            last_tick_ts=100.0,
            metrics=metrics,
        )
    assert len(world.peer_inbox) == 1
    assert world.peer_inbox[0].speaker == "Cy"
    assert world.world_events == ()


def test_world_base_passes_engagement_through(tmp_path: Path) -> None:
    """``engagement_hint`` and ``required_target`` ride through
    untouched — Layer-G's exogenous-nudge contract is preserved.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        world = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo",),
            last_tick_ts=0.0,
            engagement_hint="You must address Bo this tick.",
            required_target="Bo",
            metrics=metrics,
        )
    assert world.engagement_hint == "You must address Bo this tick."
    assert world.required_target == "Bo"


def test_world_base_excludes_self_speaks_via_helper(tmp_path: Path) -> None:
    """Defence-in-depth: a self-speak should not appear in the
    agent's own peer_inbox even if it lands in the watermark window.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Aki", target="Aki", thought="self talk", ts=100.0)
        _seed_speak(ep, actor="Bo", target="Aki", thought="from Bo", ts=110.0)
        world = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo",),
            last_tick_ts=0.0,
            metrics=metrics,
        )
    speakers = [s.speaker for s in world.peer_inbox]
    assert "Aki" not in speakers
    assert speakers == ["Bo"]


# ---------------------------------------------------------------------------
# Tick loop integration: the watermark advances per-agent so the next
# tick drains the inbox.
# ---------------------------------------------------------------------------


def _craft_chat() -> dict:
    return {
        "content": (
            '{"thought": "shape a small bowl", "action": "craft", '
            '"target": null, "artifact": "a small bowl"}'
        ),
        "thinking": "",
        "raw": {},
    }


@pytest.mark.usefixtures("metrics")
def test_run_advances_watermark_per_agent(tmp_path: Path) -> None:
    """Across two ticks for the same agent, the watermark advances
    so peer events committed BEFORE the first tick are NOT visible
    on the second tick. This is the one-shot drain.
    """
    captured_worlds: list[tuple[str, tuple[PeerSpeech, ...]]] = []

    real_artisan_think = Artisan.think
    real_scholar_think = Scholar.think

    def _capture_artisan_think(self: Artisan, world):
        captured_worlds.append((self.name, world.peer_inbox))
        return real_artisan_think(self, world)

    def _capture_scholar_think(self: Scholar, world):
        captured_worlds.append((self.name, world.peer_inbox))
        return real_scholar_think(self, world)

    canned = _craft_chat()
    with (
        patch("microverse.agents.artisan.chat", return_value=canned),
        patch("microverse.agents.scholar.chat", return_value=canned),
        patch.object(Artisan, "think", _capture_artisan_think),
        patch.object(Scholar, "think", _capture_scholar_think),
    ):
        # Pre-seed a peer-to-Aki speak BEFORE the run starts; with
        # the run-start watermark at runtime, this should be visible
        # on Aki's first tick but drained from her view on the
        # second tick.
        from microverse.run import run

        data_dir = tmp_path / "data"
        harvest_dir = tmp_path / "harvest"
        data_dir.mkdir()
        harvest_dir.mkdir()
        ep_path = data_dir / "episodic.sqlite"
        with EpisodicMemory(ep_path) as ep:
            _seed_speak(ep, actor="Cy", target="Aki", thought="hello", ts=time.time() - 1.0)

        # Run two ticks; with default Aki+Cy roster, both tick.
        run(ticks=2, tempo=0, data_dir=data_dir, harvest_dir=harvest_dir)

    # On the FIRST Aki tick the inbox should have the pre-seeded
    # "hello" from Cy. On the SECOND Aki tick the watermark has
    # advanced past that speak, so the inbox is empty (assuming Cy
    # did not speak to Aki in between — Cy crafted in the canned
    # response).
    aki_views = [inbox for name, inbox in captured_worlds if name == "Aki"]
    assert len(aki_views) >= 1
    first_aki = aki_views[0]
    speakers = [s.speaker for s in first_aki]
    assert "Cy" in speakers, f"Aki's first tick must see the pre-seeded hello, got {first_aki!r}"
    if len(aki_views) >= 2:
        second_aki = aki_views[1]
        assert all(s.utterance != "hello" for s in second_aki), (
            f"Aki's second tick must NOT re-see the drained 'hello', got {second_aki!r}"
        )

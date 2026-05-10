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

from pathlib import Path
from unittest.mock import patch

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


def _speak_to_aki_chat() -> dict:
    # Utterance must NOT contain "Aki" as a whole word — the
    # peer_inbox name-filter would otherwise drop it. The integration
    # surface here is the runtime watermark, not the filter.
    return {
        "content": (
            '{"thought": "the river is rising rapidly", "action": "speak", '
            '"target": "Aki", "artifact": null}'
        ),
        "thinking": "",
        "raw": {},
    }


def test_run_threads_watermark_so_peer_speeches_drain(tmp_path: Path) -> None:
    """Integration: across multiple ticks of the default Aki+Cy
    roster, Cy speaks to Aki every Cy-tick. The runtime's
    ``last_tick_ts`` watermark must advance per-agent so an Aki
    tick after a Cy speak shows the speak in inbox, while a later
    Aki tick (after the watermark has advanced past that speak)
    does NOT re-show it. This pins that ``run.py`` is using the
    helper correctly with per-agent watermarks, not a single
    global one.
    """
    captured: list[tuple[str, tuple[PeerSpeech, ...]]] = []

    real_artisan_think = Artisan.think
    real_scholar_think = Scholar.think

    def _capture_artisan(self: Artisan, world):
        captured.append((self.name, world.peer_inbox))
        return real_artisan_think(self, world)

    def _capture_scholar(self: Scholar, world):
        captured.append((self.name, world.peer_inbox))
        return real_scholar_think(self, world)

    aki_chat = _craft_chat()
    cy_chat = _speak_to_aki_chat()
    with (
        patch("microverse.agents.artisan.chat", return_value=aki_chat),
        patch("microverse.agents.scholar.chat", return_value=cy_chat),
        patch.object(Artisan, "think", _capture_artisan),
        patch.object(Scholar, "think", _capture_scholar),
    ):
        from microverse.run import run

        data_dir = tmp_path / "data"
        harvest_dir = tmp_path / "harvest"
        data_dir.mkdir()
        harvest_dir.mkdir()
        # Many ticks so we get multiple Aki ticks with at least one
        # Cy tick in between, demonstrating both visibility and drain.
        run(ticks=10, tempo=0, data_dir=data_dir, harvest_dir=harvest_dir)

    aki_views = [inbox for name, inbox in captured if name == "Aki"]
    # With the weighted scheduler favouring Aki (soul_tokens=100) and
    # Cy (70), Aki should get at least 4 of 10 ticks.
    assert len(aki_views) >= 2, f"need >=2 Aki ticks for this test, got {len(aki_views)}"

    # At least one Aki view must contain Cy's speak (visibility).
    assert any(any(s.speaker == "Cy" for s in view) for view in aki_views), (
        f"Aki must see Cy's speak on at least one tick, got {aki_views!r}"
    )
    # At least one Aki view AFTER seeing Cy must have an empty inbox
    # (drain semantics) — a fresh Aki tick with no Cy tick between
    # must not re-see prior speaks.
    saw_cy = False
    drained_after_seeing = False
    for view in aki_views:
        speakers = {s.speaker for s in view}
        if "Cy" in speakers:
            saw_cy = True
            continue
        if saw_cy and not speakers:
            drained_after_seeing = True
            break
    assert drained_after_seeing, (
        f"after Aki sees Cy, a later tick with no new Cy speak must drain, got {aki_views!r}"
    )

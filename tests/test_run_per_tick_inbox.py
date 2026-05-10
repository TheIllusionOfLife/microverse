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

This file pins (1)-(3) directly. The drain test exercises the
helper at three watermark points to assert the per-agent advance
contract — independent of scheduler RNG, the LLM, and the Trader,
so it stays deterministic in CI where Ollama is unavailable.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.artisan import Artisan
from microverse.agents.base import Agent
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
# Watermark-advance contract: simulate three ticks of the same agent
# without depending on the scheduler RNG, the LLM, or the Trader.
# ---------------------------------------------------------------------------


def test_watermark_drain_across_three_calls(tmp_path: Path) -> None:
    """Three consecutive helper calls for the same agent. Between
    tick 1 and tick 2 a peer speaks; on tick 2 the inbox holds that
    speak. After tick 2 the runtime advances the watermark to tick
    2's timestamp; on tick 3 (no new speak in between) the inbox
    drains.

    This mirrors the per-agent watermark dynamics in ``run.py``
    without invoking the full tick loop. The CI-flaky integration
    test was replaced with this deterministic unit because the
    weighted scheduler's RNG is not seedable across the test
    boundary and the Trader's flush() reaches Ollama (absent in CI).
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # Tick 1 — empty world, empty inbox.
        world_1 = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo",),
            last_tick_ts=100.0,
            metrics=metrics,
        )
        assert world_1.peer_inbox == (), (
            f"cold-start inbox must be empty, got {world_1.peer_inbox!r}"
        )

        # Bo speaks to Aki between tick 1 and tick 2.
        _seed_speak(ep, actor="Bo", target="Aki", thought="storm rising", ts=120.0)

        # Tick 2 — Bo's speak is visible because it landed after the
        # tick-1 watermark.
        watermark_after_tick_1 = 110.0
        world_2 = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo",),
            last_tick_ts=watermark_after_tick_1,
            metrics=metrics,
        )
        assert len(world_2.peer_inbox) == 1, (
            f"speak must surface on tick 2, got {world_2.peer_inbox!r}"
        )
        assert world_2.peer_inbox[0].speaker == "Bo"

        # Tick 3 — nothing new since tick 2; watermark advances past
        # Bo's speak so the inbox drains.
        watermark_after_tick_2 = 130.0
        world_3 = _build_per_tick_world_base(
            episodic=ep,
            agent=_agent("Aki"),
            peers=("Bo",),
            last_tick_ts=watermark_after_tick_2,
            metrics=metrics,
        )
        assert world_3.peer_inbox == (), (
            f"watermark advanced past Bo's speak; tick 3 must drain, got {world_3.peer_inbox!r}"
        )

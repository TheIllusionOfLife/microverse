"""Slice 6: ``_derive_topic`` no longer pattern-matches on agent name.

The prior fallback ``f"{agent.role} {agent.name}"`` seeded FTS5 lore
retrieval with the agent's own name, so any lore explicitly tagged
with that name surfaced for that agent. Path-3 closes this channel:
``_derive_topic`` returns weather + season state only. The agent's
name and role never reach FTS5.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.artisan import Artisan
from microverse.memory.episodic import EpisodicMemory
from microverse.run import _derive_topic


def test_derive_topic_uses_weather_when_present(tmp_path: Path) -> None:
    """A recent ``weather.*`` event still seeds the topic — that part
    of the contract is preserved. Just the agent-name fallback is
    removed.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="world", action="weather.storm", target=None, payload={})
        topic = _derive_topic(ep, Artisan(name="Aki"))
    assert "storm" in topic, f"weather kind must seed topic, got {topic!r}"


def test_derive_topic_does_not_include_agent_name(tmp_path: Path) -> None:
    """Even with a weather event present, the agent's name must NOT
    appear in the topic — FTS5 retrieval becomes name-blind.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="world", action="weather.storm", target=None, payload={})
        topic = _derive_topic(ep, Artisan(name="Aki"))
    assert "Aki" not in topic, f"agent name must not seed FTS5 topic, got {topic!r}"


def test_derive_topic_no_weather_uses_season_or_neutral(tmp_path: Path) -> None:
    """With no weather events at all, the fallback must NOT use the
    agent's role+name (the previous Layer-G behaviour). Instead the
    topic should be a neutral / season-derived seed.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        topic = _derive_topic(ep, Artisan(name="Aki"))
    assert "Aki" not in topic, f"agent name must not appear in fallback, got {topic!r}"
    assert "artisan" not in topic.lower(), f"agent role must not appear, got {topic!r}"


def test_derive_topic_constant_for_different_agents_in_same_world(tmp_path: Path) -> None:
    """Two agents looking at the same world must get the same topic —
    the topic is a function of the world, not the agent.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="world", action="weather.fog", target=None, payload={})
        t_aki = _derive_topic(ep, Artisan(name="Aki"))
        t_bo = _derive_topic(ep, Artisan(name="Bo"))
    assert t_aki == t_bo, f"topic must be world-keyed, not agent-keyed: {t_aki!r} vs {t_bo!r}"

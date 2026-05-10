"""Slice 5 / Path-3 contract: ``build_context`` produces no
autobiographical view of the receiving agent.

Comprehensive sweep: seed an agent's history with thoughts,
artifacts, and every action verb, then assert NONE of those
substrings can reach the receiver's per-tick prompt. The receiver's
own past is the LLM's introspective fuel; cutting every channel is
what the seven prior layers each tried and failed to do.

Coverage:
  * Self thoughts (the original Layer-G channel) — must not appear.
  * Self artifact texts (the artifact-text channel that emerged
    after Layer G shipped) — must not appear.
  * Self action verbs as past-tense self-summaries
    ("Aki crafted ...", "Aki spoke ...", "Aki rested", etc.) — must
    not appear in any rendered field.
  * Lore from FTS5 retrieval is community knowledge, not self
    history — Slice 6 adds the receiver-name redaction; this slice
    asserts the no-autobiography property is intact at the agent
    level (the lore channel is hardened separately).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from microverse.agents.base import WorldContext
from microverse.memory import build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory

_ALL_VERBS_PAST = ("crafted", "spoke", "studied", "rested", "traveled")
_ALL_VERBS_PRESENT = ("craft", "speak", "study", "rest", "travel")


def _seed_diverse_history(ep: EpisodicMemory, actor: str, n: int = 50) -> None:
    """Populate ``n`` events for ``actor`` covering every action with
    distinctive thoughts and artifacts so any leak is detectable.
    Timestamps are placed within the last hour so the 7-day window
    in ``build_context`` does not silently filter the seed away.
    """
    actions = ("craft", "speak", "study", "rest", "travel")
    base = time.time() - 60.0
    for i in range(n):
        action = actions[i % len(actions)]
        ep.append(
            actor=actor,
            action=action,
            target=actor if action == "speak" else None,
            payload={
                "thought": f"signature thought zeta-{i}",
                "artifact": f"signature artifact omega-{i}" if action == "craft" else None,
            },
            ts=base + i * 0.1,
        )


def _world_state_dump(world: WorldContext) -> str:
    """Concatenate every text-bearing field of ``world`` so substring
    assertions cover the full view that could possibly reach the
    renderer. ``recent_episodic`` is included even though Slice 4
    already cuts the template-side reading — the goal here is the
    SCHEMA contract: until Slice 5 strips the field, nothing the
    receiver-agent did themselves should appear in any field.
    """
    parts: list[str] = []
    parts.append(world.season)
    parts.append(world.weather)
    parts.extend(world.peers_today)
    for s in world.peer_inbox:
        parts.append(s.speaker)
        parts.append(s.utterance)
    parts.extend(world.world_events)
    parts.extend(world.lore_excerpt)
    parts.append(world.engagement_hint)
    if world.required_target is not None:
        parts.append(world.required_target)
    # Include recent_episodic in the dump so any residual self-history
    # in that field fails the sweep RED until Slice 5 strips the field.
    parts.extend(getattr(world, "recent_episodic", ()))
    return "\n".join(parts)


def test_build_context_drops_self_thoughts(tmp_path: Path) -> None:
    """No thought string from the agent's own past surfaces in the
    rendered context. The 50-event sweep must produce exactly zero
    substring matches across every text-bearing field of the
    returned WorldContext.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_diverse_history(ep, actor="Aki", n=50)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
        )
    dump = _world_state_dump(out)
    for i in range(50):
        marker = f"signature thought zeta-{i}"
        assert marker not in dump, (
            f"self thought {marker!r} leaked into context, dump head:\n{dump[:600]!r}"
        )


def test_build_context_drops_self_artifact_texts(tmp_path: Path) -> None:
    """The artifact-text channel that surfaced after Layer-G shipped
    must be closed: artifact bodies from the agent's own past crafts
    do not appear in any field of the returned WorldContext.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_diverse_history(ep, actor="Aki", n=50)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
        )
    dump = _world_state_dump(out)
    for i in range(50):
        marker = f"signature artifact omega-{i}"
        assert marker not in dump, (
            f"self artifact text {marker!r} leaked, dump head:\n{dump[:600]!r}"
        )


def test_build_context_drops_self_action_summaries(tmp_path: Path) -> None:
    """No "Aki crafted ...", "Aki spoke ...", "Aki studied", etc. line
    appears anywhere in the returned context. The compressed-run
    summaries from prior layers are fully gone.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_diverse_history(ep, actor="Aki", n=50)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
        )
    dump = _world_state_dump(out)
    for verb in _ALL_VERBS_PAST + _ALL_VERBS_PRESENT:
        # Self-action summary: pattern "Aki <verb>" anywhere.
        marker = f"Aki {verb}"
        assert marker not in dump, (
            f"self action summary {marker!r} leaked, dump head:\n{dump[:600]!r}"
        )


def test_build_context_recent_episodic_field_is_unused_or_empty(tmp_path: Path) -> None:
    """Either ``WorldContext`` no longer has ``recent_episodic`` (Slice
    5 schema removal) or it is unconditionally empty — both are
    acceptable end-states; the contract is that the prompt cannot
    see self events.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_diverse_history(ep, actor="Aki", n=50)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
        )
    field = getattr(out, "recent_episodic", ())
    assert field == (), f"recent_episodic must be removed or empty after Slice 5, got {field!r}"


@pytest.mark.parametrize("agent_name", ["Aki", "Bo", "Cy"])
def test_build_context_no_self_history_for_any_agent(tmp_path: Path, agent_name: str) -> None:
    """The contract holds for any agent name, not just Aki — the
    bounded view is structural, not name-specific.
    """
    with (
        EpisodicMemory(tmp_path / f"ep-{agent_name}.sqlite") as ep,
        SemanticMemory(tmp_path / f"se-{agent_name}.sqlite") as se,
    ):
        _seed_diverse_history(ep, actor=agent_name, n=30)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
        )
    dump = _world_state_dump(out)
    assert "signature thought zeta-0" not in dump
    assert "signature artifact omega-0" not in dump
    assert f"{agent_name} crafted" not in dump
    assert f"{agent_name} spoke" not in dump

"""Relationship ledger projection — ADR 0007 Phase 1 (Stage B).

``derive_relationships`` is a pure read model over the episodic WAL log
(pattern-twin of ``world.diversity``). It surfaces, per real peer, how
many times each addressed the other (``speak`` edges) and how many
committed scenes they co-authored. No new store, no write path.

Invariants pinned here:
  * speak edges counted in BOTH directions, full-history.
  * co-authorship derived ONLY from committed ``contribute`` events that
    share a ``scene_id`` — NEVER from ``scene.open`` (which logs
    *scheduled* authors before turns run and can abort, fabricating
    ties).
  * only peers in ``known_peers`` (the registered roster) are surfaced;
    ``world`` / ``scene`` / ``harvester`` and hallucinated speak targets
    are excluded (defence against autoescape=False prompt injection).
  * the agent never has a relationship with itself.
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.episodic import EpisodicMemory
from microverse.world.relationships import derive_relationships

ROSTER = ("Aki", "Cy")


def _speak(ep: EpisodicMemory, actor: str, target: str | None) -> None:
    ep.append(actor=actor, action="speak", target=target, payload={"thought": "hello"})


def _contribute(ep: EpisodicMemory, actor: str, scene_id: str | None) -> None:
    payload: dict[str, object] = {"fragment": "a fragment", "contribute_to": "workshop.loom"}
    if scene_id is not None:
        payload["scene_id"] = scene_id
    ep.append(actor=actor, action="contribute", target="workshop.loom", payload=payload)


def test_empty_log_yields_no_relationships(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        assert derive_relationships(ep, agent_name="Aki", known_peers=ROSTER) == ()


def test_speak_edges_counted_both_directions(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _speak(ep, "Cy", "Aki")  # Cy addressed Aki
        _speak(ep, "Cy", "Aki")
        _speak(ep, "Aki", "Cy")  # Aki addressed Cy
        facts = derive_relationships(ep, agent_name="Aki", known_peers=ROSTER)
    assert len(facts) == 1
    f = facts[0]
    assert f.peer == "Cy"
    assert f.addressed_you == 2
    assert f.you_addressed == 1
    assert f.co_authored == 0


def test_co_authorship_from_committed_contributes(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _contribute(ep, "Aki", "scene-1")
        _contribute(ep, "Cy", "scene-1")
        _contribute(ep, "Aki", "scene-2")
        _contribute(ep, "Cy", "scene-2")
        facts = derive_relationships(ep, agent_name="Aki", known_peers=ROSTER)
    assert len(facts) == 1
    assert facts[0].peer == "Cy"
    assert facts[0].co_authored == 2


def test_aborted_scene_scheduled_author_not_counted(tmp_path: Path) -> None:
    """scene.open names three scheduled authors, but if a co-author never
    commits a contribute (scene aborts), no tie is fabricated."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(
            actor="scene",
            action="scene.open",
            target="workshop.loom",
            payload={
                "scene_id": "scene-x",
                "turn1_author": "Aki",
                "turn2_author": "Cy",
                "turn3_author": "Aki",
                "wip_name": "workshop.loom",
            },
        )
        _contribute(ep, "Aki", "scene-x")  # only Aki actually contributed
        facts = derive_relationships(ep, agent_name="Aki", known_peers=ROSTER)
    assert facts == ()


def test_non_roster_and_hallucinated_targets_excluded(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _speak(ep, "Aki", "Ghost")  # hallucinated target, not in roster
        _speak(ep, "Aki", "world")  # non-agent actor name
        _speak(ep, "Aki", None)  # untargeted speak
        facts = derive_relationships(ep, agent_name="Aki", known_peers=ROSTER)
    assert facts == ()


def test_agent_has_no_self_relationship(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _speak(ep, "Aki", "Aki")
        _contribute(ep, "Aki", "scene-solo")
        facts = derive_relationships(ep, agent_name="Aki", known_peers=ROSTER)
    assert all(f.peer != "Aki" for f in facts)
    assert facts == ()


def test_sorted_by_interaction_strength_desc(tmp_path: Path) -> None:
    roster = ("Aki", "Cy", "Bo")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _speak(ep, "Aki", "Bo")  # 1 interaction with Bo
        for _ in range(5):
            _speak(ep, "Aki", "Cy")  # 5 interactions with Cy
        facts = derive_relationships(ep, agent_name="Aki", known_peers=roster)
    assert [f.peer for f in facts] == ["Cy", "Bo"]

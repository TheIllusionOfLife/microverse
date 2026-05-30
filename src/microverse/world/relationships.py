"""Relationship ledger — ADR 0007 Phase 1 (Pillar 1), derive-on-read.

A pure projection over the episodic WAL log (pattern-twin of
``world.diversity``): no new store, no write path, so it is automatically
replay-deterministic and crash-consistent. The WAL log is the single
source of truth; the ledger is recomputed each tick from it.

What the ADR wants ("who helped whom, who built with whom") is already in
the log: ``speak`` targets and committed scene co-authorship. We surface,
per real roster peer, the reciprocal address counts and the number of
committed scenes the two co-authored.

Path-3 safety: every field of ``RelationFact`` is an integer count or a
whitelisted roster name. No free text from the agent's own past flows
through, and a hallucinated speak target can never reach the prompt
because peers are filtered against the registered roster.
"""

from __future__ import annotations

from collections import Counter

from microverse.agents.base import RelationFact
from microverse.memory.episodic import EpisodicMemory


def derive_relationships(
    episodic: EpisodicMemory,
    *,
    agent_name: str,
    known_peers: tuple[str, ...] | frozenset[str] | set[str],
) -> tuple[RelationFact, ...]:
    """Build ``agent_name``'s relationship ledger from the full episodic
    history.

    ``known_peers`` is the registered roster (the scheduler's agent
    names). Only peers in this set surface — ``world`` / ``scene`` /
    ``harvester`` and any hallucinated ``Action.target`` are excluded.
    The agent never has a relationship with itself.

    Returns one ``RelationFact`` per peer the agent has a measurable tie
    to, sorted by total interaction strength descending (ties broken by
    peer name).
    """
    peers = {p for p in known_peers if p != agent_name}
    if not peers:
        return ()

    speak = episodic.speak_edge_counts()
    scenes = episodic.scene_contributor_sets()

    co_authored: Counter[str] = Counter()
    for contributors in scenes.values():
        if agent_name not in contributors:
            continue
        for other in contributors:
            if other != agent_name and other in peers:
                co_authored[other] += 1

    facts: list[RelationFact] = []
    for peer in peers:
        addressed_you = speak.get((peer, agent_name), 0)
        you_addressed = speak.get((agent_name, peer), 0)
        co = co_authored.get(peer, 0)
        if addressed_you or you_addressed or co:
            facts.append(
                RelationFact(
                    peer=peer,
                    addressed_you=addressed_you,
                    you_addressed=you_addressed,
                    co_authored=co,
                )
            )

    facts.sort(key=lambda f: (-(f.addressed_you + f.you_addressed + f.co_authored), f.peer))
    return tuple(facts)

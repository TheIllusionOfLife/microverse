"""Slice 2 (R2.a): _compute_peers helper for run.py.

`run.py:215` previously constructed an empty `WorldContext()` so
`peers_today` was structurally always empty — the persona template
then rendered "You have not spoken with anyone today" every tick,
reinforcing the solitary-narrator frame that the silent-craftsperson
attractor lives inside. The helper unifies registered agents (always
present) with recently-active speak partners from episodic (so a
mid-run Stranger immigrant who has already addressed Aki shows up
even if she hasn't spoken back).
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.artisan import Artisan
from microverse.memory.episodic import EpisodicMemory
from microverse.run import _compute_peers
from microverse.world.scheduler import WeightedScheduler


def test_compute_peers_includes_other_registered_agents(tmp_path: Path) -> None:
    """When two agents are registered, the helper returns the other
    agent for self."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    bo = Artisan(name="Bo")
    sched.register(aki)
    sched.register(bo)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        peers = _compute_peers(sched, ep, aki)
    assert "Bo" in peers
    assert "Aki" not in peers


def test_compute_peers_excludes_self(tmp_path: Path) -> None:
    """Even if Aki has an episodic event with herself as actor and
    target, she must not appear in her own peers list."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    sched.register(aki)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="speak", target="Aki", payload={})
        peers = _compute_peers(sched, ep, aki)
    assert "Aki" not in peers


def test_compute_peers_returns_empty_when_alone(tmp_path: Path) -> None:
    """A solo registered agent with empty episodic gets an empty peers
    tuple (the existing solo-mode contract); the helper does not
    invent peers."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    sched.register(aki)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        peers = _compute_peers(sched, ep, aki)
    assert peers == ()


def test_compute_peers_includes_recent_speak_partner_from_episodic(tmp_path: Path) -> None:
    """A traveler (Stranger) speaks to Aki, then leaves. Even though
    they are no longer registered, the recent speak makes them an
    eligible peer for engagement-gate purposes."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    sched.register(aki)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="traveler", action="speak", target="Aki", payload={})
        peers = _compute_peers(sched, ep, aki)
    assert "traveler" in peers


def test_compute_peers_includes_addressee_when_self_spoke(tmp_path: Path) -> None:
    """If Aki spoke to a target, that target is a recent peer too —
    even if not registered. Symmetry with the actor-side check."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    sched.register(aki)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="speak", target="visitor-7", payload={})
        peers = _compute_peers(sched, ep, aki)
    assert "visitor-7" in peers


def test_compute_peers_skips_world_actor(tmp_path: Path) -> None:
    """Weather and other world events have actor='world' and must NOT
    appear as a peer to address — speaking to 'world' is a category
    error in the persona's mental model."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    sched.register(aki)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="world", action="weather.storm", target=None, payload={})
        peers = _compute_peers(sched, ep, aki)
    assert "world" not in peers


def test_compute_peers_deduplicates_across_sources(tmp_path: Path) -> None:
    """A registered peer who also appears in recent speak history
    shows up exactly once."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    bo = Artisan(name="Bo")
    sched.register(aki)
    sched.register(bo)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="speak", target="Bo", payload={})
        ep.append(actor="Bo", action="speak", target="Aki", payload={})
        peers = _compute_peers(sched, ep, aki)
    assert peers.count("Bo") == 1


def test_compute_peers_lookback_bounds_history(tmp_path: Path) -> None:
    """Old speak partners outside the lookback window are NOT
    included. Default lookback covers ~200 recent events."""
    sched = WeightedScheduler()
    aki = Artisan(name="Aki")
    sched.register(aki)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # An ancient speak partner.
        ep.append(actor="ancient-friend", action="speak", target="Aki", payload={})
        # Then 250 unrelated events (Aki crafts in a vacuum).
        for i in range(250):
            ep.append(
                actor="Aki",
                action="craft",
                target=None,
                payload={"artifact": f"thing-{i}"},
            )
        peers = _compute_peers(sched, ep, aki, lookback=200)
    assert "ancient-friend" not in peers, (
        f"events older than lookback must be excluded, got peers={peers!r}"
    )

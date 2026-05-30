"""Persona render + wiring for the ADR 0007 self-record (Stage B).

Confirms that all three thinking personas (Artisan, Scholar, Stranger)
render the structured ``self_view`` block — traits + relationship counts
+ peer names — and that ``run._build_self_view`` assembles it from the
episodic log + static role traits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from microverse.agents.artisan import Artisan
from microverse.agents.base import RelationFact, SelfView, WorldContext
from microverse.agents.scholar import Scholar
from microverse.agents.stranger import Stranger
from microverse.memory import est_tokens
from microverse.memory.episodic import EpisodicMemory
from microverse.run import _build_self_view

POPULATED = SelfView(
    traits=("you make things with patient hands",),
    relationships=(RelationFact(peer="Cy", addressed_you=4, you_addressed=2, co_authored=3),),
    beliefs="the loom rewards a slow, even hand",
)


@pytest.mark.parametrize("agent_cls", [Artisan, Scholar, Stranger])
def test_each_persona_renders_self_view(agent_cls: type) -> None:
    world = WorldContext(peers_today=("Cy",), self_view=POPULATED)
    prompt = agent_cls(name="Aki").render_prompt(world)
    assert "Who you are" in prompt
    assert "patient hands" in prompt
    assert "Cy" in prompt
    # counts rendered
    assert "4" in prompt
    assert "2" in prompt
    assert "3" in prompt
    # beliefs rendered
    assert "slow, even hand" in prompt


def test_empty_self_view_renders_no_block() -> None:
    world = WorldContext(peers_today=("Cy",))  # default empty SelfView
    prompt = Artisan(name="Aki").render_prompt(world)
    assert "Who you are" not in prompt


def test_self_view_block_stays_within_budget() -> None:
    """A populated self-view block is small (~hundreds of tokens), well
    inside the 4096-token prompt ceiling."""
    world = WorldContext(
        peers_today=("Cy", "Bo"),
        self_view=SelfView(
            traits=("you weigh ideas carefully",),
            relationships=tuple(
                RelationFact(peer=p, addressed_you=9, you_addressed=9, co_authored=9)
                for p in ("Cy", "Bo", "Eli", "Mara")
            ),
            beliefs="x" * 600,
        ),
    )
    prompt = Scholar(name="Aki").render_prompt(world)
    assert est_tokens(prompt) <= 4096


def test_build_self_view_assembles_from_log(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Cy", action="speak", target="Aki", payload={"thought": "hi"})
        ep.append(actor="Aki", action="speak", target="Cy", payload={"thought": "hi back"})
        sv = _build_self_view(ep, Artisan(name="Aki"), known_peers=("Aki", "Cy"))
    assert sv.traits == (
        "You make things with patient hands, and prefer to show rather than tell.",
    )
    assert sv.relationships == (
        RelationFact(peer="Cy", addressed_you=1, you_addressed=1, co_authored=0),
    )
    assert sv.beliefs == ""

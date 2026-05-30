"""Path-3 / Gate-5 guard for the ADR 0007 self-record carve-out.

The self-view is the EXPLICIT identity carve-out, but it must remain
*structured* identity only — it must never reintroduce the agent's own
fragment/artifact/thought prose (the channel Path-3 closed). This test
pins that boundary: even when an agent has crafted distinctive artifact
text, that text appears in neither the derived relationship ledger nor
the rendered persona's self-view block.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.artisan import Artisan
from microverse.agents.base import RelationFact, SelfView, WorldContext
from microverse.memory.episodic import EpisodicMemory
from microverse.world.relationships import derive_relationships

SECRET = "moonlit lathe of whispering cedar"
ROSTER = ("Aki", "Cy")


def test_relationship_ledger_carries_no_own_fragment_text(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="craft", target=None, payload={"artifact": SECRET})
        ep.append(actor="Aki", action="speak", target="Cy", payload={"thought": SECRET})
        ep.append(actor="Cy", action="speak", target="Aki", payload={"thought": SECRET})
        facts = derive_relationships(ep, agent_name="Aki", known_peers=ROSTER)
    rendered = repr(facts)
    assert SECRET not in rendered
    assert "cedar" not in rendered
    # The tie is still recorded structurally (counts only).
    assert facts == (RelationFact(peer="Cy", addressed_you=1, you_addressed=1, co_authored=0),)


def test_rendered_persona_self_view_has_no_own_fragment_text() -> None:
    world = WorldContext(
        peers_today=("Cy",),
        self_view=SelfView(
            traits=("you make things with patient hands",),
            relationships=(
                RelationFact(peer="Cy", addressed_you=3, you_addressed=2, co_authored=1),
            ),
            beliefs="",
        ),
    )
    prompt = Artisan(name="Aki").render_prompt(world)
    assert SECRET not in prompt
    # Structured identity DID render: peer name + counts are present.
    assert "Cy" in prompt
    assert "3" in prompt
    assert "2" in prompt

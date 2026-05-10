"""Slice 6 / Codex review HIGH: lore retrieval-time name redaction.

Even after Slice 6 removes the agent-name fallback in
``_derive_topic`` (so FTS5 is no longer seeded by the receiver's
name), Elder-compressed lore body can still contain actor names as
subjects ("Aki crafted a bowl", "Aki finished the cedar table").
A receiving agent reading that lore re-acquires their own past
through the community memory channel.

The fix is at retrieval-time, not at storage-time: ``build_context``
filters the assembled ``lore_excerpt`` so lines containing the
RECEIVER's name as a whole word are dropped. Other agents reading
the same lore still see it unredacted — community knowledge is
preserved at the village level; only the *receiver's own* name is
redacted in their own view.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.base import WorldContext
from microverse.memory import build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory


def _seed_lore(se: SemanticMemory, *, doc_id: str, text: str) -> None:
    se.index(doc_id=doc_id, text=text, payload={"text": text})


def test_lore_with_receiver_name_is_redacted(tmp_path: Path) -> None:
    """A lore document mentioning Aki by name must NOT surface in
    Aki's own ``lore_excerpt``.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_lore(se, doc_id="aki-bowl", text="Aki crafted a small wooden bowl")
        _seed_lore(se, doc_id="storm", text="The storm shook the cedar grove")
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="storm",
            receiver_name="Aki",
        )
    joined = "\n".join(out.lore_excerpt)
    assert "Aki" not in joined, (
        f"receiver name must be redacted from lore_excerpt, got:\n{joined}"
    )
    # Other lore (the storm one, no Aki reference) should still
    # surface — redaction is targeted, not blanket.
    assert "storm" in joined, f"unrelated lore must still surface, got:\n{joined}"


def test_lore_visible_to_other_agents(tmp_path: Path) -> None:
    """The same lore document mentioning Aki IS visible to Bo —
    redaction is per-receiver, not a global censor. Community
    knowledge stays community-level.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_lore(se, doc_id="aki-bowl", text="Aki crafted a small wooden bowl")
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="bowl",
            receiver_name="Bo",
        )
    joined = "\n".join(out.lore_excerpt)
    assert "Aki" in joined, f"non-receiver agent must see the lore, got:\n{joined}"


def test_lore_redaction_is_whole_word(tmp_path: Path) -> None:
    """Substring matches like "Akihiko" or "akin" must NOT trip the
    redaction filter for receiver "Aki" — same whole-word semantic
    as the peer_inbox name filter.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_lore(
            se, doc_id="akihiko", text="Akihiko built a kiln nearby in the cedar grove"
        )
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="cedar",
            receiver_name="Aki",
        )
    joined = "\n".join(out.lore_excerpt)
    assert "Akihiko" in joined, (
        f"substring match must not trip whole-word redaction, got:\n{joined}"
    )


def test_lore_no_receiver_name_no_redaction(tmp_path: Path) -> None:
    """When ``receiver_name`` is omitted (e.g. the build_context call
    is for a non-agent context like a dashboard), no redaction
    fires and lore renders verbatim.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_lore(se, doc_id="aki-bowl", text="Aki crafted a small wooden bowl")
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="bowl",
        )
    joined = "\n".join(out.lore_excerpt)
    assert "Aki" in joined, f"no-receiver call must not redact, got:\n{joined}"

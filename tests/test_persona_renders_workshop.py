"""Phase 5 — persona templates render workshop_view.

ADR 0003: every persona (Artisan / Scholar / Stranger) renders a
"village workshop currently holds" block that iterates
``world.workshop_view``. Each WIPView's name, phase, contributors,
and excerpt are surfaced as readable prompt text. Empty
``workshop_view`` produces no block (back-compat with v0.1.1
fixtures).

The persona "Hard rules" enumeration of valid actions also gains
``contribute`` so the LLM knows the verb is available.
"""

from __future__ import annotations

import re

import pytest

from microverse.agents.base import WorldContext
from microverse.prompts import render
from microverse.world.workshop import WIPView

_PERSONAS = ("persona_artisan.j2", "persona_scholar.j2", "persona_stranger.j2")


@pytest.mark.parametrize("template", _PERSONAS)
def test_persona_omits_workshop_block_when_empty(template: str) -> None:
    """A WorldContext with empty workshop_view renders no workshop
    block. v0.1.1 fixtures keep working without modification.
    """
    out = render(template, name="Aki", world=WorldContext())
    assert "village workshop" not in out.lower()
    assert "[earlier contributor" not in out
    assert "phase:" not in out.lower()


@pytest.mark.parametrize("template", _PERSONAS)
def test_persona_renders_workshop_when_present(template: str) -> None:
    """When workshop_view has WIPViews, the persona prompt surfaces
    each one with name, phase, contributors, and excerpt.
    """
    views = (
        WIPView(
            name="workshop.loom",
            phase="developing",
            contributors="Bo",
            excerpt="Bo: blue stitching",
        ),
    )
    out = render(template, name="Aki", world=WorldContext(workshop_view=views))
    assert "village workshop" in out.lower()
    assert "workshop.loom" in out
    assert "developing" in out
    assert "Bo" in out
    assert "blue stitching" in out


@pytest.mark.parametrize("template", _PERSONAS)
def test_persona_renders_multi_wip_in_order(template: str) -> None:
    """Multiple WIPViews iterate in the order they were assembled."""
    views = (
        WIPView(name="workshop.scroll", phase="forming", contributors="", excerpt=""),
        WIPView(
            name="workshop.loom",
            phase="developing",
            contributors="Bo, Cy",
            excerpt="Bo: warp\nCy: weft",
        ),
        WIPView(
            name="workshop.garden_bed",
            phase="complete",
            contributors="Bo",
            excerpt="Bo: planted seedlings",
        ),
    )
    out = render(template, name="Aki", world=WorldContext(workshop_view=views))
    # Order check via index comparison.
    i_scroll = out.find("workshop.scroll")
    i_loom = out.find("workshop.loom")
    i_garden = out.find("workshop.garden_bed")
    assert -1 < i_scroll < i_loom < i_garden
    assert "complete" in out
    assert "planted seedlings" in out


@pytest.mark.parametrize("template", _PERSONAS)
def test_persona_workshop_block_hides_excerpt_when_blank(template: str) -> None:
    """A WIPView with phase=forming and no excerpt should still
    render (the agent should know the WIP exists) but should not
    produce stray "Excerpt:" header followed by empty content.
    """
    views = (WIPView(name="workshop.scroll", phase="forming", contributors="", excerpt=""),)
    out = render(template, name="Aki", world=WorldContext(workshop_view=views))
    out_lower = out.lower()
    assert "workshop.scroll" in out
    assert "forming" in out
    # Case-insensitive: don't render a "Recent fragments:" / "Excerpt:"
    # header with empty content. Either case (or different surrounding
    # newline count) would still be a regression.
    for marker in ("recent fragments:", "excerpt:"):
        if marker not in out_lower:
            continue
        idx = out_lower.find(marker)
        tail = out_lower[idx + len(marker) :].lstrip("\n").lstrip()
        msg = (
            f"empty-excerpt header {marker!r} rendered with no content under it: "
            f"{out[idx : idx + 80]!r}"
        )
        assert tail, msg
        assert not tail.startswith("phase"), msg


# Whole-word regex used by the contribute-listing tests: ensures the
# template includes ``contribute`` as a distinct token rather than a
# substring of ``contribute_to`` or some unrelated word.
_CONTRIBUTE_WORD = re.compile(r"\bcontribute\b")


def test_artisan_lists_contribute_in_hard_rules() -> None:
    """The Artisan's "Hard rules" / action enumeration includes
    ``contribute`` so the LLM knows it is a valid verb.
    """
    out = render("persona_artisan.j2", name="Aki", world=WorldContext())
    assert _CONTRIBUTE_WORD.search(out), f"`contribute` not listed as a verb: {out!r}"


def test_scholar_lists_contribute_in_hard_rules() -> None:
    """Scholars also have access to the workshop affordance — they
    can leave field notes / observations as fragments. The persona
    surfaces it the same way.
    """
    out = render("persona_scholar.j2", name="Cy", world=WorldContext())
    assert _CONTRIBUTE_WORD.search(out), f"`contribute` not listed as a verb: {out!r}"


def test_stranger_lists_contribute_in_hard_rules() -> None:
    out = render("persona_stranger.j2", name="X", world=WorldContext())
    assert _CONTRIBUTE_WORD.search(out), f"`contribute` not listed as a verb: {out!r}"

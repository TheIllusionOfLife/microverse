"""Slice 6: ``compression.j2`` no longer renders agent thoughts.

The Layer-G plan addressed the thought → recent_episodic edge but
left the Elder compression path untouched. ``compression.j2:10``
read ``payload.get("thought", "")`` for every event in the recent
window, weaving the agent's narrative voice into the next round of
community lore — which then re-entered every agent's prompt via
``lore_excerpt``. Path-3 closes that channel: Elder compresses on
the factual surface (actor + action + target) only.
"""

from __future__ import annotations

from microverse.prompts import render


def test_compression_template_does_not_render_thought() -> None:
    """An event whose payload contains a distinctive thought string
    must NOT have that thought surface in the rendered compression
    prompt body. Elder sees only factual surface.
    """
    distinctive_thought = "every fiber demands silence"

    class _StubEvent:
        actor = "Aki"
        action = "craft"
        target = None
        payload = {"thought": distinctive_thought, "artifact": "a small bowl"}  # noqa: RUF012

    out = render(
        "compression.j2",
        prior_lore="The village values quiet making.",
        events=[_StubEvent()],
        continuity_hint="",
    )
    assert distinctive_thought not in out, (
        f"agent thought must not feed into Elder compression, got:\n{out}"
    )


def test_compression_template_renders_factual_surface() -> None:
    """The Elder's working set retains actor + action + (optional)
    target. Without the thought there must still be enough surface
    for the LLM to weave a continuation.
    """

    class _StubEvent:
        actor = "Aki"
        action = "craft"
        target = None
        payload = {"thought": "irrelevant introspection", "artifact": "x"}  # noqa: RUF012

    out = render(
        "compression.j2",
        prior_lore="lore",
        events=[_StubEvent()],
        continuity_hint="",
    )
    assert "Aki" in out, "actor must surface"
    assert "craft" in out, "action must surface"


def test_compression_template_handles_missing_payload() -> None:
    """Defensive: an event with no payload (or no thought key) must
    not crash the template. Elder still emits a clean line.
    """

    class _StubEvent:
        actor = "world"
        action = "weather.storm"
        target = None
        payload: dict = {}  # noqa: RUF012

    out = render(
        "compression.j2",
        prior_lore="lore",
        events=[_StubEvent()],
        continuity_hint="",
    )
    assert "world" in out
    assert "weather.storm" in out

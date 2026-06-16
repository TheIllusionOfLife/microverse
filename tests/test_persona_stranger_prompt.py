"""Persona-prompt content guards for Stranger.

The Watchdog's echo-chamber rehab path can register a Stranger mid-run,
so the same rest-bias trap that hit Artisan in the 17.9h soak could
reappear via Stranger if the persona keeps an explicit rest default.
Pin the prompt content so the trap can't sneak back in through this
agent.
"""

from __future__ import annotations

from microverse.agents.base import WorldContext
from microverse.prompts import render


def test_stranger_prompt_does_not_default_to_rest():
    rendered = render("persona_stranger.j2", world=WorldContext(), name="Mira")
    # The literal escape hatch must be gone.
    assert "If unsure, choose" not in rendered
    assert 'If unsure, "rest"' not in rendered
    # And no stray rest suggestion in the Hard rules section
    # (case-insensitive, catches quoted and unquoted forms).
    assert "Hard rules:" in rendered
    hard_rules = rendered.split("Hard rules:", 1)[1]
    assert "rest" not in hard_rules.lower()


def test_stranger_travel_variant_pins_travel_identity():
    """ADR 0017: the stronger-travel variant frames the Stranger's identity
    around movement so ``travel`` is the natural verb — calibrated to the
    Scholar's identity-led study lean, NOT a forced hard rule. The travel
    framing is the independent variable, so it must be present here and absent
    from the default persona.
    """
    rendered = render("persona_stranger_travel.j2", world=WorldContext(), name="Vesna")
    low = rendered.lower()
    assert "wayfarer" in low
    assert "the road" in low
    # Same no-rest-bias guard as the default stranger persona: travel is led by
    # identity, never by a rest escape hatch in the Hard rules.
    assert "Hard rules:" in rendered
    hard_rules = rendered.split("Hard rules:", 1)[1]
    assert "rest" not in hard_rules.lower()
    # The default persona must NOT carry the travel-identity framing, so the two
    # templates differ only by the IV under test.
    default = render("persona_stranger.j2", world=WorldContext(), name="Vesna")
    assert "wayfarer" not in default.lower()

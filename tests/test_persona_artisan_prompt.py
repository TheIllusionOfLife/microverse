"""Persona-prompt content guards for Artisan.

The 17.9h soak showed Aki collapsing into a permanent rest-loop within
the first hour. The prompt template explicitly told the model "If
unsure, choose 'rest'", which combined with newest-first episodic
context produced a self-reinforcing fixed point. These tests pin the
prompt content so the trap can't silently come back.
"""

from __future__ import annotations

from microverse.agents.base import WorldContext
from microverse.prompts import render


def test_artisan_prompt_does_not_default_to_rest():
    rendered = render("persona_artisan.j2", world=WorldContext(), name="Aki")
    # The literal escape hatch must be gone.
    assert "If unsure, choose" not in rendered
    # And no stray rest suggestion in the Hard rules section. We assert
    # the section anchor exists first so a template restructure can't
    # silently turn this guard into a no-op, and we use a partition so
    # a missing anchor would raise rather than yield the whole prompt.
    # Lower-cased substring catches both quoted and unquoted forms.
    assert "Hard rules:" in rendered
    hard_rules = rendered.split("Hard rules:", 1)[1]
    assert "rest" not in hard_rules.lower()


def test_artisan_prompt_requires_artifact_for_craft():
    """Layer F.1: post-Layer-E 24h soak (data/soak-24h-4, seed 38)
    showed Aki picking craft with artifact=null for hours at a time
    (1581 null vs 678 non-null over the run). The persona must contain
    a Hard rule pinning the contract so the LLM stops treating null
    as artistically intentional.
    """
    rendered = render("persona_artisan.j2", world=WorldContext(), name="Aki")
    assert "Hard rules:" in rendered
    hard_rules = rendered.split("Hard rules:", 1)[1]
    rule_present = any(
        "craft" in line.lower() and "artifact" in line.lower() for line in hard_rules.splitlines()
    )
    assert rule_present, f"missing craft+artifact hard rule in Hard rules section: {hard_rules!r}"

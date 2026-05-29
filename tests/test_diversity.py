"""Verb-diversity lever — Phase D / ADR 0002 counter-pressure.

``recent_verb_distribution`` walks the agent's most recent N actions
in the episodic log; ``suggest_underused_verb`` picks an available verb
whose recent share is below average so the persona's novelty_hint and
the post-action substitution lever can both pull the agent out of a
single-verb attractor.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from microverse.memory.episodic import EpisodicMemory
from microverse.world.diversity import (
    recent_verb_distribution,
    suggest_underused_verb,
)


def test_recent_verb_distribution_counts_recent_actions(tmp_path: Path) -> None:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    try:
        for _ in range(8):
            em.append(actor="aki", action="craft", target=None, payload={})
        for _ in range(2):
            em.append(actor="aki", action="speak", target="bo", payload={})
        em.append(actor="bo", action="study", target=None, payload={})

        dist = recent_verb_distribution(em, "aki", lookback=200)
    finally:
        em.close()
    # Aki's 10 most recent actions: 8 craft + 2 speak. Bo's are filtered.
    assert dist == Counter({"craft": 8, "speak": 2})


def test_recent_verb_distribution_lookback_cap(tmp_path: Path) -> None:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    try:
        for _ in range(50):
            em.append(actor="aki", action="craft", target=None, payload={})
        for _ in range(3):
            em.append(actor="aki", action="rest", target=None, payload={})

        dist = recent_verb_distribution(em, "aki", lookback=5)
    finally:
        em.close()
    # 5-action lookback ends on the 3 most-recent rests + 2 crafts before.
    assert sum(dist.values()) == 5
    assert dist["rest"] == 3
    assert dist["craft"] == 2


def test_suggest_underused_verb_returns_least_used_available() -> None:
    dist = Counter({"craft": 8, "speak": 1, "study": 0, "rest": 0})
    available = ["craft", "speak", "study", "rest"]
    chosen = suggest_underused_verb(dist, available)
    # Among the zero-count verbs (study, rest), either is acceptable;
    # the helper must pick *one of the underused* not the dominant.
    assert chosen in {"study", "rest"}
    assert chosen != "craft"


def test_suggest_underused_verb_returns_none_on_empty_distribution() -> None:
    """If the agent has no recent history, no hint is needed."""
    assert suggest_underused_verb(Counter(), ["craft", "speak"]) is None


def test_suggest_underused_verb_returns_none_when_balanced() -> None:
    """When every verb's share is at-or-below the average + slack, no
    hint fires (avoids hint spam during healthy diversity)."""
    dist = Counter({"craft": 3, "speak": 3, "study": 3, "rest": 3})
    assert suggest_underused_verb(dist, ["craft", "speak", "study", "rest"]) is None


def test_suggest_underused_verb_filters_to_available() -> None:
    """If the LEAST used verb is not in the available set, return the
    least-used FROM the available set."""
    dist = Counter({"craft": 8, "travel": 0, "speak": 2})
    available = ["craft", "speak"]  # travel deliberately excluded
    assert suggest_underused_verb(dist, available) == "speak"


def test_compute_novelty_hint_ignores_scene_contributes_end_to_end(tmp_path: Path) -> None:
    """End-to-end regression for the dead diversity lever.

    In the 5.5-day soak the recent window was ~94% scene ``contribute``,
    so ``_compute_novelty_hint`` returned dominant=contribute — which a
    single-tick action (never ``contribute``) can never match, so the
    lever never fired. With contribute excluded, the dominant verb is
    the agent's actual free-choice attractor (``craft`` here) and the
    suggested substitute is a different free verb, so the precondition
    ``action.verb == dominant_verb`` becomes satisfiable.
    """
    import random

    from microverse.agents.artisan import Artisan
    from microverse.ops.metrics import Metrics
    from microverse.run import _compute_novelty_hint

    metrics = Metrics(tmp_path / "metrics.sqlite")
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    try:
        # Scene-heavy log: 60 contributes then a craft burst of 12 with a
        # couple of speaks — exactly the soak's shape.
        for _ in range(60):
            em.append(actor="Aki", action="contribute", target=None, payload={})
        for _ in range(12):
            em.append(actor="Aki", action="craft", target=None, payload={})
        for _ in range(2):
            em.append(actor="Aki", action="speak", target="Cy", payload={})

        agent = Artisan("Aki", metrics=metrics, rng=random.Random(0))
        hint, dominant, suggested = _compute_novelty_hint(em, agent)
    finally:
        em.close()
        metrics.close()

    assert dominant == "craft", f"expected free-verb dominant, got {dominant!r}"
    assert suggested not in {"", "contribute"}, f"bad suggestion {suggested!r}"
    assert dominant in hint


def test_recent_verb_distribution_excludes_scene_contributes(tmp_path: Path) -> None:
    """The diversity lever targets FREE-choice verbs. Scene ``contribute``
    events flood a scene-heavy log and must be excluded, otherwise the
    dominant verb resolves to ``contribute`` (which the lever can never
    substitute) and the lever is structurally dead. Regression test for
    the 5.5-day soak where ``diversity_lever_substituted`` stayed 0
    because dominant=contribute never matched a single-tick action.
    """
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    try:
        # A scene-heavy agent: 30 scene contributes interleaved with a
        # craft burst of 6 and 2 speaks.
        for _ in range(30):
            em.append(actor="aki", action="contribute", target=None, payload={})
        for _ in range(6):
            em.append(actor="aki", action="craft", target=None, payload={})
        for _ in range(2):
            em.append(actor="aki", action="speak", target="bo", payload={})

        dist = recent_verb_distribution(em, "aki", lookback=200, exclude={"contribute"})
    finally:
        em.close()
    # contribute is excluded entirely; the free-choice mix survives so
    # the dominant verb (craft) now matches the single-tick action.
    assert "contribute" not in dist
    assert dist == Counter({"craft": 6, "speak": 2})

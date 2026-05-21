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

"""Slice 4 (R2.c): default roster has two residents (Artisan + Scholar).

The project's stated mission is a "small fictional society" producing
harvested artifacts (README.md, PROMPT.md). For the entire history
of the run only Aki the Artisan was ever registered; ``peers_today``
was structurally always empty (until R2.a) and the Watchdog's
echo-chamber rescue almost never spawned a Stranger. The default
roster now includes a structurally-different Scholar so the engagement
gate (R2.b) actually has someone to engage. ``solo`` keeps the
single-agent regime available for regression soaks.
"""

from __future__ import annotations

from microverse.agents.artisan import Artisan
from microverse.agents.scholar import Scholar
from microverse.ops.metrics import Metrics
from microverse.run import _build_roster


def test_default_roster_has_two_residents() -> None:
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    assert len(roster) == 2, f"default roster must be 2 residents, got {[a.name for a in roster]!r}"
    roles = sorted(a.role for a in roster)
    assert roles == ["artisan", "scholar"], f"roles must be {{artisan, scholar}}, got {roles!r}"


def test_solo_flag_keeps_single_resident() -> None:
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics, solo=True)
    assert len(roster) == 1
    assert roster[0].role == "artisan"


def test_default_roster_aki_and_scholar_have_distinct_names() -> None:
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    names = [a.name for a in roster]
    assert len(set(names)) == 2, f"residents must have distinct names, got {names!r}"


def test_default_roster_artisan_is_first() -> None:
    """Order matters for the Trader-isn't-scheduled invariant: Aki is
    the legacy primary resident; Scholar augments. Putting Aki first
    keeps prior behavior recognizable."""
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    assert isinstance(roster[0], Artisan)
    assert isinstance(roster[1], Scholar)

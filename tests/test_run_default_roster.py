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

import pytest

from microverse.agents.artisan import Artisan
from microverse.agents.scholar import Scholar
from microverse.agents.stranger import Stranger
from microverse.ops.metrics import Metrics
from microverse.run import _build_roster


def test_default_roster_has_two_residents() -> None:
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    assert len(roster) == 2, f"default roster must be 2 residents, got {[a.name for a in roster]!r}"
    roles = sorted(a.role for a in roster)
    assert roles == ["artisan", "scholar"], f"roles must be {{artisan, scholar}}, got {roles!r}"


def test_default_roster_weights_unchanged() -> None:
    """The env hook must not perturb the default: Aki(100) + Cy(70), byte-identical."""
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    by_name = {a.name: a for a in roster}
    assert by_name["Aki"].soul_tokens == 100
    assert by_name["Cy"].soul_tokens == 70


def test_empty_roster_spec_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty MICROVERSE_ROSTER is treated as unset, not an error."""
    monkeypatch.setenv("MICROVERSE_ROSTER", "")
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    assert [a.role for a in roster] == ["artisan", "scholar"]


def test_roster_spec_builds_three_residents() -> None:
    metrics = Metrics(":memory:")
    roster = _build_roster(
        metrics, spec="artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70"
    )
    assert [a.name for a in roster] == ["Aki", "Cy", "Vesna"]
    assert [a.role for a in roster] == ["artisan", "scholar", "stranger"]
    assert [a.soul_tokens for a in roster] == [100, 70, 70]
    assert isinstance(roster[0], Artisan)
    assert isinstance(roster[1], Scholar)
    assert isinstance(roster[2], Stranger)


def test_roster_spec_role_swap_pairing() -> None:
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics, spec="artisan:Aki:100,stranger:Vesna:70")
    assert [a.role for a in roster] == ["artisan", "stranger"]
    assert [a.soul_tokens for a in roster] == [100, 70]


def test_roster_spec_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MICROVERSE_ROSTER", "artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70")
    metrics = Metrics(":memory:")
    roster = _build_roster(metrics)
    assert [a.role for a in roster] == ["artisan", "scholar", "stranger"]


def test_roster_spec_unknown_role_fails_fast() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="unknown role"):
        _build_roster(metrics, spec="elder:Sage:50")


def test_roster_spec_malformed_entry_fails_fast() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="role:name:tokens"):
        _build_roster(metrics, spec="artisan:Aki")


def test_roster_spec_noninteger_tokens_fails_fast() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="integer"):
        _build_roster(metrics, spec="artisan:Aki:lots")


def test_roster_spec_nonpositive_tokens_fails_fast() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="positive"):
        _build_roster(metrics, spec="artisan:Aki:0")


def test_roster_spec_duplicate_names_fails_fast() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="duplicate"):
        _build_roster(metrics, spec="artisan:Aki:100,scholar:Aki:70")


def test_roster_spec_empty_after_parse_fails_fast() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="zero residents"):
        _build_roster(metrics, spec=",  ,")


def test_solo_and_roster_spec_mutually_exclusive() -> None:
    metrics = Metrics(":memory:")
    with pytest.raises(ValueError, match="mutually exclusive"):
        _build_roster(metrics, solo=True, spec="artisan:Aki:100,scholar:Cy:70")


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

"""Gauge math for ``wip_target_concentration``.

ADR 0005 Decision 1 (v0.3.1) Empirical-risk guard: when the persona
view hides ``complete`` WIPs, the model may collapse all contributes
onto the lowest-fragment open WIP. To detect that failure mode, the
run loop maintains a per-flush-window ``Counter[str]`` of
``action.contribute_to`` targets and publishes
``peak / total`` (x100, integer) as a gauge each flush.

Acceptance threshold from ADR 0005 §"v0.3.1" smoke is < 70.

These unit tests pin the math itself; the run-loop integration is
exercised by ``test_run_smoke`` and the 200-tick acceptance smoke
called out in the ADR.
"""

from __future__ import annotations

from collections import Counter


def _concentration_percent(counts: Counter[str]) -> int:
    """Mirror the formula in run.py's HARVEST_FLUSH_EVERY branch."""
    total = sum(counts.values())
    if total == 0:
        return 0
    return int(max(counts.values()) * 100 / total)


def test_concentration_single_wip_is_one_hundred() -> None:
    counts: Counter[str] = Counter()
    counts["workshop.loom"] += 10
    assert _concentration_percent(counts) == 100


def test_concentration_even_split_is_below_threshold() -> None:
    # 4 WIPs each at 5 contributes → peak=5, total=20, share=25%.
    counts: Counter[str] = Counter({"a": 5, "b": 5, "c": 5, "d": 5})
    assert _concentration_percent(counts) == 25
    assert _concentration_percent(counts) < 70  # ADR 0005 gate


def test_concentration_rerouting_signature_is_above_threshold() -> None:
    # The failure mode ADR 0005 Empirical risks calls out:
    # 90% of contributes targeting one WIP → 90 (above the 70 floor).
    counts: Counter[str] = Counter({"hot": 9, "cold": 1})
    assert _concentration_percent(counts) == 90
    assert _concentration_percent(counts) >= 70


def test_concentration_empty_window_is_zero() -> None:
    counts: Counter[str] = Counter()
    assert _concentration_percent(counts) == 0

"""Transition-triggered flush — ADR 0005 Decision 2.

When a WIP transitions to phase=complete, the run-loop must flush the
harvester opportunistically (before the next 50-tick timer boundary),
subject to a ≥5-tick throttle. Counters distinguish timer-triggered
from transition-triggered flushes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from microverse.run import run


def _contribute_chat(wip_name: str):
    """Stub LLM: every tick, agent emits a contribute action targeting
    ``wip_name`` with a long, varied fragment. The contributor changes
    each tick so the workshop's contributor subfloor is satisfied; the
    WIP fills to ``phase=complete`` quickly."""
    call_count = [0]

    def _chat(**_kwargs: object) -> dict[str, object]:
        call_count[0] += 1
        fragment = (
            f"Long fragment number {call_count[0]:03d} with varied lexical "
            f"content describing a careful observation of weather over the "
            f"long quiet afternoons of the season; the {call_count[0]} day "
            f"brings a new variation. " * 2
        )
        content = (
            '{"thought": "I add to the loom.", "action": "contribute", '
            f'"contribute_to": "{wip_name}", '
            f'"artifact": "{fragment}"}}'
        )
        return {"content": content, "thinking": "", "raw": {}}

    return _chat, call_count


def _trader_chat():
    def _chat(**_kwargs: object) -> dict[str, object]:
        return {
            "content": '[{"artifact_id": 0, "score": 0.5, "rationale": "x"}]',
            "thinking": "",
            "raw": {},
        }

    return _chat


def test_transition_flush_counter_bumps_when_wip_completes(tmp_path: Path) -> None:
    """A WIP completing mid-window should trigger an opportunistic
    flush, recorded under ``harvest_flush_transition_triggered``."""
    from microverse.world.workshop import CONFIGURED_WIPS

    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    wip_name = CONFIGURED_WIPS[0]

    contrib_chat, _ = _contribute_chat(wip_name)

    # Run for 60 ticks: at ~1 contribute per tick across 3 agents,
    # the WIP fills past COMPLETE_FRAGMENT_FLOOR (8) inside 50 ticks,
    # triggering at least one transition flush before the timer fires.
    with (
        patch("microverse.agents.artisan.chat", side_effect=contrib_chat),
        patch("microverse.agents.scholar.chat", side_effect=contrib_chat),
        patch("microverse.agents.trader.chat", side_effect=_trader_chat()),
    ):
        run(
            ticks=60,
            seed=42,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
            solo=False,
        )

    # Inspect metrics.sqlite for the new counter bumps.
    conn = sqlite3.connect(str(data_dir / "metrics.sqlite"))
    try:
        rows = conn.execute(
            "SELECT name, MAX(value) FROM metrics "
            "WHERE name IN ('harvest_flush_transition_triggered',"
            " 'harvest_flush_timer_triggered') "
            "GROUP BY name"
        ).fetchall()
    finally:
        conn.close()

    metric_max = dict(rows)
    assert metric_max.get("harvest_flush_timer_triggered", 0) >= 1, (
        f"timer flush expected; got {metric_max}"
    )
    # The transition-triggered flush must fire at least once across
    # 60 ticks of contribute-heavy workload.
    assert metric_max.get("harvest_flush_transition_triggered", 0) >= 1, (
        f"transition flush expected; got {metric_max}"
    )


def test_throttle_prevents_back_to_back_transition_flushes(tmp_path: Path) -> None:
    """If two WIPs complete within the ≥5-tick throttle window, only
    the first transition flush fires (the second waits for either the
    timer or the throttle to expire). We verify by counting bumps."""
    from microverse.world.workshop import CONFIGURED_WIPS

    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"

    # Round-robin two WIPs so both fill in parallel quickly.
    wip_a = CONFIGURED_WIPS[0]
    wip_b = CONFIGURED_WIPS[1] if len(CONFIGURED_WIPS) > 1 else CONFIGURED_WIPS[0]

    def _chat(**_kwargs: object) -> dict[str, object]:
        tgt = wip_a if (_chat.calls % 2 == 0) else wip_b  # type: ignore[attr-defined]
        _chat.calls += 1  # type: ignore[attr-defined]
        fragment = (
            "Long varied fragment with distinct lexical content for the "
            "loom and the scroll across long quiet afternoons of work. " * 2
        )
        content = (
            '{"thought": "I add.", "action": "contribute", '
            f'"contribute_to": "{tgt}", '
            f'"artifact": "{fragment}"}}'
        )
        return {"content": content, "thinking": "", "raw": {}}

    _chat.calls = 0  # type: ignore[attr-defined]

    with (
        patch("microverse.agents.artisan.chat", side_effect=_chat),
        patch("microverse.agents.scholar.chat", side_effect=_chat),
        patch("microverse.agents.trader.chat", side_effect=_trader_chat()),
    ):
        run(
            ticks=40,
            seed=42,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
            solo=False,
        )

    # Throttle assertion is statistical (depends on tick interleaving),
    # but: total flush count across 40 ticks must be <= ticks/throttle + timer_floor
    # = 40/5 + 40/50 (rounded up) = 8 + 1 = 9. (Generous upper bound;
    # the real cap is much tighter once each transition consumes the WIP.)
    conn = sqlite3.connect(str(data_dir / "metrics.sqlite"))
    try:
        rows = conn.execute(
            "SELECT MAX(value) FROM metrics WHERE name='harvest_flush_transition_triggered'"
        ).fetchone()
    finally:
        conn.close()
    transition_count = rows[0] or 0
    # Throttle: at minimum 5-tick spacing → 40-tick run, can have at
    # most 8 transition flushes total (40/5).
    assert transition_count <= 8, f"throttle violated: {transition_count} transition flushes"

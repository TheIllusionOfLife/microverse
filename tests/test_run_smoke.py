"""Smoke test for microverse.run.

End-to-end with mocked Ollama: 30 ticks at tempo=0 produce at least
one harvested artifact and the corresponding manifest.jsonl line.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from microverse.run import run


def _canned_chat(call_count: list[int]):
    """Every third call returns a craft action with an artifact."""

    def _chat(**_kwargs: object) -> dict[str, object]:
        call_count[0] += 1
        if call_count[0] % 3 == 0:
            content = (
                '{"thought": "I will craft a wooden bowl for the village.", '
                '"action": "craft", "target": null, '
                '"artifact": "A simple wooden bowl carved with three swirling lines."}'
            )
        else:
            content = (
                '{"thought": "I rest a moment.", "action": "rest", '
                '"target": null, "artifact": null}'
            )
        return {"content": content, "thinking": "", "raw": {}}

    return _chat


def _trader_chat_fixed_scores():
    """Trader stub: returns varied scores so the percentile cutoff has
    something to discriminate. Scores produce a clear top-30%."""

    def _chat(**_kwargs: object) -> dict[str, object]:
        # Return a JSON list with descending scores by artifact_id —
        # the harvester will pull whatever count it needs and rank.
        return {
            "content": (
                "["
                '{"artifact_id": 0, "score": 0.1, "rationale": "x"},'
                '{"artifact_id": 1, "score": 0.2, "rationale": "x"},'
                '{"artifact_id": 2, "score": 0.3, "rationale": "x"},'
                '{"artifact_id": 3, "score": 0.4, "rationale": "x"},'
                '{"artifact_id": 4, "score": 0.5, "rationale": "x"},'
                '{"artifact_id": 5, "score": 0.6, "rationale": "x"},'
                '{"artifact_id": 6, "score": 0.7, "rationale": "x"},'
                '{"artifact_id": 7, "score": 0.8, "rationale": "x"},'
                '{"artifact_id": 8, "score": 0.9, "rationale": "x"},'
                '{"artifact_id": 9, "score": 1.0, "rationale": "x"}'
                "]"
            ),
            "thinking": "",
            "raw": {},
        }

    return _chat


def test_30_ticks_produces_at_least_one_artifact(tmp_path: Path):
    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    call_count = [0]

    with (
        patch("microverse.agents.artisan.chat", side_effect=_canned_chat(call_count)),
        patch("microverse.agents.trader.chat", side_effect=_trader_chat_fixed_scores()),
    ):
        executed = run(
            ticks=30,
            seed=42,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
        )

    assert executed == 30
    assert call_count[0] == 30  # one Artisan chat per tick

    inbox_files = list((harvest_dir / "inbox").rglob("*.md"))
    # 10 artifacts (every 3rd tick); Trader scores 0.1..1.0; p70 cutoff
    # of 10 distinct scores accepts the top 3.
    assert len(inbox_files) == 3, f"expected 3 accepted, got {len(inbox_files)}"

    manifest = harvest_dir / "manifest.jsonl"
    assert manifest.exists()
    lines = manifest.read_text().splitlines()
    # 10 manifest lines: one per buffered candidate (3 accepted, 7 rejected).
    assert len(lines) == 10
    accepted_count = sum(1 for line in lines if json.loads(line)["accepted"])
    assert accepted_count == 3


def test_run_creates_data_and_harvest_dirs(tmp_path: Path):
    """run() must create both directories even if no artifact is harvested."""
    data_dir = tmp_path / "fresh-data"
    harvest_dir = tmp_path / "fresh-harvest"
    call_count = [0]

    rest_only = {
        "content": '{"thought": "x", "action": "rest", "target": null, "artifact": null}',
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.artisan.chat", return_value=rest_only):
        run(ticks=3, seed=0, tempo=0, data_dir=data_dir, harvest_dir=harvest_dir)
        # Touch call_count so unused-import lints don't mind us.
        call_count[0] += 0

    assert data_dir.exists()
    assert (data_dir / "episodic.sqlite").exists()
    assert harvest_dir.exists()


def test_run_survives_chat_exception(tmp_path: Path):
    """A raised exception from chat() must not crash the tick loop:
    bump llm_timeout + consecutive_fail and continue."""
    import sqlite3

    raise_count = [0]

    def boom(**_kwargs):
        raise_count[0] += 1
        if raise_count[0] <= 5:
            raise TimeoutError("simulated Ollama timeout")
        return {
            "content": '{"thought": "x", "action": "rest", "target": null, "artifact": null}',
            "thinking": "",
            "raw": {},
        }

    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"

    # Stub time.sleep so the throttle in the except branch doesn't drag
    # the test out for 5 seconds per failure.
    with (
        patch("microverse.run.time.sleep", side_effect=lambda *_a, **_k: None),
        patch("microverse.agents.artisan.chat", side_effect=boom),
    ):
        executed = run(
            ticks=2,  # 2 successful ticks needed
            seed=0,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
        )
    # We absorbed 5 exceptions then completed 2 ticks.
    assert executed == 2

    # llm_timeout was bumped 5 times, then bumps_since_flush flushed
    # at close. Confirm the metric persisted.
    with sqlite3.connect(str(data_dir / "metrics.sqlite")) as conn:
        rows = conn.execute(
            "SELECT name, MAX(value) FROM metrics WHERE name='llm_timeout' GROUP BY name"
        ).fetchall()
    assert rows == [("llm_timeout", 5)]


def test_deadlock_break_only_fires_when_all_agents_paused():
    """The deadlock-break must not reset counters as long as at least
    one agent is still un-paused — otherwise legitimate per-agent
    failures get masked.
    """
    from microverse.ops.metrics import Metrics
    from microverse.run import _all_agents_paused

    m = Metrics(":memory:")
    # Two stub agents named like real ones.
    agents = [type("A", (), {"name": "aki"})(), type("A", (), {"name": "bo"})()]

    # Neither paused — must be False.
    assert _all_agents_paused(m, agents) is False

    # Only one paused — still False (the un-paused agent can still tick).
    for _ in range(3):
        m.bump("consecutive_fail", agent="aki")
    assert m.should_pause("aki") is True
    assert m.should_pause("bo") is False
    assert _all_agents_paused(m, agents) is False

    # Both paused — True.
    for _ in range(3):
        m.bump("consecutive_fail", agent="bo")
    assert _all_agents_paused(m, agents) is True


def test_run_recovers_from_all_paused_via_consecutive_fail_reset(tmp_path: Path):
    """Three consecutive parse failures pause the only agent. The tick
    loop must auto-rehab via reset(consecutive_fail) so the run can
    still complete the requested ticks once the model recovers."""
    seq = ["bad"] * 6 + ['{"thought": "x", "action": "rest", "target": null, "artifact": null}'] * 5

    def respond(**_kwargs):
        if seq:
            content = seq.pop(0)
        else:
            content = '{"thought": "x", "action": "rest", "target": null, "artifact": null}'
        return {"content": content, "thinking": "", "raw": {}}

    with (
        patch("microverse.run.time.sleep", side_effect=lambda *_a, **_k: None),
        patch("microverse.agents.artisan.chat", side_effect=respond),
    ):
        executed = run(
            ticks=3,
            seed=0,
            tempo=0,
            data_dir=tmp_path / "data",
            harvest_dir=tmp_path / "harvest",
        )
    assert executed == 3

"""Tests for microverse.ops.metrics.Metrics.

In-process counter store with optional SQLite persistence. Counters
keyed by (name, agent | None). Used by the tick loop, harvester, and
watchdog to track JSON parse outcomes, timeouts, and per-agent
consecutive-fail counts.
"""

from __future__ import annotations

from pathlib import Path

from microverse.ops.metrics import Metrics


def test_bump_and_get_global_counter():
    m = Metrics(":memory:")
    m.bump("json_ok")
    m.bump("json_ok")
    assert m.get("json_ok") == 2


def test_unknown_counter_reads_as_zero():
    m = Metrics(":memory:")
    assert m.get("never_bumped") == 0


def test_per_agent_counter_isolated_from_global():
    m = Metrics(":memory:")
    m.bump("consecutive_fail", agent="aki")
    m.bump("consecutive_fail", agent="aki")
    m.bump("consecutive_fail", agent="bo")
    assert m.get("consecutive_fail", agent="aki") == 2
    assert m.get("consecutive_fail", agent="bo") == 1
    # Global slot for the same name is independent.
    assert m.get("consecutive_fail") == 0


def test_reset_clears_specific_counter():
    m = Metrics(":memory:")
    m.bump("consecutive_fail", agent="aki")
    m.bump("consecutive_fail", agent="aki")
    m.reset("consecutive_fail", agent="aki")
    assert m.get("consecutive_fail", agent="aki") == 0


def test_flush_persists_snapshot_rows(tmp_path: Path):
    db = tmp_path / "metrics.sqlite"
    m = Metrics(db)
    m.bump("json_ok")
    m.bump("json_ok")
    m.bump("consecutive_fail", agent="aki")
    m.flush()

    rows = m._conn.execute(
        "SELECT name, agent, value FROM metrics ORDER BY name, agent"
    ).fetchall()
    by_key = {(r[0], r[1]): r[2] for r in rows}
    assert by_key[("consecutive_fail", "aki")] == 1
    assert by_key[("json_ok", None)] == 2
    m.close()


def test_flush_appends_time_series(tmp_path: Path):
    """Each flush writes a fresh snapshot — value over time."""
    db = tmp_path / "metrics.sqlite"
    m = Metrics(db)
    m.bump("json_ok")
    m.flush()
    m.bump("json_ok")
    m.flush()
    count = m._conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE name='json_ok'"
    ).fetchone()[0]
    assert count == 2
    m.close()


def test_should_pause_after_max_consecutive_fail():
    m = Metrics(":memory:")
    for _ in range(3):
        m.bump("consecutive_fail", agent="aki")
    assert m.should_pause("aki") is True
    assert m.should_pause("bo") is False


def test_reset_consecutive_fail_unpauses_agent():
    m = Metrics(":memory:")
    for _ in range(3):
        m.bump("consecutive_fail", agent="aki")
    assert m.should_pause("aki") is True
    m.reset("consecutive_fail", agent="aki")
    assert m.should_pause("aki") is False


def test_persist_durable_across_reopen(tmp_path: Path):
    db = tmp_path / "persist.sqlite"
    m = Metrics(db)
    m.bump("llm_timeout")
    m.flush()
    m.close()

    reopened = Metrics(db)
    rows = reopened._conn.execute(
        "SELECT name, value FROM metrics WHERE name='llm_timeout'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == 1
    reopened.close()


def test_auto_flush_every(tmp_path: Path):
    db = tmp_path / "auto.sqlite"
    m = Metrics(db, auto_flush_every=3)
    m.bump("json_ok")
    m.bump("json_ok")
    rows_after_two = m._conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE name='json_ok'"
    ).fetchone()[0]
    assert rows_after_two == 0
    m.bump("json_ok")  # third bump triggers auto-flush
    rows_after_three = m._conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE name='json_ok'"
    ).fetchone()[0]
    assert rows_after_three == 1
    m.close()

"""Tests for microverse.memory.episodic.EpisodicMemory.

Covers schema, WAL pragmas, append/last contract, file-backed crash
recovery (open → write → kill -9 the process equivalent → re-open and
read back).
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from microverse.memory.episodic import EpisodicMemory

_REQUIRES_SIGKILL = pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"), reason="SIGKILL not available on this platform"
)


def test_in_memory_open_creates_events_table():
    mem = EpisodicMemory(":memory:")
    cols = {row[1] for row in mem._conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"id", "ts", "actor", "action", "target", "payload_json"} <= cols
    mem.close()


def test_wal_journal_mode_set_on_file_backed_db(tmp_path: Path):
    db = tmp_path / "events.sqlite"
    mem = EpisodicMemory(db)
    mode = mem._conn.execute("PRAGMA journal_mode").fetchone()[0]
    sync = mem._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert mode.lower() == "wal"
    assert int(sync) == 1  # SQLite encodes synchronous=NORMAL as the integer 1
    mem.close()


def test_append_returns_increasing_ids():
    mem = EpisodicMemory(":memory:")
    a = mem.append(actor="aki", action="craft", target=None, payload={"item": "lamp"})
    b = mem.append(actor="aki", action="rest", target=None, payload={})
    assert isinstance(a, int)
    assert isinstance(b, int)
    assert b > a
    mem.close()


def test_append_persists_payload_as_json_roundtrip():
    mem = EpisodicMemory(":memory:")
    payload = {"item": "lamp", "quality": 0.7, "tags": ["light", "wood"]}
    mem.append(actor="aki", action="craft", target="village", payload=payload)
    rows = mem.last(1)
    assert len(rows) == 1
    ev = rows[0]
    assert ev.actor == "aki"
    assert ev.action == "craft"
    assert ev.target == "village"
    assert ev.payload == payload
    mem.close()


def test_last_returns_most_recent_first():
    mem = EpisodicMemory(":memory:")
    for i in range(5):
        mem.append(actor=f"a{i}", action="speak", target=None, payload={"n": i})
    rows = mem.last(3)
    assert [r.payload["n"] for r in rows] == [4, 3, 2]
    mem.close()


def test_last_n_capped_at_total_count():
    mem = EpisodicMemory(":memory:")
    mem.append(actor="x", action="rest", target=None, payload={})
    rows = mem.last(100)
    assert len(rows) == 1
    mem.close()


def test_explicit_ts_is_recorded():
    mem = EpisodicMemory(":memory:")
    fixed = 1_700_000_000.5
    mem.append(actor="aki", action="rest", target=None, payload={}, ts=fixed)
    rows = mem.last(1)
    assert rows[0].ts == pytest.approx(fixed)
    mem.close()


def test_count(tmp_path: Path):
    mem = EpisodicMemory(tmp_path / "c.sqlite")
    assert mem.count() == 0
    for _ in range(7):
        mem.append(actor="a", action="rest", target=None, payload={})
    assert mem.count() == 7
    mem.close()


def test_context_manager(tmp_path: Path):
    db = tmp_path / "ctx.sqlite"
    with EpisodicMemory(db) as mem:
        mem.append(actor="a", action="rest", target=None, payload={})
        assert mem.count() == 1


def test_file_backed_durability_across_reopen(tmp_path: Path):
    """Write events, close hard, reopen, verify all events persist —
    this is the cheap durability check."""
    db = tmp_path / "durable.sqlite"
    mem = EpisodicMemory(db)
    for i in range(10):
        mem.append(actor=f"a{i}", action="craft", target=None, payload={"n": i})
    assert mem.count() == 10
    mem.close()
    del mem  # release any leftover handles

    reopened = EpisodicMemory(db)
    assert reopened.count() == 10
    rows = reopened.last(3)
    assert [r.payload["n"] for r in rows] == [9, 8, 7]
    reopened.close()


def test_since_returns_only_events_at_or_after_floor(tmp_path: Path):
    mem = EpisodicMemory(tmp_path / "since.sqlite")
    base = 1_700_000_000.0
    for i in range(10):
        mem.append(actor="aki", action="craft", target=None, payload={"n": i}, ts=base + i * 100)
    # Floor at base + 500 keeps events 5..9 (ts 500, 600, ..., 900).
    rows = mem.since(base + 500)
    assert [r.payload["n"] for r in rows] == [9, 8, 7, 6, 5]
    mem.close()


def test_since_with_limit_caps_results(tmp_path: Path):
    mem = EpisodicMemory(tmp_path / "since-limit.sqlite")
    for i in range(20):
        mem.append(actor="aki", action="craft", target=None, payload={"n": i}, ts=1.0)
    rows = mem.since(0.0, limit=3)
    assert len(rows) == 3
    mem.close()


def test_since_unlimited_can_exceed_last_n(tmp_path: Path):
    """`since` must not silently drop events past position N like
    `last(N)` would — that's the whole reason it exists."""
    mem = EpisodicMemory(tmp_path / "since-many.sqlite")
    for i in range(500):
        mem.append(actor="aki", action="craft", target=None, payload={"n": i}, ts=1.0)
    rows = mem.since(0.0)
    assert len(rows) == 500
    mem.close()


@_REQUIRES_SIGKILL
def test_kill9_recovery_via_subprocess(tmp_path: Path):
    """Spawn a child that opens the DB, writes 5 committed events, then
    SIGKILLs itself mid-loop. Re-open in this process and confirm all
    5 commits are intact (WAL guarantees no half-written rows)."""
    db = tmp_path / "kill.sqlite"
    script = f"""
import os, sys, time
sys.path.insert(0, {str(Path.cwd() / "src")!r})
from microverse.memory.episodic import EpisodicMemory
mem = EpisodicMemory({str(db)!r})
for i in range(5):
    mem.append(actor='a', action='craft', target=None, payload={{'n': i}})
sys.stdout.flush()
print('committed_5', flush=True)
# Simulate a hard crash: SIGKILL ourselves before clean close.
os.kill(os.getpid(), 9)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    # Process should have died from SIGKILL (-9) after printing committed_5.
    assert "committed_5" in proc.stdout
    assert proc.returncode != 0  # killed, not clean exit

    # Give SQLite a moment to settle (WAL → DB checkpoint on next open).
    time.sleep(0.1)
    reopened = EpisodicMemory(db)
    assert reopened.count() == 5
    rows = reopened.last(5)
    assert sorted(r.payload["n"] for r in rows) == [0, 1, 2, 3, 4]
    reopened.close()


def test_involving_returns_actor_or_target_newest_first(tmp_path: Path) -> None:
    """``involving`` is the per-actor query feeding the belief summarizer:
    events where the name is actor OR target, newest-first, bounded."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="craft", target=None, payload={"n": 0})
        ep.append(actor="Cy", action="speak", target="Aki", payload={"n": 1})
        ep.append(actor="Cy", action="craft", target=None, payload={"n": 2})  # not Aki
        ep.append(actor="Aki", action="speak", target="Cy", payload={"n": 3})
        rows = ep.involving("Aki")
    ns = [r.payload["n"] for r in rows]
    assert ns == [3, 1, 0]  # newest-first, excludes the Cy-only craft


def test_involving_respects_limit(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        for i in range(10):
            ep.append(actor="Aki", action="craft", target=None, payload={"n": i})
        rows = ep.involving("Aki", limit=3)
    assert [r.payload["n"] for r in rows] == [9, 8, 7]

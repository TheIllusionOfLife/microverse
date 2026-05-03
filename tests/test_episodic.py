"""Tests for microverse.memory.episodic.EpisodicMemory.

Covers schema, WAL pragmas, append/last contract, file-backed crash
recovery (open → write → kill -9 the process equivalent → re-open and
read back).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from microverse.memory.episodic import EpisodicMemory


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
    # synchronous=NORMAL == 1
    assert int(sync) == 1
    mem.close()


def test_append_returns_increasing_ids():
    mem = EpisodicMemory(":memory:")
    a = mem.append(actor="aki", action="craft", target=None, payload={"item": "lamp"})
    b = mem.append(actor="aki", action="rest", target=None, payload={})
    assert isinstance(a, int) and isinstance(b, int)
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

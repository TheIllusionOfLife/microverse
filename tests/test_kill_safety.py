"""End-to-end kill-safety: SIGKILL the run subprocess after 5 commits,
restart, confirm the event log is intact (5 rows, monotonic ids).

This complements ``tests/test_episodic.py``'s lower-level kill drill —
that one tests the SQLite layer directly. This one exercises the full
tick loop (Artisan → episodic.append → harvester) under SIGKILL.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

# SIGKILL is POSIX-only; the WAL/durability contract still holds on
# Windows but the drill itself can't be performed there.
pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"), reason="SIGKILL not available on this platform"
)


def test_run_subprocess_survives_kill9_with_no_loss(tmp_path: Path):
    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    src_path = str(Path.cwd() / "src")

    # Subprocess script: patch Artisan's chat with a stub that commits 5
    # rest actions, then on the 6th call sends SIGKILL to itself before
    # the wrapper can return — so the 6th tick is in flight (think()
    # never completes) and never commits. Episodic should hold exactly 5.
    script = f"""
import os, signal, sys
sys.path.insert(0, {src_path!r})
from unittest.mock import patch
from microverse.run import run

call_count = {{"n": 0}}
def fake_chat(**_kwargs):
    call_count["n"] += 1
    if call_count["n"] == 6:
        # All 5 commits are durable on disk by this point. Hard-kill self.
        sys.stdout.write("KILLING_NOW\\n")
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGKILL)
    return {{
        "content": '{{"thought": "x", "action": "rest", "target": null, "artifact": null}}',
        "thinking": "",
        "raw": {{}},
    }}

with patch("microverse.agents.artisan.chat", side_effect=fake_chat):
    run(ticks=100, tempo=0, data_dir={str(data_dir)!r}, harvest_dir={str(harvest_dir)!r}, solo=True)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "KILLING_NOW" in proc.stdout
    assert proc.returncode != 0  # killed, not clean exit

    # WAL settle window. SQLite checkpoints on next open.
    time.sleep(0.1)

    db = data_dir / "episodic.sqlite"
    assert db.exists()
    with sqlite3.connect(str(db)) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM events ORDER BY id ASC")]
    assert len(ids) == 5, f"expected exactly 5 committed events, got {len(ids)}: {ids}"
    # Monotonic, no duplicates.
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_run_subprocess_recovers_and_continues_appending(tmp_path: Path):
    """After a kill drill, a fresh run() against the same data_dir must
    keep appending — no schema corruption, no broken WAL."""
    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    src_path = str(Path.cwd() / "src")

    # First run: commit 3 events then kill.
    pre_kill = f"""
import os, signal, sys
sys.path.insert(0, {src_path!r})
from unittest.mock import patch
from microverse.run import run

call_count = {{"n": 0}}
def fake_chat(**_kwargs):
    call_count["n"] += 1
    if call_count["n"] == 4:
        os.kill(os.getpid(), signal.SIGKILL)
    return {{
        "content": '{{"thought": "x", "action": "rest", "target": null, "artifact": null}}',
        "thinking": "",
        "raw": {{}},
    }}

with patch("microverse.agents.artisan.chat", side_effect=fake_chat):
    run(ticks=100, tempo=0, data_dir={str(data_dir)!r}, harvest_dir={str(harvest_dir)!r}, solo=True)
"""
    proc = subprocess.run(
        [sys.executable, "-c", pre_kill],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0

    time.sleep(0.1)
    db = data_dir / "episodic.sqlite"
    with sqlite3.connect(str(db)) as conn:
        first_round = [r[0] for r in conn.execute("SELECT id FROM events ORDER BY id")]
    assert first_round == [1, 2, 3]

    # Second run: append 2 more cleanly. Use the same fake_chat by inlining.
    post_kill = f"""
import sys
sys.path.insert(0, {src_path!r})
from unittest.mock import patch
from microverse.run import run

def fake_chat(**_kwargs):
    return {{
        "content": '{{"thought": "x", "action": "rest", "target": null, "artifact": null}}',
        "thinking": "",
        "raw": {{}},
    }}

with patch("microverse.agents.artisan.chat", side_effect=fake_chat):
    run(ticks=2, tempo=0, data_dir={str(data_dir)!r}, harvest_dir={str(harvest_dir)!r}, solo=True)
"""
    proc2 = subprocess.run(
        [sys.executable, "-c", post_kill],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc2.returncode == 0, proc2.stderr

    with sqlite3.connect(str(db)) as conn:
        second_round = [r[0] for r in conn.execute("SELECT id FROM events ORDER BY id")]
    # Strictly increasing across the kill boundary; no resets, no duplicates.
    assert second_round == [1, 2, 3, 4, 5]

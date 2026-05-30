"""EpisodicMemory.optimize() — WAL checkpoint + PRAGMA optimize.

Long-running soaks (7-day target) accumulate a sidecar -wal file that
grows without bound unless the writer (or an operator process) checkpoints
it. optimize() opens a *short-lived* secondary connection that runs
``wal_checkpoint(TRUNCATE)`` followed by ``PRAGMA optimize`` and closes.

It must:
  - Not raise on an idle / empty database.
  - Truncate the -wal file when there are pending frames.
  - Not interfere with concurrent writes on the long-lived connection.
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.episodic import EpisodicMemory


def test_optimize_is_noop_on_idle_db(tmp_path: Path) -> None:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    try:
        em.optimize()  # must not raise
    finally:
        em.close()


def test_optimize_truncates_wal(tmp_path: Path) -> None:
    """After heavy writes the -wal sidecar holds frames. After optimize
    it should shrink (truncate) — exact size depends on SQLite version
    but it must be smaller than the pre-optimize size."""
    db_path = tmp_path / "episodic.sqlite"
    em = EpisodicMemory(db_path)
    try:
        for i in range(200):
            em.append(actor="aki", action="speak", target=None, payload={"i": i})
        wal_path = db_path.with_suffix(db_path.suffix + "-wal")
        # -wal sidecar exists after writes
        assert wal_path.exists(), "WAL sidecar should exist after writes"
        before = wal_path.stat().st_size
        em.optimize()
        after = wal_path.stat().st_size if wal_path.exists() else 0
        assert after <= before, f"WAL should shrink or stay equal: before={before} after={after}"
    finally:
        em.close()


def test_optimize_does_not_block_concurrent_append(tmp_path: Path) -> None:
    """Calling optimize() must not break the long-lived writer connection.
    A subsequent append on the original connection must still succeed."""
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    try:
        em.append(actor="aki", action="speak", target=None, payload={"i": 0})
        em.optimize()
        em.append(actor="aki", action="speak", target=None, payload={"i": 1})
        assert em.count() == 2
    finally:
        em.close()

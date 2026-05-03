"""Project-wide SQLite opener.

Lives at ``microverse.memory._sqlite`` (not ``microverse.memory.__init__``)
so callers outside the memory subpackage — notably ``microverse.ops.metrics``
— can use it without pulling in ``WorldContext`` and the rest of the
memory-layer assembly machinery, which would create an ops → memory →
agents import chain.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_sqlite_wal(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the project's standard durability
    pragmas (WAL + ``synchronous=NORMAL``).

    Strict by default: file-backed paths that fail to enter WAL raise.
    The ``:memory:`` carve-out exists because in-memory dbs always report
    ``"memory"`` from ``PRAGMA journal_mode`` — no durability surface to
    defend, so don't fail. ``check_same_thread=False`` so the watchdog
    can read while the tick loop writes; callers serialize logically.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    actual_mode = str(mode_row[0]).lower() if mode_row else ""
    if str(path) != ":memory:" and actual_mode != "wal":
        conn.close()
        raise RuntimeError(f"failed to enable WAL on db {path!r}; got mode={actual_mode!r}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

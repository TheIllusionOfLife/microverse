"""In-process counters with optional SQLite persistence.

Used by the tick loop, harvester, and watchdog to track:
  - ``json_ok`` — clean JSON parse from an agent action
  - ``json_repaired`` — needed jsonrepair to parse
  - ``json_fallback_rest`` — agent fell back to a `rest` action
  - ``llm_timeout`` — Ollama call exceeded the per-call timeout
  - ``consecutive_fail`` (per agent) — used by the watchdog stub to
    pause an agent after `MAX_CONSECUTIVE_FAIL` consecutive failures

Counters are kept in memory by ``(name, agent)`` and snapshotted to
SQLite on ``flush()``. Each flush writes a row per (name, agent) tuple
so the table is a time-series, not a current-value cache. ``auto_flush_every``
makes the tick loop's bookkeeping a no-op for the caller.

Concurrency: ``check_same_thread=False`` lets the watchdog (Phase 4a)
read counters while the tick loop bumps them; ``_lock`` serializes the
in-memory dict mutations so reads observe consistent values. ``close()``
flushes pending bumps before closing the SQLite connection.

Schema:

    metrics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        name TEXT NOT NULL,
        agent TEXT,
        value INTEGER NOT NULL
    )
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

from microverse.config import MAX_CONSECUTIVE_FAIL

_CounterKey = tuple[str, str | None]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    name TEXT NOT NULL,
    agent TEXT,
    value INTEGER NOT NULL
)
"""


class Metrics:
    """In-process counters with SQLite snapshots."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        auto_flush_every: int | None = None,
    ) -> None:
        self._counters: defaultdict[_CounterKey, int] = defaultdict(int)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        mode_row = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()
        actual_mode = str(mode_row[0]).lower() if mode_row else ""
        # `:memory:` always reports "memory" — no durability surface, so
        # accept it. File-backed dbs: WAL rejection is a hard error.
        if str(path) != ":memory:" and actual_mode != "wal":
            raise RuntimeError(f"failed to enable WAL on metrics db {path!r}; got mode={mode_row}")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._auto_flush_every = auto_flush_every
        self._bumps_since_flush = 0
        self._lock = threading.Lock()

    def bump(self, name: str, *, agent: str | None = None, by: int = 1) -> int:
        with self._lock:
            key: _CounterKey = (name, agent)
            self._counters[key] += by
            self._bumps_since_flush += 1
            should_flush = (
                self._auto_flush_every is not None
                and self._bumps_since_flush >= self._auto_flush_every
            )
            value = self._counters[key]
        if should_flush:
            self.flush()
        return value

    def get(self, name: str, *, agent: str | None = None) -> int:
        with self._lock:
            return self._counters.get((name, agent), 0)

    def reset(self, name: str, *, agent: str | None = None) -> None:
        with self._lock:
            self._counters[(name, agent)] = 0

    def set_value(self, name: str, value: int, *, agent: str | None = None) -> None:
        """Snapshot-style metric: overwrite the in-memory value rather
        than incrementing. Used for gauges (e.g., current diversity %)
        where the latest reading is what the dashboard wants, not a
        running sum."""
        with self._lock:
            self._counters[(name, agent)] = value
            self._bumps_since_flush += 1
            should_flush = (
                self._auto_flush_every is not None
                and self._bumps_since_flush >= self._auto_flush_every
            )
        if should_flush:
            self.flush()

    def should_pause(self, agent: str) -> bool:
        """Watchdog stub: pause an agent after MAX_CONSECUTIVE_FAIL fails."""
        return self.get("consecutive_fail", agent=agent) >= MAX_CONSECUTIVE_FAIL

    def flush(self) -> None:
        """Snapshot every counter to SQLite as a new row."""
        ts = time.time()
        with self._lock:
            rows = [(ts, name, agent, value) for (name, agent), value in self._counters.items()]
            self._bumps_since_flush = 0
        if rows:
            self._conn.executemany(
                "INSERT INTO metrics (ts, name, agent, value) VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def close(self) -> None:
        # Flush only if there are pending bumps. Calling flush() blindly
        # would write a redundant snapshot of values that haven't changed
        # since the last explicit flush, polluting the time-series.
        # Read under the lock to avoid a TOCTOU race with bump().
        with self._lock:
            need_flush = self._bumps_since_flush > 0
        try:
            if need_flush:
                self.flush()
        finally:
            self._conn.close()

    def __enter__(self) -> Metrics:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def _report(db_path: str) -> int:
    """Print the latest snapshot of every (name, agent) counter.

    Reads ``db_path`` directly (no Metrics open — avoids re-creating
    the schema on a missing DB). Returns 0 on success, 1 if the DB
    doesn't exist.
    """
    import sys
    from pathlib import Path

    p = Path(db_path)
    if not p.exists():
        print(f"metrics db not found: {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(p))
    try:
        rows = conn.execute(
            """
            SELECT name, agent, value, ts
            FROM metrics m
            WHERE id = (
                SELECT MAX(id) FROM metrics
                WHERE name = m.name AND IFNULL(agent, '') = IFNULL(m.agent, '')
            )
            ORDER BY name, agent
            """
        ).fetchall()
    finally:
        conn.close()

    print(f"{'name':<32} {'agent':<20} {'value':>10}  ts")
    for name, agent, value, ts in rows:
        print(f"{name:<32} {(agent or ''):<20} {value:>10}  {ts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="microverse.ops.metrics")
    parser.add_argument("--report", action="store_true", help="Print the latest snapshot.")
    parser.add_argument("--db", required=True, help="Path to metrics.sqlite.")
    args = parser.parse_args(argv)
    if args.report:
        return _report(args.db)
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

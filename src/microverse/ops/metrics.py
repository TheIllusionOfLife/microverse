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
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._auto_flush_every = auto_flush_every
        self._bumps_since_flush = 0

    def bump(self, name: str, *, agent: str | None = None, by: int = 1) -> int:
        key: _CounterKey = (name, agent)
        self._counters[key] += by
        self._bumps_since_flush += 1
        if self._auto_flush_every and self._bumps_since_flush >= self._auto_flush_every:
            self.flush()
        return self._counters[key]

    def get(self, name: str, *, agent: str | None = None) -> int:
        return self._counters.get((name, agent), 0)

    def reset(self, name: str, *, agent: str | None = None) -> None:
        self._counters[(name, agent)] = 0

    def should_pause(self, agent: str) -> bool:
        """Watchdog stub: pause an agent after MAX_CONSECUTIVE_FAIL fails."""
        return self.get("consecutive_fail", agent=agent) >= MAX_CONSECUTIVE_FAIL

    def flush(self) -> None:
        """Snapshot every counter to SQLite as a new row."""
        ts = time.time()
        rows = [(ts, name, agent, value) for (name, agent), value in self._counters.items()]
        if rows:
            self._conn.executemany(
                "INSERT INTO metrics (ts, name, agent, value) VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        self._bumps_since_flush = 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Metrics:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

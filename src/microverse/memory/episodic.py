"""SQLite-backed event log — the durable record of everything that
happens inside the microverse.

Durability contract:
  - WAL journal mode + ``synchronous=NORMAL`` are set at every connection
    open. The pragma return value is verified — if SQLite refuses to
    enter WAL mode, ``__init__`` raises rather than silently degrading.
    Mid-tick ``kill -9`` cannot lose *committed* events (the WAL holds
    the row, and the next open replays it). The most a process crash
    can lose is the in-flight, uncommitted tick.
  - ``synchronous=NORMAL`` is the standard WAL pairing: it skips fsync
    on each commit and fsyncs only at checkpoint. That means a sudden
    *power* loss (not just process crash) can lose the last few commits.
    For our soak rungs that risk is acceptable — flip to ``FULL`` if you
    care about kernel-panic durability.
  - Cold-backup snapshots come later (Phase 2). Phase 1 trusts WAL.

Schema:

    events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT,
        payload_json TEXT
    )

The wrapper is intentionally small: callers should never reach for the
underlying connection except through the explicit append / last / count
operations. This keeps the durability story simple (single transaction
per append) and the contract testable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from microverse.memory import open_sqlite_wal


@dataclass(frozen=True, slots=True)
class Event:
    """One row from the events table, with payload deserialized."""

    id: int
    ts: float
    actor: str
    action: str
    target: str | None
    payload: dict[str, Any]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    payload_json TEXT
)
"""


class EpisodicMemory:
    """File-backed event log for the microverse.

    Use as a context manager:

        with EpisodicMemory("data/episodic.sqlite") as mem:
            mem.append(actor="aki", action="craft", target=None,
                       payload={"item": "lamp"})
    """

    def __init__(self, path: str | Path) -> None:
        self._path = path
        self._conn = open_sqlite_wal(path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def append(
        self,
        *,
        actor: str,
        action: str,
        target: str | None,
        payload: dict[str, Any],
        ts: float | None = None,
    ) -> int:
        if ts is None:
            ts = time.time()
        cur = self._conn.execute(
            "INSERT INTO events (ts, actor, action, target, payload_json) VALUES (?, ?, ?, ?, ?)",
            (ts, actor, action, target, json.dumps(payload, separators=(",", ":"))),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def last(self, n: int = 100) -> list[Event]:
        rows = self._conn.execute(
            "SELECT id, ts, actor, action, target, payload_json "
            "FROM events ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [
            Event(
                id=r[0],
                ts=r[1],
                actor=r[2],
                action=r[3],
                target=r[4],
                payload=json.loads(r[5]) if r[5] else {},
            )
            for r in rows
        ]

    def since(self, ts_floor: float, *, limit: int | None = None) -> list[Event]:
        """Return events with ``ts >= ts_floor`` ordered newest-first.

        Phase 3a's ``build_context`` uses this so the 7-day window
        actually covers all events in that window — ``last(N)`` would
        silently drop events past position N when the system runs hot.
        ``limit`` caps result size for very long windows; default None
        means no cap.
        """
        if limit is None:
            rows = self._conn.execute(
                "SELECT id, ts, actor, action, target, payload_json "
                "FROM events WHERE ts >= ? ORDER BY id DESC",
                (ts_floor,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, ts, actor, action, target, payload_json "
                "FROM events WHERE ts >= ? ORDER BY id DESC LIMIT ?",
                (ts_floor, limit),
            ).fetchall()
        return [
            Event(
                id=r[0],
                ts=r[1],
                actor=r[2],
                action=r[3],
                target=r[4],
                payload=json.loads(r[5]) if r[5] else {},
            )
            for r in rows
        ]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EpisodicMemory:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

"""SQLite-backed event log — the durable record of everything that
happens inside the microverse.

Durability contract:
  - WAL journal mode + ``synchronous=NORMAL`` are set at every connection
    open. Mid-tick ``kill -9`` cannot lose committed events; the most a
    crash can lose is an in-flight (uncommitted) tick.
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
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        # check_same_thread=False because the watchdog will read while
        # the tick loop writes. We still serialize logically — every
        # write is one autocommit transaction.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        # WAL is the durability contract. NORMAL fsync is the standard
        # WAL pairing — fsync at checkpoint, not at every commit.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
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

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EpisodicMemory:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

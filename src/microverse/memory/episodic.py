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

from microverse.memory._sqlite import open_sqlite_wal


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

    def involving(self, name: str, *, limit: int = 100) -> list[Event]:
        """Return the most recent ``limit`` events where ``name`` is the
        actor OR the target, newest-first.

        A per-actor query (rather than filtering a global ``last(N)``
        window in Python) so a rarely-scheduled agent's own history is
        never starved by a burst of other agents' events. Feeds the
        belief summarizer (ADR 0007 Phase 1, Stage C).
        """
        rows = self._conn.execute(
            "SELECT id, ts, actor, action, target, payload_json "
            "FROM events WHERE actor = ? OR target = ? ORDER BY id DESC LIMIT ?",
            (name, name, limit),
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

    def speak_edge_counts(self) -> dict[tuple[str, str], int]:
        """Full-history ``(actor, target) -> count`` for every targeted
        ``speak`` event. The relationship ledger (ADR 0007 Phase 1)
        aggregates over the whole log so identity is genuinely durable —
        not a recency window. Untargeted speaks (``target IS NULL``) are
        excluded.
        """
        rows = self._conn.execute(
            "SELECT actor, target, COUNT(*) FROM events "
            "WHERE action = 'speak' AND target IS NOT NULL "
            "GROUP BY actor, target"
        ).fetchall()
        return {(str(r[0]), str(r[1])): int(r[2]) for r in rows}

    def scene_contributor_sets(self) -> dict[str, set[str]]:
        """Map ``scene_id -> {actors who committed a contribute}``.

        Co-authorship is derived ONLY from committed ``contribute``
        events that carry a ``scene_id`` payload — never from
        ``scene.open`` (which logs *scheduled* authors before the turns
        run and can abort, which would fabricate ties). Single-tick
        contributes have no ``scene_id`` and are ignored.
        """
        rows = self._conn.execute(
            "SELECT json_extract(payload_json, '$.scene_id') AS sid, actor "
            "FROM events "
            "WHERE action = 'contribute' AND sid IS NOT NULL "
            "GROUP BY sid, actor"
        ).fetchall()
        out: dict[str, set[str]] = {}
        for sid, actor in rows:
            out.setdefault(str(sid), set()).add(str(actor))
        return out

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def optimize(self) -> None:
        """WAL checkpoint (TRUNCATE) + PRAGMA optimize via a short-lived
        secondary connection so the long-lived writer is not disturbed.

        Called periodically during long soaks to reclaim the -wal sidecar
        and refresh SQLite's query planner statistics. Not a VACUUM —
        that would acquire an exclusive lock and block the writer.
        Failures (BUSY etc.) are swallowed: this is best-effort hygiene,
        not a durability boundary.
        """
        import sqlite3

        try:
            side = sqlite3.connect(str(self._path), timeout=5.0)
        except sqlite3.Error:
            return
        try:
            try:
                side.execute("PRAGMA busy_timeout = 5000")
                side.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                side.execute("PRAGMA optimize")
            except sqlite3.Error:
                # Hygiene op; do not propagate.
                pass
        finally:
            side.close()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EpisodicMemory:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

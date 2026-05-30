"""IdentityStore — persistent home for an agent's summarized beliefs.

ADR 0007 Phase 1 (Stage C), Pillar 1. Unlike the relationship ledger
(which is derived on-read from the episodic log), beliefs are produced by
a periodic LLM summarization pass and are too expensive to recompute each
tick. They are persisted here, one row per agent.

This is a *materialized cache* over the WAL log, NOT an authoritative
durability boundary: the episodic log remains the source of truth and the
beliefs can be regenerated from it. Persisting them only means beliefs
survive a clean restart instead of resetting to empty. The store reuses
``open_sqlite_wal`` so it shares the project's WAL + synchronous=NORMAL
contract.
"""

from __future__ import annotations

import time
from pathlib import Path

from microverse.memory._sqlite import open_sqlite_wal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity (
    agent_name TEXT PRIMARY KEY,
    beliefs_text TEXT NOT NULL,
    updated_ts REAL NOT NULL
)
"""


class IdentityStore:
    """One-row-per-agent persistent belief store.

    Use as a context manager::

        with IdentityStore("data/identity.sqlite") as store:
            store.put("Aki", "the loom rewards a slow, even hand")
            store.get("Aki")
    """

    def __init__(self, path: str | Path) -> None:
        self._conn = open_sqlite_wal(path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, agent_name: str) -> str:
        """Return the stored beliefs for ``agent_name``, or ``""`` if the
        agent has no record yet."""
        row = self._conn.execute(
            "SELECT beliefs_text FROM identity WHERE agent_name = ?",
            (agent_name,),
        ).fetchone()
        return str(row[0]) if row else ""

    def put(self, agent_name: str, beliefs_text: str, *, ts: float | None = None) -> None:
        """Upsert the beliefs for ``agent_name``."""
        if ts is None:
            ts = time.time()
        self._conn.execute(
            "INSERT INTO identity (agent_name, beliefs_text, updated_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(agent_name) DO UPDATE SET "
            "beliefs_text = excluded.beliefs_text, updated_ts = excluded.updated_ts",
            (agent_name, beliefs_text, ts),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> IdentityStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

"""Lexical semantic memory over SQLite FTS5.

No embeddings. The single-model invariant rules out a separate embedder
(would require pulling another Ollama model), and the chat model's own
embeddings endpoint is not reliably exposed for ``gemma4:e4b``. FTS5
gives us BM25 ranking out of the box, which is the right primitive for
"the Trader/Elder/agent wants the most-relevant N events for the
current scene topic."

Schema:

    -- payload metadata + insertion bookkeeping
    docs(doc_id TEXT PRIMARY KEY, payload_json TEXT)

    -- standard FTS5 virtual table (NOT contentless — the text column
    -- holds the indexed string). Tokenizer 'unicode61' is the default.
    docs_fts USING fts5(doc_id UNINDEXED, text, tokenize='unicode61')

Re-index is a delete+insert in one transaction. ``top_k`` escapes
user input through FTS5's MATCH-with-quoted-phrase form so operators
like AND/OR and special chars are safe.

Concurrency: ``check_same_thread=False`` lets the watchdog read
concurrently with index writes; ``_lock`` serializes all DB access so
SQLite cursors don't interleave.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from microverse.memory._sqlite import open_sqlite_wal

_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS docs (doc_id TEXT PRIMARY KEY, payload_json TEXT)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
        doc_id UNINDEXED,
        text,
        tokenize='unicode61'
    )""",
]


@dataclass(frozen=True, slots=True)
class Hit:
    """One result row from ``top_k``."""

    doc_id: str
    score: float
    payload: dict[str, Any]


# FTS5's "MATCH" syntax interprets characters like " : ( ) AND OR NEAR
# as operators. We always run input through this token extractor and
# OR-join the surviving terms — no operator surface area exposed to
# callers, and no SQL injection (the value is still parameterized).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _safe_match_query(query: str) -> str:
    tokens = [t for t in _TOKEN_RE.findall(query) if t]
    if not tokens:
        return ""
    # Quote each token to disable column-prefix syntax (e.g. col:foo).
    return " OR ".join(f'"{t}"' for t in tokens)


class SemanticMemory:
    """File-backed FTS5 store of recent events / lore chunks."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = open_sqlite_wal(path)
        for stmt in _SCHEMA:
            self._conn.execute(stmt)
        self._conn.commit()
        self._lock = threading.Lock()

    def index(
        self,
        *,
        doc_id: str,
        text: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        payload_json = json.dumps(payload or {}, separators=(",", ":"))
        with self._lock, self._conn:
            # Upsert payload row.
            self._conn.execute(
                "INSERT INTO docs (doc_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET payload_json=excluded.payload_json",
                (doc_id, payload_json),
            )
            # FTS row: delete by doc_id then insert.
            self._conn.execute("DELETE FROM docs_fts WHERE doc_id = ?", (doc_id,))
            self._conn.execute(
                "INSERT INTO docs_fts (doc_id, text) VALUES (?, ?)",
                (doc_id, text),
            )

    def delete(self, doc_id: str) -> bool:
        """Remove a doc by id. Returns True if a row was removed."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            self._conn.execute("DELETE FROM docs_fts WHERE doc_id = ?", (doc_id,))
            return (cur.rowcount or 0) > 0

    def list_ids(self, *, prefix: str | None = None) -> list[str]:
        """Return all doc_ids, optionally filtered by ``prefix``.

        Phase 3b's Elder uses this to enumerate stale lore chunks for
        replacement. The prefix is escaped so ``%`` or ``_`` in user
        input doesn't act as a SQL LIKE wildcard.
        """
        with self._lock:
            if prefix is None:
                rows = self._conn.execute("SELECT doc_id FROM docs").fetchall()
            else:
                escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = self._conn.execute(
                    "SELECT doc_id FROM docs WHERE doc_id LIKE ? ESCAPE '\\'",
                    (escaped + "%",),
                ).fetchall()
        return [r[0] for r in rows]

    def top_k(self, query: str, k: int = 5) -> list[Hit]:
        if k <= 0:
            return []
        match = _safe_match_query(query)
        if not match:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT docs_fts.doc_id, bm25(docs_fts), docs.payload_json "
                "FROM docs_fts JOIN docs USING (doc_id) "
                "WHERE docs_fts MATCH ? "
                "ORDER BY bm25(docs_fts) ASC "  # bm25() is negative; smaller = better
                "LIMIT ?",
                (match, k),
            ).fetchall()
        return [
            Hit(doc_id=r[0], score=float(r[1]), payload=json.loads(r[2]) if r[2] else {})
            for r in rows
        ]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SemanticMemory:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

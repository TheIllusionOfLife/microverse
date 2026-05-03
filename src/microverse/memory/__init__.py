"""Memory layer assembly: combine episodic + semantic into a single
``WorldContext`` for the agent's ``think()`` call.

Token-budget contract (Phase 3a):
  - working memory (the persona template + base world fields):
    ~1500 tokens, owned by the persona — we don't enforce here.
  - ``recent_episodic`` (last-7-days events, packed in reverse order):
    ≤ ``episodic_tok`` (default 1500).
  - ``lore_excerpt`` (top FTS5 hits keyed off the scene topic):
    ≤ ``lore_tok`` (default 600).

Total intended ceiling is 4096 tokens. The token estimate is a cheap
``len(text) // 4`` heuristic — over-counts on prose with short words,
under-counts on dense punctuation, but cheap and conservative for a
single-model loop where we control prompt shape.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from microverse.agents.base import WorldContext

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory
    from microverse.memory.semantic import SemanticMemory


SEVEN_DAYS_S: float = 7 * 24 * 3600.0


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
        raise RuntimeError(f"failed to enable WAL on db {path!r}; got mode={mode_row}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def est_tokens(text: str) -> int:
    """Cheap token estimate (chars // 4). See module docstring."""
    return len(text) // 4


def _format_episodic(actor: str, action: str, thought: str) -> str:
    if thought:
        return f"{actor} {action}: {thought}"
    return f"{actor} {action}"


def _pack_under_budget(items: list[str], token_budget: int, joiner: str = "\n") -> tuple[str, ...]:
    """Take items in order; drop trailing items once the rendered
    (joined) token count would exceed the budget. We measure the
    joined length in characters and apply ``len // 4`` so the result
    matches what callers will compute on the same join.
    """
    kept: list[str] = []
    joined_len = 0
    join_len = len(joiner)
    for item in items:
        new_len = joined_len + len(item) + (join_len if kept else 0)
        if new_len // 4 > token_budget:
            break
        kept.append(item)
        joined_len = new_len
    return tuple(kept)


_LORE_DIGEST_CHARS = 200  # cap on the fallback "k=v, k=v" digest


def _payload_digest(payload: dict[str, object]) -> str:
    """Bounded digest of an arbitrary payload — guards against a single
    large payload value blowing through the lore budget by itself."""
    parts: list[str] = []
    for k, v in payload.items():
        text = str(v).replace("\n", " ").strip()
        if len(text) > 80:
            text = text[:80] + "…"
        parts.append(f"{k}={text}")
        if sum(len(p) for p in parts) > _LORE_DIGEST_CHARS:
            break
    digest = ", ".join(parts)
    return digest[:_LORE_DIGEST_CHARS]


def build_context(
    *,
    world_base: WorldContext,
    episodic: EpisodicMemory,
    semantic: SemanticMemory,
    topic: str = "",
    episodic_tok: int = 1500,
    lore_tok: int = 600,
    episodic_window_s: float = SEVEN_DAYS_S,
    episodic_lookback: int = 2000,
    lore_k: int = 3,
) -> WorldContext:
    """Assemble the per-tick context from episodic + semantic memory.

    Episodic events are pulled via ``episodic.since(cutoff, limit=...)``
    so the 7-day window covers every event in that window (capped at
    ``episodic_lookback`` rows for memory ceiling). The packer then
    drops trailing items until the joined string fits under
    ``episodic_tok``.
    """
    cutoff = time.time() - episodic_window_s
    events = episodic.since(cutoff, limit=episodic_lookback)
    recent_lines = [
        _format_episodic(e.actor, e.action, str(e.payload.get("thought") or "")) for e in events
    ]
    recent = _pack_under_budget(recent_lines, episodic_tok)

    lore_lines: list[str] = []
    if topic.strip():
        for hit in semantic.top_k(topic, k=lore_k):
            text = str(hit.payload.get("text") or hit.payload.get("summary") or "")
            if not text:
                text = _payload_digest(hit.payload)
            lore_lines.append(f"- ({hit.doc_id}) {text}")
    lore = _pack_under_budget(lore_lines, lore_tok)

    return WorldContext(
        season=world_base.season,
        weather=world_base.weather,
        peers_today=world_base.peers_today,
        recent_episodic=recent,
        lore_excerpt=lore,
    )

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

import time
from typing import TYPE_CHECKING

from microverse.agents.base import WorldContext

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory, Event
    from microverse.memory.semantic import SemanticMemory


SEVEN_DAYS_S: float = 7 * 24 * 3600.0


def est_tokens(text: str) -> int:
    """Cheap token estimate (chars // 4). See module docstring."""
    return len(text) // 4


def _format_episodic(actor: str, action: str, thought: str) -> str:
    if thought:
        return f"{actor} {action}: {thought}"
    return f"{actor} {action}"


REST_SUMMARY_SUPPRESS_AT: int = 10


def _compress_rest_runs(events: list[Event]) -> list[str]:
    """Collapse runs of >=2 consecutive same-actor rest events into a
    single count-only summary line so a long rest streak cannot poison
    ``recent_episodic``. Runs of length >= ``REST_SUMMARY_SUPPRESS_AT``
    emit *nothing* — beyond that threshold the count itself becomes
    enough signal for the LLM to infer fatigue and continue resting.

    A single isolated rest is left verbatim (its thought is real recent
    context, not a run). Runs of 2..(threshold-1) render as a count-only
    line. Runs of >= threshold are dropped from the slice entirely.
    Runs are broken by any non-rest event or a rest by a different
    actor.

    Layer history:
      - Layer C: render runs as one summary with "Latest: <thought>".
      - Layer D: drop the thought; summary became count-only.
      - Layer E.1 (this layer): suppress entirely above the threshold,
        because even "Aki rested 57 times" was enough fatigue signal in
        soak-wiring-resoak-3 (post-Layer-D, seed 38, 43% rest).
    """
    out: list[str] = []
    run_actor: str | None = None
    run_count = 0
    run_thought = ""

    def flush() -> None:
        nonlocal run_actor, run_count, run_thought
        if run_count >= REST_SUMMARY_SUPPRESS_AT and run_actor:
            # Drop the entire run from the slice. The count alone still
            # carries fatigue signal — see soak-wiring-resoak-3.
            pass
        elif run_count >= 2 and run_actor:
            out.append(f"{run_actor} rested {run_count} times")
        elif run_count == 1 and run_actor:
            out.append(_format_episodic(run_actor, "rest", run_thought))
        run_actor = None
        run_count = 0
        run_thought = ""

    for e in events:
        thought = str(e.payload.get("thought") or "")
        if e.action == "rest":
            if run_actor == e.actor:
                run_count += 1
            else:
                flush()
                run_actor = e.actor
                run_count = 1
                run_thought = thought
        else:
            flush()
            out.append(_format_episodic(e.actor, e.action, thought))
    flush()
    return out


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
    recent_lines = _compress_rest_runs(events)
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

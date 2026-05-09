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
from microverse.config import REST_SUMMARY_SUPPRESS_AT

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory, Event
    from microverse.memory.semantic import SemanticMemory


SEVEN_DAYS_S: float = 7 * 24 * 3600.0


def est_tokens(text: str) -> int:
    """Cheap token estimate (chars // 4). See module docstring."""
    return len(text) // 4


_ARTIFACT_EXCERPT_MAX = 120

_ACTION_PAST_TENSE: dict[str, str] = {
    "rest": "rested",
    "speak": "spoke",
    "study": "studied",
    "craft": "crafted",
    "travel": "traveled",
}


def _format_episodic_event(e: Event) -> str:
    """Render a single episodic event for inclusion in next-prompt
    ``recent_episodic``. Layer G structural contract: the agent's
    ``thought`` is NEVER rendered — the autobiographical feedback
    edge that sustained the prior six layers' failure family. Only
    factual surface (action, target, artifact-excerpt, ``[world]`` /
    ``[harvest]`` tags) flows into the next prompt.

    The thought is still emitted by the LLM, persisted into episodic
    for audit, and consumed by current-tick logic; this function is
    the boundary that prevents it from re-feeding the loop.
    """
    actor = e.actor
    action = e.action
    payload = e.payload or {}
    target = e.target

    if actor == "world":
        return f"[world] {action}"
    if actor == "harvest" and action == "rated":
        # Alt-B exogenous feedback: surface the Trader's verdict so the
        # next-tick context shows what is actually being valued, not
        # the agent's own narrative-about-its-narrative.
        rated_actor = str(payload.get("actor") or "")
        kind = str(payload.get("kind") or "artifact")
        score_raw = payload.get("score")
        score_str = f"{float(score_raw):.2f}" if isinstance(score_raw, int | float) else "?"
        accepted_tag = "accepted" if payload.get("accepted") else "rejected"
        return f"[harvest] Trader rated {rated_actor}'s {kind} {score_str} ({accepted_tag})"
    if action == "craft":
        artifact = str(payload.get("artifact") or "").replace("\n", " ").strip()
        if artifact:
            if len(artifact) > _ARTIFACT_EXCERPT_MAX:
                artifact = artifact[:_ARTIFACT_EXCERPT_MAX] + "…"
            return f"{actor} crafted: {artifact}"
        return f"{actor} craft"
    if action == "speak":
        return f"{actor} spoke to {target}" if target else f"{actor} spoke aloud"
    return f"{actor} {action}"


def _compress_action_runs(events: list[Event]) -> list[str]:
    """Collapse runs of >=2 consecutive same-actor + same-action events
    into a single count-only summary so no streak shape can become a
    same-channel signal in the next prompt. Generalises the Layer
    C/D/E.1 rest-run mechanism to ALL actions, closing speak / study /
    travel / craft as alternative expressions of the same crystallised
    persona.

    A run of length >= ``REST_SUMMARY_SUPPRESS_AT`` is dropped entirely
    (Layer E.1 logic preserved and extended). Runs of 2..(threshold-1)
    render as ``f"{actor} {past_tense} {N} times"``. A length-1 run
    falls back to ``_format_episodic_event`` (which already drops the
    thought). Runs are broken by any change of actor or action.
    """
    out: list[str] = []
    run_actor: str | None = None
    run_action: str | None = None
    run_events: list[Event] = []

    def flush() -> None:
        nonlocal run_actor, run_action, run_events
        n = len(run_events)
        if n >= REST_SUMMARY_SUPPRESS_AT and run_actor and run_action:
            # Drop entirely — the count alone is enough signal for the
            # LLM to continue the streak (Layer E.1 finding, generalised).
            pass
        elif n >= 2 and run_actor and run_action:
            verb = _ACTION_PAST_TENSE.get(run_action, run_action)
            out.append(f"{run_actor} {verb} {n} times")
        elif n == 1 and run_events:
            out.append(_format_episodic_event(run_events[0]))
        run_actor = None
        run_action = None
        run_events = []

    for e in events:
        if run_actor == e.actor and run_action == e.action:
            run_events.append(e)
        else:
            flush()
            run_actor = e.actor
            run_action = e.action
            run_events = [e]
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
    recent_lines = _compress_action_runs(events)
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
        engagement_hint=world_base.engagement_hint,
        required_target=world_base.required_target,
    )

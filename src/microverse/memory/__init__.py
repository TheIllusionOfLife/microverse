"""Memory layer assembly: combine peer + world view + lore into a
single ``WorldContext`` for the agent's ``think()`` call.

Path-3 stateless-tick contract (Slice 5): the agent's per-tick prompt
sees only persona + world state + peer presence + exogenous nudges.
Self-history is structurally absent — the autobiographical channel
that sustained seven prior layers' attractor is gone, not narrowed.

Token-budget contract:
  - working memory (the persona template + base world fields):
    ~1500 tokens, owned by the persona — we don't enforce here.
  - ``peer_inbox`` (most-recent-tick speaks-to-self, ≤80-char
    utterances): bounded by ``_PEER_UTTERANCE_MAX`` per item; the
    helper truncates upstream of the budget.
  - ``world_events`` (only ``actor=='world'`` events since the
    receiver's last own-tick): assembled by ``_build_world_events``.
  - ``lore_excerpt`` (top FTS5 hits keyed off the scene topic):
    ≤ ``lore_tok`` (default 600).

Total intended ceiling is 4096 tokens. The token estimate is a cheap
``len(text) // 4`` heuristic — over-counts on prose with short words,
under-counts on dense punctuation, but cheap and conservative for a
single-model loop where we control prompt shape.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from microverse.agents.base import PeerSpeech, WorldContext
from microverse.world.workshop import WIPView

if TYPE_CHECKING:
    from microverse.memory.episodic import EpisodicMemory
    from microverse.memory.semantic import SemanticMemory
    from microverse.ops.metrics import Metrics
    from microverse.world.workshop import WorkshopProjection


def est_tokens(text: str) -> int:
    """Cheap token estimate (chars // 4). See module docstring."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# Path-3 builders for the bounded peer + world view that replaces the
# autobiographical ``recent_episodic`` channel.
#
# These helpers are pure: they read the episodic log, filter on actor /
# target / since_ts, and return tuples for assembly into the per-tick
# ``WorldContext``. The runtime in ``run.py`` calls them once per agent
# per tick with the agent's own ``_last_tick_ts`` watermark; the result
# rides through ``world_base`` into ``build_context`` and out to the
# persona renderer.
# ---------------------------------------------------------------------------


_PEER_UTTERANCE_MAX = 80


def _truncate_at_word_boundary(text: str, *, ceiling: int = _PEER_UTTERANCE_MAX) -> str:
    """Cap ``text`` at ``ceiling`` chars, retreating to the last space
    so the prompt never carries a mid-word fragment. Returns the
    bounded prefix + ellipsis when the input exceeds the ceiling;
    otherwise returns the input verbatim.
    """
    if len(text) <= ceiling:
        return text
    head = text[:ceiling]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip() + "…"


def _build_peer_inbox(
    episodic: EpisodicMemory,
    *,
    agent_name: str,
    since_ts: float,
    metrics: Metrics | None = None,
) -> tuple[PeerSpeech, ...]:
    """Build the per-tick inbox of speaks-to-self by other agents
    since the receiver's last own-tick.

    Filters applied (in order):
      1. ``ts > since_ts`` — drop stale speaks. The watermark is
         exclusive: an event with ``ts == since_ts`` is the agent's
         own commit (or coincident with it), so it is already drained.
      2. ``action == "speak"`` — only speak events qualify.
      3. ``target == agent_name`` — only speaks-to-self.
      4. ``actor != agent_name`` — defence-in-depth against
         autobiographical leak via own speeches.
      5. Empty utterance after strip — drop (renders as
         ``"- Bo: "`` noise).
      6. Receiver-name whole-word match (case-insensitive) in the
         utterance — DROP the entire PeerSpeech (Codex review HIGH
         on cross-agent narrative laundering). Substring matches like
         ``Akihiko`` for receiver ``Aki`` do not trip this filter.

    The utterance is sourced from ``payload.get("thought")`` because
    the existing ``Action`` schema has no separate utterance field.
    This means the speaker's narrative voice rides through; the
    structural mitigations above bound the leak.

    Uses ``EpisodicMemory.since`` so a hot run with > 200 events
    between ticks does not silently drop fresh addressed speeches.

    Returns chronologically ordered (oldest-first) PeerSpeech tuples.
    """
    rows = episodic.since(since_ts)
    name_pattern = re.compile(rf"\b{re.escape(agent_name)}\b", re.IGNORECASE)
    out: list[PeerSpeech] = []
    for e in rows:
        if e.ts <= since_ts:
            continue
        if e.action != "speak":
            continue
        if e.target != agent_name:
            continue
        if e.actor == agent_name:
            continue
        utterance = str((e.payload or {}).get("thought") or "").strip()
        if not utterance:
            continue
        if name_pattern.search(utterance):
            if metrics is not None:
                metrics.bump("peer_inbox_dropped", agent=agent_name)
            continue
        out.append(
            PeerSpeech(
                speaker=e.actor,
                utterance=_truncate_at_word_boundary(utterance),
            )
        )
    out.reverse()  # episodic.since is newest-first; flip to chronological
    return tuple(out)


def _build_world_events(
    episodic: EpisodicMemory,
    *,
    since_ts: float,
) -> tuple[str, ...]:
    """Build the per-tick view of world events (weather, season,
    arrivals) since ``since_ts``. NEVER any agent action — the
    ``actor == "world"`` filter is exclusive.

    Watermark is exclusive (``ts > since_ts``) so an event coincident
    with the agent's own tick boundary cannot replay. Uses
    ``EpisodicMemory.since`` so a hot run does not silently drop
    fresh world events past the lookback cap.

    Returns chronologically ordered ``"[world] {action}"`` strings.
    """
    rows = episodic.since(since_ts)
    out: list[str] = []
    for e in rows:
        if e.ts <= since_ts:
            continue
        if e.actor != "world":
            continue
        out.append(f"[world] {e.action}")
    out.reverse()
    return tuple(out)


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


_WORKSHOP_FRAGMENT_TAIL = 4  # last N fragments included in each WIPView excerpt
_WORKSHOP_REDACTED_MARKER = "[earlier contributor wove ...]"


def _build_workshop_view(
    workshop: WorkshopProjection,
    *,
    agent_name: str,
    metrics: Metrics | None,
) -> tuple[WIPView, ...]:
    """Render a per-receiver view of every configured WIP.

    ADR 0003 Decision 1 (load-bearing per Codex): the receiver's own
    fragment texts are replaced by ``_WORKSHOP_REDACTED_MARKER`` and
    the receiver's own contributor name is masked. Non-receiver
    contributors and their fragment texts pass through verbatim
    (community knowledge is preserved at the village level; only the
    *receiver's own* contributions are redacted in their own view).

    Contributor identity is matched by case-insensitive *exact* string
    equality (``casefold``) because the contributor field is a stored
    actor name, not free text. This is more correct than a regex
    whole-word check, which fails on names containing non-word
    characters (``C++``, ``Aki.``). Substring names like ``Akihiko``
    vs receiver ``Aki`` are correctly NOT matched.

    Every redaction bumps ``workshop_view_self_redactions`` per-agent
    so operators can verify the redaction is firing under live load.

    ADR 0005 Decision 1 (v0.3.1): WIPs in ``complete`` phase are
    filtered out of the per-receiver view entirely. The persona prompt
    only ever sees ``forming`` or ``developing`` WIPs. The projection
    itself is unchanged — the Harvester still sees complete WIPs at
    flush time, and the validator still hard-folds any contribute that
    targets a complete WIP by name (defence-in-depth). Each hidden
    WIP bumps ``workshop_view_hidden_complete`` per-agent so operators
    can watch the filter fire under live load.
    """
    receiver_key = agent_name.casefold()
    views: list[WIPView] = []
    for wip in workshop.wips():
        if wip.phase == "complete":
            if metrics is not None:
                metrics.bump("workshop_view_hidden_complete", agent=agent_name)
            continue
        # Contributors: drop the receiver's own name; preserve order.
        peer_contribs: list[str] = []
        for c in wip.contributors():
            if c.casefold() == receiver_key:
                continue
            peer_contribs.append(c)
        contributors = ", ".join(peer_contribs)

        # Excerpt: last N fragments. Replace own texts with the
        # redacted marker; keep peer texts verbatim. Also bump
        # the metric on every redaction so operators can see it.
        tail = wip.fragments[-_WORKSHOP_FRAGMENT_TAIL:]
        lines: list[str] = []
        for f in tail:
            if f.contributor.casefold() == receiver_key:
                if metrics is not None:
                    metrics.bump("workshop_view_self_redactions", agent=agent_name)
                lines.append(_WORKSHOP_REDACTED_MARKER)
            else:
                lines.append(f"{f.contributor}: {f.text}")
        excerpt = "\n".join(lines)

        views.append(
            WIPView(
                name=wip.name,
                phase=wip.phase,
                contributors=contributors,
                excerpt=excerpt,
            )
        )
    return tuple(views)


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
    lore_tok: int = 600,
    lore_k: int = 3,
    receiver_name: str | None = None,
    workshop: WorkshopProjection | None = None,
    metrics: Metrics | None = None,
) -> WorldContext:
    """Assemble the per-tick context from semantic memory + the
    pre-populated bounded fields on ``world_base``.

    Path-3 stateless-tick contract: the autobiographical
    ``recent_episodic`` channel is gone. Self-history never enters
    the prompt. ``peer_inbox`` and ``world_events`` ride through from
    ``world_base`` (populated by ``run.py:_build_per_tick_world_base``
    using per-agent watermarks). The only thing this function adds is
    the ``lore_excerpt`` keyed off the scene topic.

    Slice 6 (Codex review HIGH): when ``receiver_name`` is provided,
    every lore line containing that name as a whole word is dropped.
    This closes the lore-channel autobiographical leak — Elder lore
    can still mention agents by name (community knowledge stays
    visible to OTHER readers); the receiving agent simply doesn't see
    lore about themselves.

    ``episodic`` is retained in the signature for forward-compat
    (other future memory layers may need it) but is currently
    unused.
    """
    del episodic  # forward-compat placeholder; see docstring.
    lore_lines: list[str] = []
    if topic.strip():
        for hit in semantic.top_k(topic, k=lore_k):
            text = str(hit.payload.get("text") or hit.payload.get("summary") or "")
            if not text:
                text = _payload_digest(hit.payload)
            lore_lines.append(f"- ({hit.doc_id}) {text}")

    if receiver_name:
        name_pattern = re.compile(rf"\b{re.escape(receiver_name)}\b", re.IGNORECASE)
        lore_lines = [line for line in lore_lines if not name_pattern.search(line)]

    lore = _pack_under_budget(lore_lines, lore_tok)

    if workshop is not None and receiver_name:
        workshop_view = _build_workshop_view(workshop, agent_name=receiver_name, metrics=metrics)
    else:
        workshop_view = ()

    return WorldContext(
        season=world_base.season,
        weather=world_base.weather,
        peers_today=world_base.peers_today,
        peer_inbox=world_base.peer_inbox,
        world_events=world_base.world_events,
        lore_excerpt=lore,
        engagement_hint=world_base.engagement_hint,
        required_target=world_base.required_target,
        workshop_view=workshop_view,
    )

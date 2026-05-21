"""Verb-diversity lever — Phase D structural counter-pressure for ADR 0002.

The Artisan/Scholar/Stranger personas tend to collapse onto a single
dominant verb (Artisan to ``craft`` at ~93% in the ADR 0002 24h soak).
Seven prompt patches did not move it; ADR 0002 documents the limit at
the model level.

Phase D adds two cheap structural levers on top of the persona:

  1. ``WorldContext.novelty_hint`` — when the agent's top recent verb
     share crosses a threshold, the run-loop computes an underused
     verb via :func:`suggest_underused_verb` and surfaces a one-line
     hint inside the persona prompt. The LLM may ignore it.
  2. Post-action substitution in the agent's ``think()`` wrapper:
     when the LLM does ignore the hint and re-emits the dominant
     verb, the agent flips a coin (30% by default) and substitutes
     the chosen action verb with the underused one. The substitution
     uses a NEUTRAL replacement thought; the diversity counter is
     bumped so the dashboard can show how much of the observed verb
     mix is LLM-chosen vs lever-flipped.

This is structural counter-pressure equivalent to F.2 and the
engagement-gate coercions — NOT a claim to dissolve ADR 0002's
model-level limit.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from microverse.memory.episodic import EpisodicMemory


def recent_verb_distribution(
    episodic: EpisodicMemory,
    agent_name: str,
    *,
    lookback: int = 200,
) -> Counter[str]:
    """Counter of action verbs in the most recent ``lookback`` actions
    by ``agent_name``. Other agents' actions are filtered out.
    Actions other than the agent's own (world.*, scene.*, workshop.*)
    are also ignored — only ``action`` values that match a verb enum
    survive (any string is allowed; the caller can filter further).

    Uses ``EpisodicMemory.last(lookback * K)`` and filters in Python
    so we don't add a new SQL helper. K=4 gives plenty of headroom
    when the agent shares the log with peers + world events.
    """
    # Pull a generous window — the events table mixes agents and world
    # events, so we may need to over-pull to find `lookback` of THIS
    # agent's own actions. K=4 is a soft ceiling; if not enough are
    # found, return whatever we have.
    rows: Any = episodic.last(max(lookback * 4, 200))
    verbs: list[str] = []
    for ev in rows:
        if ev.actor != agent_name:
            continue
        # Skip non-agent actions (scene.open, workshop.recycle, etc).
        if "." in ev.action:
            continue
        verbs.append(ev.action)
        if len(verbs) >= lookback:
            break
    return Counter(verbs)


def suggest_underused_verb(
    dist: Counter[str],
    available: list[str],
    *,
    dominance_threshold: float = 0.50,
) -> str | None:
    """Return an underused verb from ``available`` when the dominant
    verb's share crosses ``dominance_threshold``. Returns None when:

    - ``dist`` is empty (no history → no signal),
    - no single verb dominates (healthy diversity → no nudge),
    - or every available verb has the same share as the dominant.

    When triggered, picks the verb in ``available`` with the lowest
    count (ties broken by ``available`` order — stable, no RNG).
    """
    total = sum(dist.values())
    if total == 0 or not available:
        return None
    top_count = max(dist.values())
    if top_count / total < dominance_threshold:
        return None
    # Restrict to available verbs; pick the lowest-count one. Verbs
    # that are not in dist at all count as 0 (most underused).
    scored: list[tuple[str, int]] = [(v, dist.get(v, 0)) for v in available]
    # If the dominant verb itself is "available" but has the lowest
    # count (degenerate when all peers shifted away mid-window), no
    # hint fires.
    least_count = min(c for _, c in scored)
    if least_count >= top_count:
        return None
    for verb, count in scored:
        if count == least_count and count < top_count:
            return verb
    return None

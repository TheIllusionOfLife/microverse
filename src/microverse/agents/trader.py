"""Trader: ranks an artifact buffer for the Harvester.

Phase 2 role. Takes a list of artifact candidates, asks the LLM to
score each on novelty/utility/completeness, returns one ``Score`` per
candidate keyed by index. The Harvester then applies a percentile
threshold (default p70) to decide which candidates are worth the
host's attention.

Trader uses *factual* sampling so the same artifact gets ~the same
score across rounds. Out-of-range scores are clamped to ``[0.0, 1.0]``;
missing entries are filled with ``0.0`` so the result list always has
the same length as the input. The pipeline never raises.
"""

from __future__ import annotations

from typing import Any, TypeGuard

from pydantic import BaseModel, ConfigDict, Field

from microverse._text import jaccard, safe_json_loads, tokenize
from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.agents.harvester import ArtifactCandidate, WIPCandidate
from microverse.config import (
    LLM_MAX_TOKENS_RANK,
    LLM_TIMEOUT_RANK_S,
    MAX_PARSE_BYTES,
    SAMPLING_FACTUAL,
    TRADER_WIP_NOVELTY_LOOKBACK,
)
from microverse.llm.ollama_client import chat
from microverse.prompts import render

_PERSONA_TEMPLATE = "persona_trader.j2"


class Score(BaseModel):
    """One Trader judgment for a single artifact."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    artifact_id: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=300)


_PREFERRED_LIST_KEYS = ("scores", "rankings", "items", "artifacts", "results")

# Ollama JSON Schema. Forces an array root so gemma4:e4b cannot collapse
# to a single object the way it does under format="json". Empirically
# verified against gemma4:e4b under think=False; re-check on Ollama
# upgrades (cf. ollama/ollama#15260 for known regressions in this area).
_RANK_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            # Bounds mirror the ``Score`` Pydantic model (ge=0 / 0.0-1.0 /
            # max_length=300) so the model is steered toward valid output
            # rather than relying on ``_coerce_score`` to clamp after.
            "artifact_id": {"type": "integer", "minimum": 0},
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "maxLength": 300},
        },
        "required": ["artifact_id", "score"],
    },
}


def _list_looks_like_scores(value: object) -> TypeGuard[list[dict[str, Any]]]:
    """Quick shape check: list of dicts each having an ``artifact_id``."""
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(d, dict) and "artifact_id" in d for d in value)


def _extract_list(data: Any) -> list[dict[str, Any]]:
    """Pull the score list out of common JSON shapes:

      - direct list:                              ``[{...}, {...}]``
      - object with a known wrapping key:         ``{"scores": [...]}``
                                                   ``{"rankings": [...]}``
      - object whose values include exactly one
        list-of-dicts-with-artifact_id:           ``{"x": [...], "y": "z"}``

    Anything else yields an empty list. The fallback specifically rejects
    "first list value" because a model might emit a metadata list or a
    rationale list that happens to come before the real one.
    """
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        # 1. Try known wrapping keys.
        for key in _PREFERRED_LIST_KEYS:
            value = data.get(key)
            if _list_looks_like_scores(value):
                return [d for d in value if isinstance(d, dict)]
        # 2. Find the *unique* list-of-score-dicts among all values.
        candidates = [v for v in data.values() if _list_looks_like_scores(v)]
        if len(candidates) == 1:
            return [d for d in candidates[0] if isinstance(d, dict)]
        # 3. Single Score-shaped object at root: wrap so the caller still
        # gets one score (gemma4 sometimes emits this under format="json").
        if "artifact_id" in data:
            return [data]
    return []


def _safe_parse_scores(raw: str) -> list[dict[str, Any]]:
    """Best-effort parse: strict → json_repair → empty list."""
    if len(raw.encode("utf-8", errors="replace")) > MAX_PARSE_BYTES:
        return []
    parsed = safe_json_loads(raw)
    if parsed is None:
        return []
    return _extract_list(parsed)


def _wip_text(c: WIPCandidate) -> str:
    """All fragment texts joined for tokenisation."""
    return " ".join(text for _, text in c.fragments)


def score_wip(c: WIPCandidate, *, last_completed: list[WIPCandidate]) -> float:
    """Rule-based score for a completed WIP — no LLM call.

    ADR 0003 Decision 3: Trader v2 must not become a new attractor.
    The scoring features are mechanistic, not vibe-based:

      * **length**: total character count of all fragments, capped at
        1.0 once the WIP reaches 1000 chars. Encourages WIPs that
        accreted substantial text.
      * **contributors**: distinct-contributor count, capped at 1.0
        once the WIP has at least 3 contributors. Encourages
        cross-agent collaboration.
      * **novelty**: 1 - mean Jaccard similarity (over min_len=4
        tokens, stop-words dropped) against the most recent
        ``TRADER_WIP_NOVELTY_LOOKBACK`` completed WIPs. Penalises a
        WIP that repeats the vocabulary of its predecessors.

    Score is the mean of the three components — every value in
    [0.0, 1.0]. The lookback uses the existing _text.tokenize +
    jaccard helpers; no new tokeniser, no Elder reach-into.
    """
    total_chars = sum(len(t) for _, t in c.fragments)
    length_score = min(total_chars / 1000.0, 1.0)
    contrib_score = min(len(c.contributors) / 3.0, 1.0)
    if not last_completed:
        novelty_score = 1.0
    else:
        tokens_c = tokenize(_wip_text(c), min_len=4, drop_stopwords=True)
        sims: list[float] = []
        for prev in last_completed[:TRADER_WIP_NOVELTY_LOOKBACK]:
            tokens_p = tokenize(_wip_text(prev), min_len=4, drop_stopwords=True)
            sims.append(jaccard(tokens_c, tokens_p))
        novelty_score = 1.0 - (sum(sims) / len(sims))
    raw = (length_score + contrib_score + novelty_score) / 3.0
    return max(0.0, min(1.0, raw))


def _coerce_score(entry: dict[str, Any], artifact_id: int) -> Score:
    raw_score = entry.get("score", 0.0)
    try:
        clamped = max(0.0, min(1.0, float(raw_score)))
    except (TypeError, ValueError):
        clamped = 0.0
    rationale = str(entry.get("rationale", ""))[:300]
    return Score(artifact_id=artifact_id, score=clamped, rationale=rationale)


class Trader(Agent):
    role = "trader"
    persona_template = _PERSONA_TEMPLATE
    sampling = SAMPLING_FACTUAL

    def __init__(self, name: str, *, soul_tokens: int = 100) -> None:
        super().__init__(name, soul_tokens=soul_tokens)
        # v0.2 (ADR 0003): Trader v2 maintains a small bounded history
        # of completed WIPs across rank() calls so the novelty term in
        # ``score_wip`` has a stable reference. Capped at
        # TRADER_WIP_NOVELTY_LOOKBACK; older entries drop off the
        # head.
        self._wip_history: list[WIPCandidate] = []

    # Trader's per-tick role is judging, not narrating. ``think`` exists
    # to satisfy the Agent ABC if a tick loop ever decides to schedule
    # the Trader directly; the ranking work is in :meth:`rank`.
    def think(self, world: WorldContext) -> Action:
        return Action(action=ActionKind.REST)

    def rank(
        self, candidates: list[ArtifactCandidate | WIPCandidate]
    ) -> list[Score]:
        """Heterogeneous rank: ArtifactCandidate via LLM (existing path);
        WIPCandidate via the rule-based ``score_wip`` (no LLM call).

        Returns one Score per candidate in index order. The two
        score populations live in the same [0, 1] interval, so the
        Harvester's percentile cutoff applies uniformly.
        """
        if not candidates:
            return []

        artifact_indices = [
            i for i, c in enumerate(candidates) if isinstance(c, ArtifactCandidate)
        ]
        wip_indices = [
            i for i, c in enumerate(candidates) if isinstance(c, WIPCandidate)
        ]

        # Score WIPs purely with the rule-based path. Update the
        # novelty history AFTER scoring so this batch's WIPs don't
        # penalise each other within the same flush.
        wip_scores: dict[int, Score] = {}
        for i in wip_indices:
            cand = candidates[i]
            assert isinstance(cand, WIPCandidate)
            value = score_wip(cand, last_completed=self._wip_history)
            wip_scores[i] = Score(artifact_id=i, score=value, rationale="rule-based")

        # Score artifacts via the existing LLM path when any are
        # present. Skip the LLM call entirely if the batch is
        # WIP-only — keeps the no-LLM contract of WIP-only flushes.
        artifact_scores: dict[int, Score] = {}
        if artifact_indices:
            artifact_candidates = [candidates[i] for i in artifact_indices]
            prompt = render(
                self.persona_template,
                candidates=[
                    {
                        "artifact_id": k,
                        "actor": c.actor,  # type: ignore[union-attr]
                        "action": c.action,  # type: ignore[union-attr]
                        "artifact": c.artifact,  # type: ignore[union-attr]
                    }
                    for k, c in enumerate(artifact_candidates)
                ],
            )
            result = chat(
                messages=[{"role": "user", "content": prompt}],
                think=False,
                format=_RANK_SCHEMA,
                options={**self.sampling, "num_predict": LLM_MAX_TOKENS_RANK},
                timeout_s=LLM_TIMEOUT_RANK_S,
            )
            entries = _safe_parse_scores(result["content"])
            by_inner_id: dict[int, dict[str, Any]] = {}
            for entry in entries:
                try:
                    aid = int(entry.get("artifact_id", -1))
                except (TypeError, ValueError):
                    continue
                if 0 <= aid < len(artifact_candidates):
                    by_inner_id[aid] = entry
            for inner_id, outer_id in enumerate(artifact_indices):
                coerced = _coerce_score(
                    by_inner_id.get(inner_id, {}), artifact_id=outer_id
                )
                artifact_scores[outer_id] = coerced

        # Merge in index order so the Harvester's zip is well-defined.
        out: list[Score] = []
        for i, _ in enumerate(candidates):
            if i in wip_scores:
                out.append(wip_scores[i])
            elif i in artifact_scores:
                out.append(artifact_scores[i])
            else:
                # Shouldn't happen for known kinds; defensive zero.
                out.append(Score(artifact_id=i, score=0.0, rationale=""))

        # Update novelty history AFTER scoring this batch. Newest at
        # the head so ``[:TRADER_WIP_NOVELTY_LOOKBACK]`` in
        # ``score_wip`` picks the most recent N.
        for i in wip_indices:
            cand = candidates[i]
            assert isinstance(cand, WIPCandidate)
            self._wip_history.insert(0, cand)
        del self._wip_history[TRADER_WIP_NOVELTY_LOOKBACK:]
        return out

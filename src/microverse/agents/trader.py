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

from microverse._text import safe_json_loads
from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.agents.harvester import ArtifactCandidate
from microverse.config import LLM_MAX_TOKENS, LLM_TIMEOUT_S, MAX_PARSE_BYTES, SAMPLING_FACTUAL
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
            "artifact_id": {"type": "integer"},
            "score": {"type": "number"},
            "rationale": {"type": "string"},
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

    # Trader's per-tick role is judging, not narrating. ``think`` exists
    # to satisfy the Agent ABC if a tick loop ever decides to schedule
    # the Trader directly; the ranking work is in :meth:`rank`.
    def think(self, world: WorldContext) -> Action:
        return Action(action=ActionKind.REST)

    def rank(self, candidates: list[ArtifactCandidate]) -> list[Score]:
        if not candidates:
            return []

        prompt = render(
            self.persona_template,
            candidates=[
                {"artifact_id": i, "actor": c.actor, "action": c.action, "artifact": c.artifact}
                for i, c in enumerate(candidates)
            ],
        )
        result = chat(
            messages=[{"role": "user", "content": prompt}],
            think=False,
            format=_RANK_SCHEMA,
            options={**self.sampling, "num_predict": LLM_MAX_TOKENS},
            timeout_s=LLM_TIMEOUT_S,
        )

        entries = _safe_parse_scores(result["content"])
        by_id: dict[int, dict[str, Any]] = {}
        for entry in entries:
            try:
                aid = int(entry.get("artifact_id", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= aid < len(candidates):
                by_id[aid] = entry

        # Always return one score per candidate, in index order.
        return [_coerce_score(by_id.get(i, {}), artifact_id=i) for i in range(len(candidates))]

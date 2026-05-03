"""Agent ABC + the strict ``Action`` schema agents must emit each tick.

Parse pipeline used by every agent's ``think()``:

    1. Strict JSON + Pydantic v2 validation. Increment ``json_ok`` on
       success, reset that agent's ``consecutive_fail`` counter.
    2. On failure, run ``json_repair`` and re-validate. Increment
       ``json_repaired`` on success.
    3. On failure of both, return a safe ``rest`` action. Increment
       ``json_fallback_rest`` and bump ``consecutive_fail`` for the
       agent so the watchdog can pause it after MAX_CONSECUTIVE_FAIL.

The pipeline never raises — it always returns an Action.
"""

from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from microverse._text import safe_json_loads
from microverse.config import MAX_PARSE_BYTES

if TYPE_CHECKING:
    from microverse.ops.metrics import Metrics


# Words an in-world inhabitant should never utter. ``\b`` boundaries
# avoid matching inside larger words (e.g., "compromised" doesn't trip
# "model"). Case-insensitive at compile time.
#
# "outside" was deliberately removed from the bare-word list because
# it appears in legitimate village prose ("outside the bakery", "the
# outside walls"). Meta uses are caught by the phrase regex below.
META_LEAK_RE = re.compile(r"\b(ai|model|simulation|prompt|llm|api)\b", re.IGNORECASE)
META_LEAK_PHRASE_RE = re.compile(
    r"\boutside\s+(?:the\s+|this\s+|our\s+)?(simulation|world|reality|system|prompt|run)\b",
    re.IGNORECASE,
)


def has_meta_leak(text: str) -> bool:
    """Return True if ``text`` contains an in-world meta-reference."""
    if not text:
        return False
    return bool(META_LEAK_RE.search(text) or META_LEAK_PHRASE_RE.search(text))


class ActionKind(StrEnum):
    SPEAK = "speak"
    CRAFT = "craft"
    STUDY = "study"
    REST = "rest"
    TRAVEL = "travel"


class Action(BaseModel):
    """One agent's decision for a single tick."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    thought: str = Field(default="", max_length=500)
    action: ActionKind
    target: str | None = Field(default=None, max_length=100)
    artifact: str | None = Field(default=None, max_length=8000)


def _rest_action() -> Action:
    return Action(thought="", action=ActionKind.REST, target=None, artifact=None)


def parse_action(raw: str, *, metrics: Metrics, agent: str) -> Action:
    """Parse an LLM response into an Action. Never raises.

    On strict success: bump ``json_ok``, reset ``consecutive_fail`` for ``agent``.
    On repaired success: bump ``json_repaired``, reset ``consecutive_fail``.
    On total failure: bump ``json_fallback_rest`` + ``consecutive_fail`` and
    return a safe ``rest`` action.

    Inputs above ``MAX_PARSE_BYTES`` (UTF-8 bytes) are short-circuited
    straight to the fallback so the tick loop cannot stall on a
    pathological response.
    """
    if len(raw.encode("utf-8", errors="replace")) > MAX_PARSE_BYTES:
        metrics.bump("json_fallback_rest")
        metrics.bump("consecutive_fail", agent=agent)
        return _rest_action()

    # Strict pass first; bumps json_ok on success.
    try:
        strict = json.loads(raw)
    except json.JSONDecodeError:
        strict = None
    if isinstance(strict, dict):
        try:
            action = Action.model_validate(strict)
            if has_meta_leak(action.thought) or has_meta_leak(action.artifact or ""):
                # Different counter from json_fallback_rest so the
                # watchdog can distinguish parse failures from
                # immersion breaks.
                metrics.bump("meta_leak_block", agent=agent)
                return _rest_action()
            metrics.bump("json_ok")
            metrics.reset("consecutive_fail", agent=agent)
            return action
        except ValidationError:
            pass

    # Repair pass: shared helper handles the strict-or-repair retry; we
    # validate the repaired object here and bump json_repaired only on
    # success so the metric stays tied to "needed repair".
    repaired = safe_json_loads(raw)
    if isinstance(repaired, dict):
        try:
            action = Action.model_validate(repaired)
        except ValidationError:
            action = None
        if action is not None:
            if has_meta_leak(action.thought) or has_meta_leak(action.artifact or ""):
                metrics.bump("meta_leak_block", agent=agent)
                return _rest_action()
            metrics.bump("json_repaired")
            metrics.reset("consecutive_fail", agent=agent)
            return action

    metrics.bump("json_fallback_rest")
    metrics.bump("consecutive_fail", agent=agent)
    return _rest_action()


@dataclass(frozen=True, slots=True)
class WorldContext:
    """Snapshot of world state passed into ``Agent.think``.

    Phase 3a fills ``recent_episodic`` (last-7-days events the agent
    witnessed) and ``lore_excerpt`` (top-k FTS5 hits keyed off the
    current scene topic). ``microverse.memory.build_context`` is the
    canonical assembler.
    """

    season: str = "spring"
    weather: str = "clear"
    peers_today: tuple[str, ...] = ()
    recent_episodic: tuple[str, ...] = ()
    lore_excerpt: tuple[str, ...] = ()


class Agent(abc.ABC):
    """Base agent interface. Subclasses define a persona prompt and call
    ``microverse.llm.ollama_client.chat`` inside ``think()``.
    """

    role: str
    persona_template: str
    sampling: dict[str, float | int]

    def __init__(self, name: str, *, soul_tokens: int = 100) -> None:
        self.name = name
        self.soul_tokens = soul_tokens

    def tempo(self) -> float:
        """Seconds to sleep after this agent's tick. Override per role."""
        return 30.0

    @abc.abstractmethod
    def think(self, world: WorldContext) -> Action:  # pragma: no cover
        ...

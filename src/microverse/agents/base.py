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
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from json_repair import repair_json
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from microverse.config import MAX_PARSE_BYTES

if TYPE_CHECKING:
    from microverse.ops.metrics import Metrics


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

    # 1. Strict
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        try:
            action = Action.model_validate(data)
            metrics.bump("json_ok")
            metrics.reset("consecutive_fail", agent=agent)
            return action
        except ValidationError:
            pass

    # 2. Repair
    try:
        repaired = repair_json(raw, return_objects=False)
        if not repaired or repaired in ("{}", "[]", '""'):
            raise ValueError("repair produced empty / non-action shape")
        repaired_data = json.loads(repaired)
        if not isinstance(repaired_data, dict):
            raise ValueError("repair did not produce an object")
        action = Action.model_validate(repaired_data)
        metrics.bump("json_repaired")
        metrics.reset("consecutive_fail", agent=agent)
        return action
    except (json.JSONDecodeError, ValidationError, ValueError):
        pass

    # 3. Fallback
    metrics.bump("json_fallback_rest")
    metrics.bump("consecutive_fail", agent=agent)
    return _rest_action()


@dataclass(frozen=True, slots=True)
class WorldContext:
    """Snapshot of world state passed into ``Agent.think``.

    Phase 1 is intentionally minimal — Phase 3a fills this out.
    """

    season: str = "spring"
    weather: str = "clear"
    peers_today: tuple[str, ...] = ()
    recent_episodic: tuple[str, ...] = ()


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

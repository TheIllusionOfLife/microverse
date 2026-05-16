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
from microverse.config import MAX_PARSE_BYTES, MIN_FRAGMENT_CHARS
from microverse.world.workshop import WIPView

if TYPE_CHECKING:
    from microverse.ops.metrics import Metrics
    from microverse.world.workshop import WorkshopProjection


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
    # v0.2 (ADR 0003): a contribution to a shared workshop WIP. The
    # WIP name rides in ``Action.contribute_to``; the fragment text
    # rides in ``Action.artifact``. ``target`` stays None (target is
    # for peer addressing).
    CONTRIBUTE = "contribute"


class Action(BaseModel):
    """One agent's decision for a single tick."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    thought: str = Field(default="", max_length=500)
    action: ActionKind
    target: str | None = Field(default=None, max_length=100)
    artifact: str | None = Field(default=None, max_length=8000)
    # v0.2 (ADR 0003): name of the workshop WIP this contribution
    # targets. Validated by ``parse_action`` against the configured
    # set; only meaningful when ``action == ActionKind.CONTRIBUTE``.
    # Defaults to None so v0.1.1 callers / fixtures round-trip.
    contribute_to: str | None = Field(default=None, max_length=100)


def _rest_action() -> Action:
    return Action(thought="", action=ActionKind.REST, target=None, artifact=None)


def _validate_contribute(
    action: Action,
    *,
    metrics: Metrics,
    agent: str,
    workshop: WorkshopProjection | None = None,
) -> tuple[Action, bool]:
    """ADR 0003 + 0004: validate a CONTRIBUTE action against the
    workshop schema and the v0.3 structural fixes.

    Folds (returns ``(_rest_action(), True)``) when any of:
    - ``contribute_to`` is not a configured WIP, OR artifact is empty
      after strip (existing v0.2 behaviour;
      ``contribute_invalid_target``).
    - artifact length after strip is below ``MIN_FRAGMENT_CHARS``
      (ADR 0004 Decision 2; ``contribute_too_short``).
    - ``workshop`` is provided AND the target WIP is in ``complete``
      phase (ADR 0004 Decision 3; ``contribute_to_complete_wip``).

    Also folds non-contribute actions that carry a stray
    ``contribute_to`` (workshop affordance reachable only through
    CONTRIBUTE).

    Returns ``(action, folded)``. ``folded`` is True iff the original
    action was rejected and replaced with a safe rest. The caller uses
    the bool to decide whether to credit ``json_ok`` / ``json_repaired``
    — a folded action should not credit either, while a legitimate
    ``{"action":"rest","thought":""}`` should.
    """
    # Lazy import so agents/base.py stays cycle-free.
    from microverse.world.workshop import CONFIGURED_WIPS

    if action.action == ActionKind.CONTRIBUTE:
        artifact = (action.artifact or "").strip()
        if action.contribute_to not in CONFIGURED_WIPS or not artifact:
            metrics.bump("contribute_invalid_target", agent=agent)
            return _rest_action(), True
        if len(artifact) < MIN_FRAGMENT_CHARS:
            metrics.bump("contribute_too_short", agent=agent)
            return _rest_action(), True
        if workshop is not None and workshop.is_complete(action.contribute_to or ""):
            metrics.bump("contribute_to_complete_wip", agent=agent)
            return _rest_action(), True
        return action, False

    if action.contribute_to is not None:
        # Stray name on a non-contribute verb. Defence-in-depth: the
        # workshop affordance is only reachable through CONTRIBUTE.
        metrics.bump("contribute_invalid_target", agent=agent)
        return _rest_action(), True
    return action, False


def parse_action(
    raw: str,
    *,
    metrics: Metrics,
    agent: str,
    workshop: WorkshopProjection | None = None,
) -> Action:
    """Parse an LLM response into an Action. Never raises.

    On strict success: bump ``json_ok``, reset ``consecutive_fail`` for ``agent``.
    On repaired success: bump ``json_repaired``, reset ``consecutive_fail``.
    On total failure: bump ``json_fallback_rest`` + ``consecutive_fail`` and
    return a safe ``rest`` action.

    Inputs above ``MAX_PARSE_BYTES`` (UTF-8 bytes) are short-circuited
    straight to the fallback so the tick loop cannot stall on a
    pathological response.

    v0.3 (ADR 0004 Decision 3): when ``workshop`` is provided,
    contribute actions targeting a complete WIP are hard-folded to rest
    with ``contribute_to_complete_wip``. ``workshop=None`` preserves
    v0.2 back-compat.
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
            action, folded = _validate_contribute(
                action, metrics=metrics, agent=agent, workshop=workshop
            )
            if folded:
                # _validate_contribute folded — don't credit json_ok
                # and don't reset consecutive_fail on a fold.
                return action
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
            repaired_action: Action | None = Action.model_validate(repaired)
        except ValidationError:
            repaired_action = None
        if repaired_action is not None:
            if has_meta_leak(repaired_action.thought) or has_meta_leak(
                repaired_action.artifact or ""
            ):
                metrics.bump("meta_leak_block", agent=agent)
                return _rest_action()
            validated, folded = _validate_contribute(
                repaired_action, metrics=metrics, agent=agent, workshop=workshop
            )
            if folded:
                # Workshop-route fold — don't credit json_repaired.
                return validated
            metrics.bump("json_repaired")
            metrics.reset("consecutive_fail", agent=agent)
            return validated

    metrics.bump("json_fallback_rest")
    metrics.bump("consecutive_fail", agent=agent)
    return _rest_action()


@dataclass(frozen=True, slots=True)
class PeerSpeech:
    """A single speak event another agent addressed to *this* agent
    since *this* agent's last own-tick. Carried by
    ``WorldContext.peer_inbox`` so the receiver can respond on the
    very next tick — the only multi-tick continuity we surface.

    Both fields are required: the value of the inbox is precisely
    that ``who said it`` and ``what they said`` are both present.
    The utterance is truncated at a word boundary to ≤80 chars by
    the slice-2 builder; cross-agent narrative laundering is
    bounded by dropping any utterance containing the receiver's
    name as a whole word.
    """

    speaker: str
    utterance: str


@dataclass(frozen=True, slots=True)
class WorldContext:
    """Snapshot of world state passed into ``Agent.think``.

    Path-3 stateless-tick contract: this is *all* the agent sees on
    a tick. There is no ``recent_episodic`` of self-or-peer actions —
    seven prior layers each fixed one autobiographical channel and
    the LLM rerouted to the next. Path-3 removes the substrate.

    Fields:
      * ``season``: calendar position. Currently a static ``"spring"``
        stub — v0.1 does not model a calendar. Reserved for v0.2.
      * ``weather``: most recent ``weather.*`` event kind (or
        ``"clear"`` if none yet), populated per-tick by
        ``run._derive_weather``. Defaults to ``"clear"`` for tests
        that construct ``WorldContext`` directly.
      * ``peers_today``: distinct names of other agents present
        (registered roster + peers who have addressed self). Names
        only, never actions.
      * ``peer_inbox``: speaks-to-self by other agents since the
        receiver's last own-tick. One-shot, drained on next own-tick
        by the slice-3 wiring in ``run.py``.
      * ``world_events``: factual world events
        (``[world] weather.storm``, etc.) since the receiver's last
        own-tick. NEVER any agent action.
      * ``lore_excerpt``: FTS5 hits keyed off season+weather (slice
        6 enforces no agent name in the topic seed).
      * ``engagement_hint`` / ``required_target``: Layer-G exogenous
        nudge when the agent has gone too long without a targeted
        speak. Empty/None when the gate is not firing.
    """

    season: str = "spring"
    weather: str = "clear"
    peers_today: tuple[str, ...] = ()
    peer_inbox: tuple[PeerSpeech, ...] = ()
    world_events: tuple[str, ...] = ()
    lore_excerpt: tuple[str, ...] = ()
    engagement_hint: str = ""
    required_target: str | None = None
    # v0.2 (ADR 0003): per-receiver workshop view. Each ``WIPView``
    # is pre-rendered with the receiver's own fragments redacted to
    # anonymous markers and the receiver's own name masked in the
    # contributors string. Empty tuple means the workshop is absent
    # (v0.1.1 callers or fresh data dir). Defaults preserve
    # backward-compat with every existing WorldContext() fixture.
    workshop_view: tuple[WIPView, ...] = ()


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
        # v0.3 (ADR 0004 Decision 3): the runtime sets this after the
        # WorkshopProjection is constructed so the agent's parse_action
        # can hard-fold contributes targeting a complete WIP. Defaults
        # to None for tests that construct an agent without a workshop.
        self._workshop: WorkshopProjection | None = None

    def attach_workshop(self, workshop: WorkshopProjection) -> None:
        """Bind a WorkshopProjection for the v0.3 validator hard-fold."""
        self._workshop = workshop

    def tempo(self) -> float:
        """Seconds to sleep after this agent's tick. Override per role."""
        return 30.0

    @abc.abstractmethod
    def think(self, world: WorldContext) -> Action:  # pragma: no cover
        ...

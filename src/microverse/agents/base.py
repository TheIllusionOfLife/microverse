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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from microverse._text import safe_json_loads
from microverse.config import MAX_PARSE_BYTES, MIN_FRAGMENT_CHARS
from microverse.world.workshop import WIPView

if TYPE_CHECKING:
    from microverse.ops.metrics import Metrics
    from microverse.world.economy import EnergyLedger
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
    # v0.4 (ADR 0005 Decision 3): scene linkage. When a contribute is
    # produced inside a scene micro-loop, scene_id matches the
    # corresponding scene.open event's id, and turn_index is 1/2/3.
    # Plain contributes outside a scene leave both as None. These ride
    # in the episodic payload so the WorkshopProjection's replay sees
    # the same scene grouping as the live tick.
    scene_id: str | None = Field(default=None, max_length=64)
    turn_index: int | None = Field(default=None, ge=1, le=3)


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
    - ``workshop`` is provided AND the target WIP is in ``complete``
      phase (ADR 0004 Decision 3; ``contribute_to_complete_wip``).
    - artifact length after strip is below ``MIN_FRAGMENT_CHARS``
      (ADR 0004 Decision 2; ``contribute_too_short``).

    Order matters: the complete-WIP check fires before the length
    check so a short fragment aimed at a locked WIP is attributed to
    the structural pathology (gate 6), not the length pathology.

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
        # ADR 0004 gate 6 (`contribute_to_complete_wip < 1 %`) is the
        # primary v0.2 pathology we are closing — the 83 % black hole
        # where contributes fell into locked WIPs. When a fragment is
        # both targeting a complete WIP AND too short, attribute the
        # fold to the structural pathology, not the length pathology,
        # so gate 6 stays observable.
        if workshop is not None and workshop.is_complete(action.contribute_to or ""):
            metrics.bump("contribute_to_complete_wip", agent=agent)
            return _rest_action(), True
        if len(artifact) < MIN_FRAGMENT_CHARS:
            metrics.bump("contribute_too_short", agent=agent)
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
            # ADR 0006: scene_id / turn_index are stamped at runtime by
            # ``SceneRunner`` — never accepted from the LLM. Strip any
            # forged values here so a contribute outside a scene cannot
            # masquerade as part of one (which would pollute the
            # gate-8 scene grouping in spike_workshop_measure.py).
            if action.scene_id is not None or action.turn_index is not None:
                metrics.bump("scene_meta_forged", agent=agent)
                action = action.model_copy(update={"scene_id": None, "turn_index": None})
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
            # ADR 0006: see strict-pass note above. Strip forged scene
            # metadata from json-repaired actions too.
            if repaired_action.scene_id is not None or repaired_action.turn_index is not None:
                metrics.bump("scene_meta_forged", agent=agent)
                repaired_action = repaired_action.model_copy(
                    update={"scene_id": None, "turn_index": None}
                )
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
class SceneTurn:
    """One prior turn inside the current scene, surfaced to the next
    turn's persona as an explicit scene-scoped input.

    ADR 0005:194 carve-out: even when turn 3's author == turn 1's author,
    the agent sees their own turn-1 text VERBATIM here. This is NOT
    autobiographical replay (the Path-3 invariant still holds for the
    workshop view / lore / peer inbox) — it is explicit scene context,
    surfaced exactly once per scene, with the prior author named.
    """

    author: str
    text: str


@dataclass(frozen=True, slots=True)
class RelationFact:
    """One peer edge in an agent's relationship ledger, derived from the
    episodic log (ADR 0007 Phase 1, Pillar 1).

    All three fields are integer counts — never free text — so surfacing
    them into the persona cannot leak the agent's own fragment/artifact
    prose. ``peer`` is always a registered roster name (the projection
    whitelists against the known roster), so a hallucinated speak target
    can never become prompt text despite ``autoescape=False``.
    """

    peer: str
    addressed_you: int  # times ``peer`` spoke TO this agent
    you_addressed: int  # times this agent spoke TO ``peer``
    co_authored: int  # committed scenes both contributed to


@dataclass(frozen=True, slots=True)
class SelfView:
    """The agent's persistent self-record, fed back into the persona.

    ADR 0007 Phase 1 sanctions this as the EXPLICIT Path-3 carve-out
    (mirrors ADR 0006's turn-3 scene-context carve-out): structured
    identity state only. It carries static ``traits``, a derived
    ``relationships`` ledger (counts + roster names), and a periodically
    summarized ``beliefs`` line — never the agent's own past
    fragment/artifact/thought prose. The workshop redactor still hides
    an agent's own fragments from its WIP excerpt; this is a separate,
    narrow channel.
    """

    traits: tuple[str, ...] = ()
    relationships: tuple[RelationFact, ...] = ()
    beliefs: str = ""


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
    # v0.4 (ADR 0005 D3): explicit scene-scoped input. Populated by
    # SceneRunner before turn-2 and turn-3 think() calls. Empty for
    # single-tick (non-scene) actions. Path-3 carve-out: even when
    # turn-3 author == turn-1 author, this tuple SHOWS that author's
    # own turn-1 fragment — by explicit design, not via autobiographical
    # replay. See ADR 0005:189-196.
    scene_context: tuple[SceneTurn, ...] = ()
    # v0.4 (ADR 0005 D3): the WIP the current scene is built around.
    # The persona renders it as an instruction so the LLM contributes
    # to the SAME WIP all 3 turns; the scene runner aborts otherwise.
    # Empty when not in scene mode. Set by SceneRunner via the run-
    # loop's world_factory closure.
    scene_wip_name: str = ""
    # v0.4 (Phase D): novelty hint when an agent's top-recent verb
    # share crosses the diversity threshold. Persona renders this as
    # one line; empty string means no hint is active. The hint is a
    # NUDGE (the LLM may ignore it); the post-action diversifier in
    # the agent will substitute the verb at a fixed rate if the LLM
    # repeats the same dominant verb.
    novelty_hint: str = ""
    # v0.4 (Phase D): the *structured* form of the same nudge. When
    # the hint fires, these carry the dominant and suggested verbs
    # directly so the agent's _maybe_diversify helper does not have
    # to parse the human-readable hint string back (Gemini PR review
    # on #38 flagged the string-parse round-trip as brittle).
    novelty_dominant_verb: str = ""
    novelty_suggested_verb: str = ""
    # ADR 0008 spike: one-line scarcity signal, mirroring ``novelty_hint``.
    # Empty when the action economy is off (so flag-off prompts are
    # byte-identical) or when reserves are ample. When the agent's energy is
    # low it names the role's cheap specialty and the verbs that are currently
    # out of reach, so the model can choose affordably ON ITS OWN — the
    # perception channel without which the economy could only ever FORCE
    # diversity at the executor, never move the model's chosen verbs.
    energy_hint: str = ""
    # v1.1 (ADR 0007 Phase 1, Pillar 1): the agent's persistent
    # self-record — static traits, a derived relationship ledger, and a
    # periodically summarized beliefs line. The EXPLICIT Path-3 carve-out
    # (structured identity only, never own fragments). Populated by
    # ``run._build_self_view`` and carried through ``build_context`` via
    # ``dataclasses.replace``. Defaults to an empty ``SelfView`` so every
    # existing ``WorldContext()`` fixture stays valid.
    self_view: SelfView = field(default_factory=SelfView)


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
        # ADR 0008 spike: the run loop attaches a shared EnergyLedger ONLY in
        # economy modes that include substitution. None => the economy lever
        # is a no-op in think() (flag off, throttle-only mode, or a test that
        # does not attach), so think() reproduces pre-spike behavior exactly.
        self._energy: EnergyLedger | None = None
        # ADR 0008 spike telemetry the run loop stamps onto the committed
        # payload (Gate 9): ``parsed_verb`` (the rawest model choice, before any
        # lever — the CHOSEN stream) and ``economy_substituted`` (bool: did the
        # economy lever rewrite the verb — the economy-ONLY substitution signal,
        # so Gate 9 need not conflate it with diversity/engagement/fold
        # overrides). Empty until think() runs.
        self._verb_trace: dict[str, object] = {}

    def attach_workshop(self, workshop: WorkshopProjection) -> None:
        """Bind a WorkshopProjection for the v0.3 validator hard-fold."""
        self._workshop = workshop

    def attach_energy(self, ledger: EnergyLedger) -> None:
        """Bind the shared EnergyLedger so ``think()`` applies the economy
        substitution lever (ADR 0008 spike). Only called by the run loop in
        substitution-enabled economy modes."""
        self._energy = ledger

    def tempo(self) -> float:
        """Seconds to sleep after this agent's tick. Override per role."""
        return 30.0

    @abc.abstractmethod
    def think(self, world: WorldContext) -> Action:  # pragma: no cover
        ...


# Phase D Step 2 (shared between Artisan + Scholar — Gemini PR review
# on #38 asked for the consolidation). The probability and replacement
# thought are still per-agent (Artisan and Scholar have slightly
# different neutral lines) but the decision logic now lives here.
def apply_diversity_lever(
    action: Action,
    world: WorldContext,
    *,
    rng: Any,
    metrics: Metrics,
    agent_name: str,
    replacement_thought: str,
    probability: float,
) -> Action:
    """Substitute the action verb when the LLM ignored the novelty
    hint. Returns the action unchanged when no hint is active, when
    the parsed action is a fallback REST, when the hint suggests
    CONTRIBUTE (we cannot fabricate WIP + fragment), or when the LLM
    already diversified.

    Reads structured fields ``WorldContext.novelty_dominant_verb`` and
    ``novelty_suggested_verb`` — no hint-string parsing. Returns the
    original action unchanged when those fields are empty (legacy
    callers that set only ``novelty_hint`` get no-op behaviour, which
    is the safe default).
    """
    if not world.novelty_dominant_verb or not world.novelty_suggested_verb:
        return action
    is_fallback_rest = action.action == ActionKind.REST and not action.thought
    if is_fallback_rest:
        return action
    try:
        target_verb = ActionKind(world.novelty_suggested_verb)
    except ValueError:
        return action
    if action.action.value != world.novelty_dominant_verb:
        return action
    if target_verb == ActionKind.CONTRIBUTE:
        return action
    if rng.random() >= probability:
        return action
    metrics.bump("diversity_lever_substituted", agent=agent_name)
    new_target: str | None = None
    if target_verb == ActionKind.SPEAK and world.peers_today:
        new_target = rng.choice(world.peers_today)
    return action.model_copy(
        update={
            "thought": replacement_thought,
            "action": target_verb,
            "target": new_target,
            "artifact": None,
            "contribute_to": None,
        }
    )


# Action-economy lever (re-diagnosis spike — ADR 0008). Sibling of
# ``apply_diversity_lever``: a HARD substitution (not a probabilistic nudge)
# when the LLM picks a verb the agent cannot pay for. The substitution target
# is the cheapest affordable PRODUCTIVE verb (never ``contribute`` — cannot
# fabricate a WIP + fragment; ``rest`` only as a last resort so the lever
# drives specialization rather than collapsing onto rest).
def apply_economy_lever(
    action: Action,
    world: WorldContext,
    *,
    ledger: EnergyLedger,
    role: str,
    agent_name: str,
    rng: Any,
    metrics: Metrics,
    replacement_thought: str,
) -> Action:
    """Substitute an unaffordable verb for an affordable one. Returns the
    action unchanged when:

    - the call is inside a scene turn (``world.scene_wip_name`` set) — the
      forced ``contribute`` must survive or ``SceneRunner`` aborts the scene;
    - the parsed action is a fallback REST (empty thought) — the
      ``json_fallback_rest`` watchdog signal must propagate;
    - the chosen verb is already affordable;
    - or the substitution would be a no-op (already the target verb).

    Bumps ``economy_verb_substituted{agent}`` only when it changes the verb.
    """
    if world.scene_wip_name:
        return action
    is_fallback_rest = action.action == ActionKind.REST and not action.thought
    if is_fallback_rest:
        return action
    if ledger.can_afford(agent_name, role, action.action.value):
        return action
    target_verb = ActionKind(ledger.resolve_executed_verb(agent_name, role, action.action.value))
    if target_verb == action.action:
        return action
    metrics.bump("economy_verb_substituted", agent=agent_name)
    new_target: str | None = None
    if target_verb == ActionKind.SPEAK and world.peers_today:
        new_target = rng.choice(world.peers_today)
    return action.model_copy(
        update={
            "thought": replacement_thought,
            "action": target_verb,
            "target": new_target,
            "artifact": None,
            "contribute_to": None,
        }
    )

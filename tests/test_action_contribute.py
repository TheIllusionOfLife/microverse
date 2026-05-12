"""Phase 3 — ``Action.contribute`` schema and parse_action validation.

ADR 0003 Decision (additive contract):
  - ``ActionKind.CONTRIBUTE = "contribute"``.
  - ``Action.contribute_to: str | None = None`` — names a configured
    WIP from ``microverse.world.workshop.CONFIGURED_WIPS``.
  - ``parse_action`` validates: when action is contribute, the
    ``contribute_to`` MUST be a configured WIP name AND
    ``artifact`` (the fragment text) MUST be non-empty. Anything else
    folds to ``rest`` per the "never raises" invariant.
  - When action is NOT contribute, ``contribute_to`` MUST be None —
    a stray name on speak/craft/etc. is malformed and folds to rest
    so the workshop projection cannot be reached through the wrong
    action verb.

Defaults stay backward-compatible: existing Actions without
``contribute_to`` round-trip identically. Existing tests
(``test_action_parse.py``) keep passing.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from microverse.agents.base import Action, ActionKind, parse_action
from microverse.ops.metrics import Metrics
from microverse.world.workshop import CONFIGURED_WIPS


def test_action_kind_has_contribute() -> None:
    assert ActionKind.CONTRIBUTE.value == "contribute"


def test_action_default_contribute_to_is_none() -> None:
    action = Action(action=ActionKind.REST)
    assert action.contribute_to is None


def test_action_accepts_contribute_with_valid_target() -> None:
    wip = CONFIGURED_WIPS[0]
    action = Action(
        thought="I add a stitch",
        action=ActionKind.CONTRIBUTE,
        target=None,
        contribute_to=wip,
        artifact="a blue stitch across the rough warp",
    )
    assert action.action == ActionKind.CONTRIBUTE
    assert action.contribute_to == wip
    assert action.artifact == "a blue stitch across the rough warp"


def test_action_rejects_unknown_field_via_extra_forbid() -> None:
    """Action's model_config sets extra='forbid' — adding a new
    schema field must not relax the strict-JSON contract for
    unrelated keys.
    """
    with pytest.raises(ValidationError):
        Action.model_validate(
            {
                "action": "rest",
                "unknown_field": "x",
            }
        )


def _raw(action: str, *, contribute_to: str | None = None,
         target: str | None = None, artifact: str | None = None,
         thought: str = "x") -> str:
    payload: dict[str, object] = {"thought": thought, "action": action}
    payload["target"] = target
    payload["artifact"] = artifact
    if contribute_to is not None:
        payload["contribute_to"] = contribute_to
    return json.dumps(payload)


def test_parse_action_accepts_valid_contribute() -> None:
    metrics = Metrics(":memory:")
    wip = CONFIGURED_WIPS[0]
    raw = _raw(
        "contribute",
        contribute_to=wip,
        artifact="a blue stitch across the warp",
    )
    out = parse_action(raw, metrics=metrics, agent="Aki")
    assert out.action == ActionKind.CONTRIBUTE
    assert out.contribute_to == wip
    assert out.artifact == "a blue stitch across the warp"
    assert metrics.get("json_ok") == 1
    assert metrics.get("json_fallback_rest") == 0


def test_parse_action_folds_contribute_to_unknown_wip_to_rest() -> None:
    metrics = Metrics(":memory:")
    raw = _raw(
        "contribute",
        contribute_to="workshop.does-not-exist",
        artifact="hello",
    )
    out = parse_action(raw, metrics=metrics, agent="Aki")
    assert out.action == ActionKind.REST
    assert out.contribute_to is None
    # The fold uses the "invalid contribute" counter, NOT
    # json_fallback_rest, so operators can distinguish parse failures
    # from workshop-route failures.
    assert metrics.get("contribute_invalid_target", agent="Aki") == 1


def test_parse_action_folds_contribute_without_target_to_rest() -> None:
    metrics = Metrics(":memory:")
    raw = _raw("contribute", contribute_to=None, artifact="hello")
    out = parse_action(raw, metrics=metrics, agent="Aki")
    assert out.action == ActionKind.REST
    assert metrics.get("contribute_invalid_target", agent="Aki") == 1


def test_parse_action_folds_contribute_with_empty_artifact_to_rest() -> None:
    """A contribute with empty fragment text is the workshop analogue
    of the Layer-F.2 empty-craft trap: ``contribute_to`` populated but
    no fragment means the agent is gesturing at a WIP without
    actually adding anything to it. Fold so the workshop projection
    cannot accumulate phantom contributions.
    """
    metrics = Metrics(":memory:")
    wip = CONFIGURED_WIPS[0]
    raw = _raw("contribute", contribute_to=wip, artifact=None)
    out = parse_action(raw, metrics=metrics, agent="Aki")
    assert out.action == ActionKind.REST
    assert metrics.get("contribute_invalid_target", agent="Aki") == 1


def test_parse_action_folds_stray_contribute_to_on_other_actions() -> None:
    """A speak / craft / study / rest / travel action with a non-None
    ``contribute_to`` is malformed: the workshop affordance is only
    reachable through ``contribute``. Fold to rest as defence-in-depth
    against an LLM accidentally emitting the field on the wrong verb.
    """
    metrics = Metrics(":memory:")
    wip = CONFIGURED_WIPS[0]
    for action in ("speak", "craft", "study", "rest", "travel"):
        raw = _raw(
            action,
            contribute_to=wip,
            target=None,
            artifact="x" if action == "craft" else None,
        )
        out = parse_action(raw, metrics=metrics, agent="Aki")
        assert out.action == ActionKind.REST, (
            f"{action!r} with stray contribute_to should fold to rest, "
            f"got {out.action!r}"
        )


def test_parse_action_contribute_round_trip_via_repair() -> None:
    """The json_repair pass also validates contribute. A trailing
    comma is the cheapest "repaired" shape.
    """
    metrics = Metrics(":memory:")
    wip = CONFIGURED_WIPS[0]
    raw = (
        '{"thought": "x", "action": "contribute", "target": null, '
        f'"contribute_to": "{wip}", "artifact": "a stitch",}}'  # trailing comma
    )
    out = parse_action(raw, metrics=metrics, agent="Aki")
    assert out.action == ActionKind.CONTRIBUTE
    assert out.contribute_to == wip


def test_parse_action_meta_leak_still_applies_to_contribute_artifact() -> None:
    """The fragment text passes through the same meta-leak filter as
    craft artifacts. A "model" reference in the fragment folds to rest.
    """
    metrics = Metrics(":memory:")
    wip = CONFIGURED_WIPS[0]
    raw = _raw(
        "contribute",
        contribute_to=wip,
        artifact="the model speaks of cherry blossoms",
    )
    out = parse_action(raw, metrics=metrics, agent="Aki")
    assert out.action == ActionKind.REST
    assert metrics.get("meta_leak_block", agent="Aki") == 1

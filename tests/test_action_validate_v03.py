"""v0.3 (ADR 0004 Decisions 2 + 3) — validator hard-folds.

Slice 2.1/2.2: contribute fragments shorter than MIN_FRAGMENT_CHARS
                hard-fold to rest with ``contribute_too_short``.
Slice 3.1/3.3: contribute to a complete WIP hard-folds to rest with
                ``contribute_to_complete_wip``; ``workshop=None``
                back-compat skips the lookup.
"""

from __future__ import annotations

import json

from microverse.agents.base import ActionKind, parse_action
from microverse.config import MIN_FRAGMENT_CHARS
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.world.workshop import CONFIGURED_WIPS, WorkshopProjection


def _make_contribute_json(*, wip: str, text: str) -> str:
    return json.dumps(
        {
            "thought": "I weave",
            "action": "contribute",
            "target": None,
            "artifact": text,
            "contribute_to": wip,
        }
    )


def test_min_fragment_chars_constant_is_120() -> None:
    """ADR 0004 Decision 2: the floor is 120 chars (~25 words). The
    composite acceptance gate (length AND repeat-4gram AND
    peer-reference) is the load-bearing guard, but this floor is the
    structural enforcement point.
    """
    assert MIN_FRAGMENT_CHARS == 120


def test_contribute_below_min_chars_hard_folds(metrics: Metrics) -> None:
    """A contribute with artifact length < MIN_FRAGMENT_CHARS folds to
    rest, bumps ``contribute_too_short``, and does NOT credit
    ``json_ok``.
    """
    short_text = "a small wooden box"  # 18 chars, far below 120
    raw = _make_contribute_json(wip=CONFIGURED_WIPS[0], text=short_text)
    action = parse_action(raw, metrics=metrics, agent="aki")
    assert action.action == ActionKind.REST
    assert metrics.get("contribute_too_short", agent="aki") == 1
    assert metrics.get("json_ok") == 0
    assert metrics.get("json_repaired") == 0


def test_contribute_at_min_chars_validates(metrics: Metrics) -> None:
    """A contribute with artifact length >= MIN_FRAGMENT_CHARS passes
    the floor and is accepted (assuming no workshop lookup folds it).
    """
    long_text = "x" * MIN_FRAGMENT_CHARS
    raw = _make_contribute_json(wip=CONFIGURED_WIPS[0], text=long_text)
    action = parse_action(raw, metrics=metrics, agent="aki")
    assert action.action == ActionKind.CONTRIBUTE
    assert action.contribute_to == CONFIGURED_WIPS[0]
    assert metrics.get("contribute_too_short", agent="aki") == 0
    assert metrics.get("json_ok") == 1


def test_contribute_after_strip_is_short_folds(metrics: Metrics) -> None:
    """Padding the artifact with whitespace must not bypass the floor;
    the check happens on ``.strip()``.
    """
    raw = _make_contribute_json(wip=CONFIGURED_WIPS[0], text="  short  " + " " * 200)
    action = parse_action(raw, metrics=metrics, agent="aki")
    assert action.action == ActionKind.REST
    assert metrics.get("contribute_too_short", agent="aki") == 1


def test_contribute_to_complete_wip_hard_folds(tmp_path, metrics: Metrics) -> None:
    """ADR 0004 Decision 3: when the named WIP is in ``complete`` phase
    (between completion and recycle), a contribute targeting it folds
    to rest with ``contribute_to_complete_wip``. Persona-only marking
    would leave the gate-4 invariant unenforceable; this is the load-
    bearing structural enforcement.
    """
    from microverse.world.workshop import COMPLETE_FRAGMENT_FLOOR

    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        # Drive the target WIP to complete.
        for i in range(COMPLETE_FRAGMENT_FLOOR):
            ep.append(
                actor=("Aki" if i % 2 == 0 else "Bo"),
                action="contribute",
                target=CONFIGURED_WIPS[0],
                payload={"fragment": f"fragment-{i}-with-many-letters-aaaaaaaaaaaaaa"},
                ts=float(i),
            )
        proj = WorkshopProjection(ep)
        assert proj.is_complete(CONFIGURED_WIPS[0])

        long_text = (
            "this is a perfectly adequate fragment that exceeds the minimum "
            "length floor by a comfortable margin so the validator does not fold"
        )
        raw = _make_contribute_json(wip=CONFIGURED_WIPS[0], text=long_text)
        action = parse_action(raw, metrics=metrics, agent="aki", workshop=proj)
    assert action.action == ActionKind.REST
    assert metrics.get("contribute_to_complete_wip", agent="aki") == 1
    assert metrics.get("json_ok") == 0


def test_contribute_to_open_wip_with_workshop_param_validates(tmp_path, metrics: Metrics) -> None:
    """When the workshop is passed but the target WIP is NOT complete,
    the validator must let the contribute through. Defence-in-depth
    against accidentally folding every contribute under v0.3.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj = WorkshopProjection(ep)
        long_text = (
            "this is a perfectly adequate fragment that exceeds the minimum "
            "length floor by a comfortable margin so the validator does not fold"
        )
        raw = _make_contribute_json(wip=CONFIGURED_WIPS[0], text=long_text)
        action = parse_action(raw, metrics=metrics, agent="aki", workshop=proj)
    assert action.action == ActionKind.CONTRIBUTE
    assert metrics.get("contribute_to_complete_wip", agent="aki") == 0
    assert metrics.get("json_ok") == 1


def test_parse_action_back_compat_without_workshop(metrics: Metrics) -> None:
    """ADR 0004 Decision 3: existing callers passing no workshop= must
    keep working unchanged — the lookup is skipped.
    """
    long_text = "x" * MIN_FRAGMENT_CHARS
    raw = _make_contribute_json(wip=CONFIGURED_WIPS[0], text=long_text)
    action = parse_action(raw, metrics=metrics, agent="aki")  # no workshop kwarg
    assert action.action == ActionKind.CONTRIBUTE
    assert metrics.get("contribute_to_complete_wip", agent="aki") == 0

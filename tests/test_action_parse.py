"""Tests for microverse.agents.base.Action + parse_action.

Action is the strict Pydantic v2 model an agent emits each tick.
parse_action is the parse-with-retry-with-jsonrepair-with-fallback
pipeline that the tick loop uses to never crash on malformed JSON.
"""

from __future__ import annotations

import pytest

from microverse.agents.base import ActionKind, parse_action
from microverse.ops.metrics import Metrics


def test_action_strict_valid_json():
    payload = (
        '{"thought": "I will craft a lamp.", "action": "craft", "target": null, "artifact": "lamp"}'
    )
    metrics = Metrics(":memory:")
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.CRAFT
    assert a.thought == "I will craft a lamp."
    assert a.target is None
    assert a.artifact == "lamp"
    assert metrics.get("json_ok") == 1
    assert metrics.get("json_repaired") == 0
    assert metrics.get("json_fallback_rest") == 0


def test_action_repaired_when_trailing_comma():
    """json-repair handles common LLM mishaps like trailing commas."""
    payload = '{"thought": "rest", "action": "rest", "target": null, "artifact": null,}'
    metrics = Metrics(":memory:")
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_repaired") == 1
    assert metrics.get("json_ok") == 0


def test_action_fallback_rest_on_garbage():
    metrics = Metrics(":memory:")
    a = parse_action("not json at all", metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert a.target is None
    assert a.artifact is None
    assert metrics.get("json_fallback_rest") == 1
    # consecutive_fail bumps so the watchdog can pause this agent later.
    assert metrics.get("consecutive_fail", agent="aki") == 1


def test_action_kind_unknown_falls_back_to_rest():
    payload = '{"thought": "x", "action": "fly_to_moon", "target": null, "artifact": null}'
    metrics = Metrics(":memory:")
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1


def test_action_thought_too_long_falls_back():
    """thought capped to keep prompt budgets honest."""
    long_thought = "x" * 5000
    payload = f'{{"thought": "{long_thought}", "action": "rest", "target": null, "artifact": null}}'
    metrics = Metrics(":memory:")
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1


def test_consecutive_fail_resets_on_success():
    metrics = Metrics(":memory:")
    parse_action("garbage", metrics=metrics, agent="aki")
    parse_action("more garbage", metrics=metrics, agent="aki")
    assert metrics.get("consecutive_fail", agent="aki") == 2

    good = '{"thought": "ok", "action": "rest", "target": null, "artifact": null}'
    parse_action(good, metrics=metrics, agent="aki")
    assert metrics.get("consecutive_fail", agent="aki") == 0


def test_action_extra_fields_rejected_then_repaired_or_rest():
    """Strict mode rejects extra fields; the repair path may strip them
    (json-repair keeps them, so this falls back). Either way, no crash."""
    payload = '{"thought": "x", "action": "rest", "target": null, "artifact": null, "extra": 1}'
    metrics = Metrics(":memory:")
    a = parse_action(payload, metrics=metrics, agent="aki")
    # Whether we land in json_ok with extra=ignored or in fallback,
    # the action result must be safe.
    assert a.action == ActionKind.REST


def test_action_kind_rest_speak_craft_study_travel_all_valid():
    metrics = Metrics(":memory:")
    for kind in ("speak", "craft", "study", "rest", "travel"):
        payload = f'{{"thought": "x", "action": "{kind}", "target": null, "artifact": null}}'
        a = parse_action(payload, metrics=metrics, agent="aki")
        assert a.action.value == kind


@pytest.mark.parametrize(
    "raw",
    [
        # Markdown-wrapped JSON (common with chat models)
        '```json\n{"thought": "x", "action": "rest", "target": null, "artifact": null}\n```',
        # Leading prose
        'Sure! Here you go: {"thought": "x", "action": "rest", "target": null, "artifact": null}',
    ],
)
def test_action_repair_handles_common_llm_wrappers(raw: str):
    metrics = Metrics(":memory:")
    a = parse_action(raw, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_repaired") == 1

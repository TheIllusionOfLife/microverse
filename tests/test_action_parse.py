"""Tests for microverse.agents.base.Action + parse_action.

Action is the strict Pydantic v2 model an agent emits each tick.
parse_action is the parse-with-retry-with-jsonrepair-with-fallback
pipeline that the tick loop uses to never crash on malformed JSON.
"""

from __future__ import annotations

import pytest

from microverse.agents.base import ActionKind, parse_action
from microverse.ops.metrics import Metrics


def test_action_strict_valid_json(metrics: Metrics):
    payload = (
        '{"thought": "I will craft a lamp.", "action": "craft", "target": null, "artifact": "lamp"}'
    )
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.CRAFT
    assert a.thought == "I will craft a lamp."
    assert a.target is None
    assert a.artifact == "lamp"
    assert metrics.get("json_ok") == 1
    assert metrics.get("json_repaired") == 0
    assert metrics.get("json_fallback_rest") == 0


def test_action_strict_rest_with_empty_thought_credits_json_ok(metrics: Metrics):
    """Regression for Codex review of PR #32: a legitimate
    ``{"action":"rest","thought":""}`` must credit ``json_ok`` and
    reset ``consecutive_fail``. An earlier fold-detection heuristic
    used ``action == REST and not action.thought`` which mis-classified
    this case as a workshop-route fallback fold.
    """
    metrics.bump("consecutive_fail", agent="aki")
    metrics.bump("consecutive_fail", agent="aki")
    payload = '{"thought": "", "action": "rest", "target": null, "artifact": null}'
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert a.thought == ""
    assert metrics.get("json_ok") == 1
    assert metrics.get("json_repaired") == 0
    assert metrics.get("json_fallback_rest") == 0
    assert metrics.get("contribute_invalid_target", agent="aki") == 0
    # consecutive_fail must be reset on a successful parse.
    assert metrics.get("consecutive_fail", agent="aki") == 0


def test_action_repaired_rest_with_empty_thought_credits_json_repaired(metrics: Metrics):
    """Companion to the strict-path test: when the JSON needs repair
    (trailing comma) AND has an empty thought + rest action, we must
    still credit ``json_repaired`` rather than silently folding.
    """
    payload = '{"thought": "", "action": "rest", "target": null, "artifact": null,}'
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert a.thought == ""
    assert metrics.get("json_repaired") == 1
    assert metrics.get("json_ok") == 0
    assert metrics.get("json_fallback_rest") == 0


def test_action_repaired_when_trailing_comma(metrics: Metrics):
    """json-repair handles common LLM mishaps like trailing commas."""
    payload = '{"thought": "rest", "action": "rest", "target": null, "artifact": null,}'
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_repaired") == 1
    assert metrics.get("json_ok") == 0


def test_action_fallback_rest_on_garbage(metrics: Metrics):
    a = parse_action("not json at all", metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert a.target is None
    assert a.artifact is None
    assert metrics.get("json_fallback_rest") == 1
    # consecutive_fail bumps so the watchdog can pause this agent later.
    assert metrics.get("consecutive_fail", agent="aki") == 1


def test_action_kind_unknown_falls_back_to_rest(metrics: Metrics):
    payload = '{"thought": "x", "action": "fly_to_moon", "target": null, "artifact": null}'
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1


def test_action_thought_too_long_falls_back(metrics: Metrics):
    """thought capped to keep prompt budgets honest."""
    long_thought = "x" * 5000
    payload = f'{{"thought": "{long_thought}", "action": "rest", "target": null, "artifact": null}}'
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1


def test_consecutive_fail_resets_on_success(metrics: Metrics):
    parse_action("garbage", metrics=metrics, agent="aki")
    parse_action("more garbage", metrics=metrics, agent="aki")
    assert metrics.get("consecutive_fail", agent="aki") == 2

    good = '{"thought": "ok", "action": "rest", "target": null, "artifact": null}'
    parse_action(good, metrics=metrics, agent="aki")
    assert metrics.get("consecutive_fail", agent="aki") == 0


def test_action_extra_fields_rejected_then_repaired_or_rest(metrics: Metrics):
    """Strict mode rejects extra fields; the repair path may strip them
    (json-repair keeps them, so this falls back). Either way, no crash."""
    payload = '{"thought": "x", "action": "rest", "target": null, "artifact": null, "extra": 1}'
    a = parse_action(payload, metrics=metrics, agent="aki")
    # Whether we land in json_ok with extra=ignored or in fallback,
    # the action result must be safe.
    assert a.action == ActionKind.REST


def test_action_kind_rest_speak_craft_study_travel_all_valid(metrics: Metrics):
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
def test_action_repair_handles_common_llm_wrappers(raw: str, metrics: Metrics):
    a = parse_action(raw, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_repaired") == 1


def test_meta_phrase_outside_the_simulation_blocks(metrics: Metrics):
    """Regression: 'outside the simulation' must trigger the meta-leak
    guard. Earlier version of the regex required 'this' between
    'outside' and the trigger noun and silently missed 'the'."""
    payload = (
        '{"thought": "I sense there is something outside the simulation", '
        '"action": "speak", "target": null, "artifact": null}'
    )
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("meta_leak_block", agent="aki") == 1


def test_meta_reference_in_thought_blocks_action(metrics: Metrics):
    """If the parsed action's thought contains a meta-token (AI, model,
    simulation, prompt, outside), fall back to rest and bump
    meta_leak_block instead of accepting the immersion break."""
    payload = (
        '{"thought": "I realize I am inside an AI simulation", '
        '"action": "speak", "target": null, "artifact": null}'
    )
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("meta_leak_block", agent="aki") == 1
    assert metrics.get("json_ok") == 0


def test_meta_reference_in_artifact_blocks_action(metrics: Metrics):
    payload = (
        '{"thought": "ok", "action": "craft", "target": null, '
        '"artifact": "a story about an LLM that wakes up"}'
    )
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("meta_leak_block", agent="aki") == 1


def test_clean_action_with_no_meta_reference_passes(metrics: Metrics):
    payload = (
        '{"thought": "I will craft a wooden bowl", "action": "craft", '
        '"target": null, "artifact": "a wooden bowl"}'
    )
    a = parse_action(payload, metrics=metrics, agent="aki")
    assert a.action == ActionKind.CRAFT
    assert metrics.get("meta_leak_block", agent="aki") == 0


def test_action_oversize_input_short_circuits_to_fallback(metrics: Metrics):
    """Inputs above MAX_PARSE_BYTES skip parse attempts entirely so a
    pathological 100KB blob can't stall the tick loop in O(N^2) repair."""
    from microverse.config import MAX_PARSE_BYTES

    huge = '{"thought": "' + ("x" * (MAX_PARSE_BYTES + 1024)) + '"}'
    a = parse_action(huge, metrics=metrics, agent="aki")
    assert a.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1
    assert metrics.get("json_ok") == 0
    assert metrics.get("json_repaired") == 0

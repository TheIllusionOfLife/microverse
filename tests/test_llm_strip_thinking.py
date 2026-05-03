"""Tests for microverse.llm.thinking.strip_thinking.

The recipe is from ~/.claude/skills/local-llm/SKILL.md:99-106 — split on
`</think>` and the GPT-OSS channel marker, then strip.
"""

from microverse.llm.thinking import strip_thinking


def test_strips_think_tags_and_keeps_remainder():
    assert strip_thinking("<think>internal monologue</think>hello world") == "hello world"


def test_no_thinking_returns_input_stripped():
    assert strip_thinking("hello world") == "hello world"


def test_only_thinking_returns_empty():
    assert strip_thinking("<think>I am thinking</think>") == ""


def test_channel_marker_variant():
    raw = "<|channel|>analysis<|message|>some analysis<|channel|>final<|message|>final answer"
    assert strip_thinking(raw) == "final answer"


def test_combined_think_and_channel():
    raw = "<think>analysis here</think><|channel|>final<|message|>real answer"
    assert strip_thinking(raw) == "real answer"


def test_whitespace_around_final_is_stripped():
    assert strip_thinking("<think>x</think>\n\n  the answer  \n") == "the answer"

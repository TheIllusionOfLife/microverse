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


def test_multiple_think_blocks_all_removed():
    raw = "<think>first</think>middle<think>second</think>final"
    assert strip_thinking(raw) == "final"


def test_case_insensitive_think_tags():
    assert strip_thinking("<THINK>x</THINK>foo") == "foo"
    assert strip_thinking("<Think>x</Think>bar") == "bar"


def test_unclosed_think_tag_strips_to_end():
    """An unclosed <think> opens the trace and never closes — treat the
    rest of the string as thinking and yield empty."""
    assert strip_thinking("<think>secret unclosed reasoning") == ""


def test_unclosed_think_followed_by_close_in_later_block():
    """Don't treat a stray closing tag as the boundary if there was an
    earlier unclosed opener — we can't know what's safe; strip everything
    up to the LAST closing tag."""
    raw = "<think>part1</think>visible<think>part2</think>final"
    assert strip_thinking(raw) == "final"


def test_think_tag_with_attributes_or_whitespace():
    """Some models emit `<think >` or `< think>` — be liberal."""
    assert strip_thinking("<think >x</think >hello") == "hello"


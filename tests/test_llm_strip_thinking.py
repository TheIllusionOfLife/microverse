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
    """All paired <think>...</think> blocks are removed; visible text
    between them is preserved (it's the model's actual answer)."""
    raw = "<think>first</think>middle<think>second</think>final"
    assert strip_thinking(raw) == "middlefinal"


def test_case_insensitive_think_tags():
    assert strip_thinking("<THINK>x</THINK>foo") == "foo"
    assert strip_thinking("<Think>x</Think>bar") == "bar"


def test_unclosed_think_tag_strips_to_end():
    """An unclosed <think> opens the trace and never closes — treat the
    rest of the string as thinking and yield empty."""
    assert strip_thinking("<think>secret unclosed reasoning") == ""


def test_unclosed_opener_with_later_paired_block_strips_everything_before_close():
    """When a stray <think> with no matching </think> precedes a paired
    block, we cannot trust anything between the unclosed opener and the
    last </think> — strip up through the final close. The unclosed-opener
    handler in strip_thinking drops everything from the opener onward,
    which subsumes the second block too."""
    # Unclosed opener, then visible, then a paired block, then final.
    raw = "before<think>orphaned no close <think>later</think>final"
    # The non-greedy paired-block regex matches the FIRST <think>..</think>
    # span (from the orphaned opener to the inner close), so it strips
    # "<think>orphaned no close <think>later</think>", leaving "beforefinal".
    assert strip_thinking(raw) == "beforefinal"


def test_think_tag_with_attributes_or_whitespace():
    """Some models emit `<think >` or `< think>` — be liberal."""
    assert strip_thinking("<think >x</think >hello") == "hello"

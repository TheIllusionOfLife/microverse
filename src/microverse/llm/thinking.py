"""Strip thinking-trace tokens from a model response.

Recipe lifted from ~/.claude/skills/local-llm/SKILL.md (Thinking Token
Leakage section). Used as defense-in-depth: callers should also pass
`think=False` to Ollama, but this guarantees no thinking tokens ever leak
to downstream code regardless of model or runtime quirks.
"""


def strip_thinking(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    if "<|channel|>final<|message|>" in text:
        text = text.split("<|channel|>final<|message|>", 1)[1]
    return text.strip()

"""Strip thinking-trace tokens from a model response.

Defense-in-depth helper: callers should also pass ``think=False`` to
Ollama, but this guarantees no thinking tokens ever leak to downstream
code regardless of model or runtime quirks.

Handles, with case-insensitive matching:
  - paired ``<think>...</think>`` blocks (any number)
  - unclosed ``<think>`` (everything after is treated as thinking)
  - whitespace inside the tags (``<think >``, ``< /think>``)
  - the GPT-OSS channel-marker form (``<|channel|>final<|message|>``)
"""

import re

# Match <think ...> or </think ...> with optional attributes / inner spaces.
_THINK_OPEN = re.compile(r"<\s*think\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"<\s*/\s*think\s*>", re.IGNORECASE)
# Paired block: open ... close (non-greedy).
_THINK_BLOCK = re.compile(r"<\s*think\b[^>]*>.*?<\s*/\s*think\s*>", re.IGNORECASE | re.DOTALL)


def has_thinking_markers(text: str) -> bool:
    """Return True if ``text`` contains any thinking-related marker.

    Used as the leak signal — independent of whether stripping actually
    removed anything, so unclosed tags still register.
    """
    if _THINK_OPEN.search(text):
        return True
    if _THINK_CLOSE.search(text):
        return True
    return "<|channel|>" in text or "<|message|>" in text


def strip_thinking(text: str) -> str:
    # 1. Remove all complete <think>...</think> blocks (any case, attrs).
    text = _THINK_BLOCK.sub("", text)
    # 2. Drop any remaining unclosed <think> and everything after it.
    open_match = _THINK_OPEN.search(text)
    if open_match:
        text = text[: open_match.start()]
    # 3. Drop any orphan </think> and everything before it (the model
    #    started a thought without an opening tag).
    close_match = list(_THINK_CLOSE.finditer(text))
    if close_match:
        text = text[close_match[-1].end() :]
    # 4. GPT-OSS channel-marker form: keep only the final-channel message.
    if "<|channel|>final<|message|>" in text:
        text = text.split("<|channel|>final<|message|>", 1)[1]
    elif "<|channel|>" in text or "<|message|>" in text:
        # Other channels (analysis, commentary, etc.) — drop everything
        # up through the last channel header to be safe.
        idx = max(text.rfind("<|channel|>"), text.rfind("<|message|>"))
        # If we can find a final <|message|> after the last header, keep
        # what comes after it; otherwise drop the channel scaffolding.
        last_msg = text.rfind("<|message|>")
        text = text[last_msg + len("<|message|>") :] if last_msg >= 0 else text[idx:]
    return text.strip()

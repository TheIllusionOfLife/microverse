"""Project-wide constants.

Single source of truth for the model name, sampling defaults, and runtime
caps. Importable from anywhere — keep this module dependency-free.
"""

from __future__ import annotations

# Single-model invariant. Every LLM call goes here. No router, no fallback.
MODEL: str = "gemma4:e4b"

# Hard caps for a single LLM call.
LLM_TIMEOUT_S: float = 90.0
LLM_MAX_TOKENS: int = 1024
# Trader.rank() returns one Score per buffered artifact in a single
# response; a full 50-tick flush can hold ~20 items with up-to-300-char
# rationales, which exceeds the 1024-token default. Bump to fit the
# whole array without truncating the tail to score=0.0.
LLM_MAX_TOKENS_RANK: int = 4096

# Retry / failure-mode caps used by agents and the watchdog.
MAX_RETRIES: int = 2
MAX_CONSECUTIVE_FAIL: int = 3

# Hard cap on bytes fed into ``parse_action`` (json.loads + json_repair).
# Above this, skip the parse attempts and fall back directly to a safe
# rest action — protects the tick loop from O(N^2) repair time on
# pathological inputs.
MAX_PARSE_BYTES: int = 32 * 1024

# Sampling presets per ~/.claude/skills/local-llm/SKILL.md:74-78. Picked
# by role: creative roles (Artisan, Scholar, Stranger) want exploration;
# judging roles (Trader, Harvester) want lower variance.
SAMPLING_CREATIVE: dict[str, float | int] = {
    "temperature": 1.0,
    "top_k": 20,
    "top_p": 0.95,
}
SAMPLING_FACTUAL: dict[str, float | int] = {
    "temperature": 0.6,
    "top_p": 0.9,
}

# Default tick budget for a non-infinite microverse.run.
MAX_TICKS_DEFAULT: int = 1_000_000

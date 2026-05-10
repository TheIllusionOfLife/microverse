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
# Pair the larger budget with a longer per-call timeout so a worst-case
# generation near the 4096-token cap doesn't breach the 90s per-call
# limit on slower local hardware (~30 tok/s on Apple Silicon for an 8B
# Q4 model puts 4096 tokens at ~137s).
LLM_TIMEOUT_RANK_S: float = 300.0

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

# Circuit breaker on the deadlock-break path in run.py. Each invocation
# without an intervening successful think() increments a counter; once
# it reaches this value the loop exits with a deadlock_break_exit metric
# bump rather than spinning forever. A 17.9h soak with intermittent
# Ollama failures generated 6372 timeout bumps because no such bound
# existed. 10 keeps a brief outage recoverable while bounding the worst
# case to ~30-60 wasted think() calls before exit.
MAX_CONSECUTIVE_DEADLOCK_BREAKS: int = 10

# Layer E.2: hard post-LLM rate-limit in Artisan. After this many
# consecutive intentional rests, the next intentional rest is coerced
# to speak (if peers exist) or study. Three is conservative — a real
# artisan can rest a few times in a row legitimately, but four-in-a-row
# is the empirical trap signature from soak-24h-3.
ARTISAN_REST_STREAK_LIMIT: int = 3

# Layer-G slice 3 (R2.b): engagement gate. If an agent produces no
# `speak` with a non-null target across this many of its own actions,
# the next tick injects an engagement hint + required_target into
# WorldContext and Artisan coerces if the LLM disobeys. K=20 is a
# generous floor (peer interaction every ~20 personal ticks); under
# the post-Layer-F soak Aki silently crafted hundreds of ticks in a
# row, so 20 is plenty of head-room without being intrusive.
PEER_ENGAGEMENT_INTERVAL: int = 20

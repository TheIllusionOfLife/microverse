"""Project-wide constants.

Single source of truth for the model name, sampling defaults, and runtime
caps. Importable from anywhere — keep this module dependency-free.
"""

from __future__ import annotations

# Single-model invariant. Every LLM call goes here. No router, no fallback.
# v0.3 (ADR 0004 Decision 5): swapped from "gemma4:e4b" (9.6 GB) to
# "gemma4:26b" (17 GB) — same model family (prompt format, tokenizer,
# thinking-channel behavior preserved); only parameter count changes.
# Phase 0 qualification smoke (60 ticks, seed 38) passed all four
# gates: 60/60 valid Action JSON, p95 latency 5.91s, Trader rank()
# returned distinct scores across [0.20..0.90], no immediate
# action-share divergence from the e4b baseline.
MODEL: str = "gemma4:26b"

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

# v0.2 (ADR 0003): Harvester per-flush per-primary-verb caps applied
# AFTER Trader ranking. Bounds the rate at which any single action
# verb can dominate the harvest manifest — defence-in-depth against
# the Trader v2 itself becoming a new attractor. A 50-tick flush at
# v0.1.1 cadence yields ~15 flushes/hour, so a cap of 5 caps the
# craft inbox at ~75/hour vs the unbounded v0.1.1 ~280-830/hour.
HARVEST_CRAFT_CAP_PER_FLUSH: int = 5

# v0.2 (ADR 0003): Watchdog workshop_stale detector. When all
# configured WIPs are flat (no new contribute events) for longer
# than this window, bump ``watchdog_workshop_stale`` so the operator
# can see the workshop affordance has fallen out of use.
WORKSHOP_STALE_TIMEOUT_S: float = 3600.0  # 1 hour

# v0.2 (ADR 0003): Trader v2 novelty term — number of recent
# completed WIPs to compare against when computing the Jaccard
# distance for ``score_wip``. Larger N smooths the novelty score
# at the cost of more tokenisation work; 8 matches the workshop's
# fragment-count threshold for ``developing → complete`` so the
# lookback is the same order of magnitude as a single completed
# WIP's fragment count.
TRADER_WIP_NOVELTY_LOOKBACK: int = 8

# v0.3 (ADR 0004 Decision 1): WIP recycle lifecycle bounds. After a
# WIP has been rejected MAX_HARVEST_ATTEMPTS times in a row, the
# Harvester force-recycles it (drops the fragments, resets to
# forming). Independently, any WIP held in ``complete`` longer than
# HARVEST_PENDING_TIMEOUT_S is force-recycled regardless of attempts.
# Both bounds together guarantee the capacity invariant: the system
# maintains at least ``len(CONFIGURED_WIPS)`` open slots in steady
# state, eliminating the v0.2 ``complete-WIP black hole`` where 83%
# of contributes silently fell into locked WIPs.
MAX_HARVEST_ATTEMPTS: int = 3
HARVEST_PENDING_TIMEOUT_S: float = 1800.0  # 30 min

# v0.3 (ADR 0004 Decision 2): hard floor on contribute fragment
# length. The 120-character / ~25-word floor is the structural
# enforcement point against v0.2's pathology where ~91-char single-
# sentence object descriptions rode through the contribute verb.
# The composite acceptance gate (length AND repeat-4gram AND peer-
# reference) is the load-bearing guard against this floor becoming
# a new padding attractor.
MIN_FRAGMENT_CHARS: int = 120

# v0.3 (ADR 0004 Decision 4): WIP acceptance policy is structurally
# distinct from the artifact-side p70 percentile. WIPs use an
# absolute floor (defence-in-depth against single-contributor padded
# WIPs that slip past the subfloor) AND a contributor subfloor —
# the actual goal is cross-agent dialogue, not "long fragments." A
# solo WIP clearing the absolute floor is structurally not the
# artifact we want; the subfloor is the load-bearing guard.
WIP_ACCEPTANCE_FLOOR: float = 0.55
WIP_CONTRIBUTOR_FLOOR: int = 2

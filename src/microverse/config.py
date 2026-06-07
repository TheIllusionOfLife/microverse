"""Project-wide constants.

Single source of truth for the model name, sampling defaults, and runtime
caps. Importable from anywhere — keep this module dependency-free.
"""

from __future__ import annotations

import os

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

# v1.1 (ADR 0007 Phase 1, Pillar 1): static per-role traits surfaced in
# the persistent self-record. One short, stable line per role — the
# durable "who you are" anchor that survives across ticks. Dynamic
# beliefs/commitments are summarized separately (Stage C); these traits
# are intentionally fixed. Roles without an entry render no trait line.
TRAITS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "artisan": ("You make things with patient hands, and prefer to show rather than tell.",),
    "scholar": ("You weigh ideas carefully and notice what others might miss.",),
    "stranger": ("You carry an outsider's eye and trust contrast over easy consensus.",),
}

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

# v0.4 (Phase A): operational retention bounds for multi-week soaks.
# Without these, a 7-day soak fills disk via unbounded snapshots (~50
# archives x ~50MB = 2.5 GB), unbounded manifest.jsonl growth, and an
# ever-growing -wal sidecar on episodic.sqlite.
#
# SNAPSHOT_RETENTION_*: prune_snapshots() drops oldest archives until
# BOTH bounds hold. Newest archive is always preserved even when over
# the count cap (otherwise a single huge snapshot would orphan its own
# tree).
SNAPSHOT_RETENTION_COUNT: int = 24
SNAPSHOT_RETENTION_BYTES: int = 5 * 1024**3  # 5 GiB

# MANIFEST_ROTATE_BYTES: harvester rotates manifest.jsonl to
# manifest-<UTC>.jsonl when the active file passes this size. Readers
# (dashboard, gates producer) glob manifest*.jsonl.
MANIFEST_ROTATE_BYTES: int = 256 * 1024 * 1024  # 256 MiB

# EPISODIC_OPTIMIZE_EVERY: how often to call EpisodicMemory.optimize()
# (wal_checkpoint(TRUNCATE) + PRAGMA optimize). Not a VACUUM — that
# would lock the long-lived writer. 100k events ≈ one optimize per day
# at Soak B throughput.
EPISODIC_OPTIMIZE_EVERY: int = 100_000

# v1.1 (ADR 0007 Phase 1, Stage C): dynamic beliefs. The belief
# summarizer is an out-of-world LLM pass (like Elder.compress_lore /
# Trader.rank — NOT inside agent.think(), so the single-model invariant
# for the action loop is preserved). Beliefs are regenerated every
# BELIEF_UPDATE_INTERVAL ticks per scheduled agent, summarized from the
# last BELIEF_LOOKBACK events involving that agent, capped at
# BELIEF_MAX_CHARS, and persisted in data/identity.sqlite (a materialized
# cache over the WAL log — regenerable, not an authoritative store).
BELIEF_UPDATE_INTERVAL: int = 200
BELIEF_LOOKBACK: int = 60
BELIEF_MAX_CHARS: int = 280
# A belief is one short sentence; a tight token cap bounds both the
# truncation waste and the worst-case per-call latency of the
# synchronous out-of-world summarization pass.
BELIEF_MAX_TOKENS: int = 96

# v0.4 (Phase C): embedding model for gate-7 scene semantic dependence
# measurement. NEVER used inside agent.think() — single-model invariant
# is preserved for the agent action loop. Embeddings are observability
# infrastructure called only from spike_workshop_measure.py.
EMBEDDING_MODEL: str = "nomic-embed-text"

# v0.4 (ADR 0005 D3): probability a chosen agent's tick routes into a
# 3-turn scene instead of a single-tick action. Conservative default
# 0.15 — scenes are 3x LLM volume per artist-tick, so a 7-day soak
# stays in latency budget. Raise to 0.25 after the v0.4 acceptance
# soak confirms throughput holds.
SCENE_GATE_P: float = 0.15
# Minimum number of distinct peers besides the chosen agent for the
# scene gate to fire. The default roster is 2 agents (Aki + Cy), so
# other_peers per tick is length 1 — gating at >= 2 makes the scene
# gate unreachable in production (proven empirically by the v1.0 RC
# soak: 46 h of ticks produced zero scenes). The 1-peer rotation
# A->B->A is explicitly supported by ``pick_authors`` and covered by
# tests; ADR 0006 documents turn 3's scene_context for the
# same-author case as explicit scene input (NOT autobiographical
# replay). Gate 8 still measures real semantic dependence under this
# rotation: turn 2 reads turn 1, turn 3 reads turn 1+2. Raising this
# back to >= 2 requires growing the default roster first.
SCENE_MIN_PEERS: int = 1

# ----------------------------------------------------------------------
# Action-economy spike (re-diagnosis under HALT — ADR 0008).
#
# Falsifiable test of ADR 0008's claim that Gate 3 (verb monoculture) is
# STRUCTURAL and needs an action-economy lever, not identity. A finite
# per-agent stamina pool + comparative-advantage verb costs make each role
# cheap at its specialty and dear elsewhere; the scene-initiation gate and a
# hard-substitution lever are the two mechanisms. Env-driven so all A/B arms
# run from a single commit (no edit between runs):
#
#   "0"        off — constructs no ledger; reproduces current main EXACTLY.
#   "1"        role-advantage (both mechanisms: scene gate + substitution).
#   "flat"     role-agnostic control (both mechanisms, no cheap specialty).
#   "throttle" ablation: scene gate only (no substitution).
#   "sub"      ablation: substitution only (no scene gate).
#
# The "flat" control and the two ablations exist to attribute any diversity
# gain (Codex review): is it comparative advantage, or merely a uniform
# contribute throttle, or merely executor override?
# The full set of recognized modes. An unrecognized value (e.g. a typo) would
# otherwise build a ledger with neither gate nor substitution — a silent,
# unlabeled experiment arm — so run() validates against this set and fails fast.
# ``adv`` (advantage-perception, ADR 0009 follow-up) == ``sub`` (substitution +
# hint, no scene gate) EXCEPT the energy_hint names the agent's TRUE cheapest
# affordable verb including its payload specialty (craft), so each role is nudged
# toward its OWN advantage instead of the shared payload-free escape that caused
# co-drift. The selector differs (run._compute_energy_hint dispatches on adv);
# the cost table and gates are identical to ``sub``.
# ``bal`` (balanced contribute, ADR 0010 follow-up) == ``adv`` (honest hint) PLUS
# a cost table where every role's contribute is raised to the dearest (22), so a
# contribute-heavy scholar drains and its scarcity hint fires (Stage 4 showed adv
# specializes the artisan but not the scholar, whose cheap contribute never
# triggers the hint). Same hint selector as ``adv``; differs only in the table.
VALID_ECONOMY_MODES: frozenset[str] = frozenset({"0", "1", "flat", "throttle", "sub", "adv", "bal"})
ECONOMY_MODE: str = os.environ.get("MICROVERSE_ECONOMY", "0")
ECONOMY_ENABLED: bool = ECONOMY_MODE != "0"
_ECONOMY_SCENE_GATE: bool = ECONOMY_MODE in ("1", "flat", "throttle")
_ECONOMY_SUBSTITUTE: bool = ECONOMY_MODE in ("1", "flat", "sub", "adv", "bal")

# Finite stamina pool. Regen sits between the cheap specialty (~6) and the
# dear contribute (~22) so a role can sustain its specialty indefinitely but
# must save up across cheaper/idle ticks to afford an off-specialty verb or
# to re-initiate a scene — the scarcity pressure that should diversify the mix.
#
# Stage 0/1 tune (post-#45): regen 12 → 8. At 12 a pure-contribute policy was
# NOT throttled — in the 2-agent roster the whole-roster per-tick regen returns
# 2*12=24 between an agent's own actions, exceeding even the artisan's
# contribute cost (22), so the pool never drains and the cost numbers never
# bit. 8 makes contribute drain (offline replay: always-contribute sub_rate
# 0.0 → 0.37, contribute share 1.0 → 0.63) while the role specialty (cost 6)
# stays fully sustainable (sub_rate 0.0). ENERGY_MAX is not the lever — the
# steady state oscillates near the regen/cost balance, not near max. Verify
# with: uv run python scripts/replay_economy.py --synthetic --regen 8.
ENERGY_MAX: float = 100.0
ENERGY_REGEN_PER_TICK: float = 8.0

# Comparative advantage: each role is cheap at exactly one productive verb
# (its strict specialty) and dear elsewhere. ``rest`` is free for every role
# (always affordable — the energy analog of the scheduler's
# ``max(soul_tokens, 1)`` floor, so the system can never deadlock on energy).
VERB_COST_BY_ROLE: dict[str, dict[str, float]] = {
    "artisan": {
        "craft": 6.0,
        "study": 14.0,
        "speak": 16.0,
        "travel": 18.0,
        "rest": 0.0,
        "contribute": 22.0,
    },
    "scholar": {
        "study": 6.0,
        "speak": 10.0,
        "craft": 18.0,
        "travel": 16.0,
        "rest": 0.0,
        "contribute": 14.0,
    },
    "stranger": {
        "travel": 6.0,
        "speak": 10.0,
        "study": 12.0,
        "craft": 16.0,
        "rest": 0.0,
        "contribute": 18.0,
    },
}

# Phase D diversity-lever substitution probability, promoted out of the
# hardcoded agent constants so the A/B can prove the economy flag-off arm is a
# true no-op without the diversity lever as a confound.
DIVERSITY_SUBSTITUTE_PROB: float = 0.30

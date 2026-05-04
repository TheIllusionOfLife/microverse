# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also `AGENTS.md` (contributor conventions) and `README.md` (operator workflows). `PROMPT.md` is the historical ralph-loop build prompt; the project is now at `v0.1.0`, so treat its phase mechanics as historical context, not active workflow.

## Common commands

```bash
uv sync                                                    # install deps
uv run ruff check                                          # lint
uv run ruff format --check                                 # formatting
uv run mypy src/microverse                                 # static typing
uv run pip-audit                                           # dependency audit
uv run pytest -q -m 'not integration'                      # default suite (offline, no Ollama)
uv run pytest -q -m integration                            # only when Ollama + gemma4:e4b are live
uv build                                                   # package build check
uv run pytest -q tests/test_harvester.py::test_name        # single test
uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42   # bounded smoke run
uv run python -m microverse.ops.metrics --report --db data/metrics.sqlite
uv run python scripts/render_dashboard.py --data data --harvest harvest
uv run python scripts/verify_kill_drill.py --db data/episodic.sqlite --watermark "$W"
```

`MICROVERSE_DATA` and `MICROVERSE_HARVEST` redirect runtime state and harvest output respectively. `data/`, `harvest/`, and logs are intentionally untracked.

CI runs Ruff, mypy, pip-audit, the default pytest suite, and `uv build` on pull requests. `SECURITY.md` covers vulnerability reporting and generated-data handling. Architecture decisions live in `docs/adr/`.

## Architecture

The runtime is a single-process tick loop wired in `src/microverse/run.py`. One tick:

1. `WeightedScheduler.next()` picks an agent (weight = `soul_tokens`, floor 1).
2. If `Metrics.should_pause(agent)` is true, skip. After `>3 * len(agents)` consecutive skips AND every agent paused, the deadlock-break path sleeps and resets each paused agent's `consecutive_fail` (`run.py:178`).
3. `_derive_topic()` picks the most recent `weather.*` event kind (or falls back to role + name) and `memory.build_context()` assembles a `WorldContext` with bounded `recent_episodic` (≤1500 tok) + `lore_excerpt` (≤600 tok).
4. `agent.think(world)` returns an `Action`; exceptions bump `llm_timeout` + `consecutive_fail` and skip commit.
5. `_commit_action()` appends to `EpisodicMemory` (SQLite WAL); `_maybe_harvest()` buffers the artifact for the Harvester.
6. Every tick: `WorldClock.advance()` may emit a `weather.*` event. Every 25 ticks: `Watchdog.check()`. Every 50 ticks: `Harvester.flush()`. Every 1000 ticks: `world.snapshot.maybe_snapshot()`.

Layout follows `src/` package convention; modules map cleanly to roles:

| Module | Role |
|---|---|
| `agents/` | `Artisan` (creates), `Trader` (ranks at flush, *not* scheduler-registered), `Elder` (lore compression w/ Jaccard drift guard), `Stranger` (immigrant, spawned by Watchdog), `Harvester` (out-of-world artifact writer), `base.py` (Action schema + `parse_action`). |
| `memory/` | `EpisodicMemory` (events, WAL), `SemanticMemory` (FTS5 lore), `build_context` (assembler with token budgets). |
| `world/` | `WeightedScheduler`, `WorldClock` (seeded weather), `snapshot.py` (cold backups under `data/snapshots/`). |
| `ops/` | `Metrics` (in-mem counters → SQLite time-series), `Watchdog` (runaway / stagnation / diversity detectors). |
| `llm/` | `ollama_client.chat` (the *only* model entry point), `thinking.strip_thinking` (defense-in-depth scrubber). |
| `prompts/` | Jinja persona templates (`persona_*.j2`, `compression.j2`). |

## Non-obvious invariants

- **Single model.** `microverse.config.MODEL = "gemma4:e4b"`. Every LLM call goes through `microverse.llm.ollama_client.chat`. No router, no fallback, no other models.
- **Trader is not scheduled.** `Trader` is constructed but *not* registered in the `WeightedScheduler`. It runs only when `Harvester.flush()` calls its `rank()` (`run.py:145-152`).
- **Harvester has two modes.** Without a trader: heuristic length check, write immediately. With a trader: buffer in `consider()`, rank + percentile-cutoff in `flush()`. The flush *re-raises* on ranker failure so retry can rescue the buffer; if all scores tie across ≥2 items, accept nothing.
- **`parse_action` never raises.** It tries strict JSON → `json_repair` → fallback to `rest`. It also blocks meta-leaks (`META_LEAK_RE` / `META_LEAK_PHRASE_RE` in `agents/base.py`) and short-circuits inputs over `MAX_PARSE_BYTES` (32 KiB).
- **WAL is the durability boundary.** Snapshots under `data/snapshots/` are cold backups for catastrophic corruption, *not* the recovery path. SQLite is opened with `synchronous=NORMAL`; verify with `scripts/verify_kill_drill.py`.
- **Thinking discipline.** Pass `think=False`, force `thinking=""` on response, run `strip_thinking()` defensively, bump `thinking_leak` on any leak signal. Never expose raw `thinking` to callers.
- **Watchdog mutates the scheduler.** It is the only authority that may register a `Stranger` mid-run (echo-chamber rehab, capped by `max_strangers`).
- **Topic must be derived per tick.** `_derive_topic` depends on `agent.role`; caching across ticks would mis-tag lore retrieval for Stranger immigrants spawned mid-run.
- **Signals.** `SIGINT` / `SIGTERM` set a stop flag so `finally` runs a final harvester flush. `SIGKILL` is recoverable via WAL but discards the in-flight tick.

## Workflow rules

- Branch before any work; never push to `main`. Push an explicit branch (e.g., `git push origin docs/update-readme`).
- TDD for behavioral changes: red commit, then green commit; small slices may be one commit.
- One PR open at a time; address review feedback as follow-up commits to the same PR.
- Verification before declaring done: `uv run ruff check && uv run ruff format --check && uv run mypy src/microverse && uv run pip-audit && uv run pytest -q -m 'not integration' && uv build`.
- Ruff config: line length 100, `py312`, rule set `E,F,I,B,UP,SIM,C4,PT,RUF,T20,ERA`. `print()` allowed only in tests, `ops/metrics.py`, and `run.py`.
- Live-Ollama tests must carry the `integration` marker so the default suite stays offline and fast.

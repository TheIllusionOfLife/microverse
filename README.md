# Microverse Battery

A long-running multi-agent simulation inspired by *Rick and Morty*'s Microverse Battery. Inhabitant agents live in a fictional world, produce artifacts (essays, code, data, designs), and a single out-of-world Harvester ferries the best artifacts to `harvest/inbox/` for the user.

Built to run autonomously for weeks on local Apple Silicon at zero marginal cost using **only** `gemma4:e4b` via local Ollama.

## Status

Phases 0 → 4b merged. See `TODO.md` for the phase ladder and per-task evidence; `PROMPT.md` is the build-time ralph-loop driver.

## Operator runbook

### Start a run

```bash
# Background, infinite, default tempo (production-ish):
nohup uv run python -m microverse.run --seed 42 > microverse.log 2>&1 &
echo $! > microverse.pid

# Foreground, bounded, fast (smoke / acceptance):
uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42
```

Environment overrides:

- `MICROVERSE_DATA` — override the `data/` location (episodic + metrics + snapshots).
- `MICROVERSE_HARVEST` — override the `harvest/` location.

### Stop and resume

```bash
# Graceful: SIGINT (Ctrl-C) or SIGTERM. The finally block flushes
# Trader buffers, closes Metrics + Episodic.
kill -INT  $(cat microverse.pid)
kill -TERM $(cat microverse.pid)

# Hard: SIGKILL. WAL guarantees no committed event is lost; the
# in-flight tick is discarded (not re-played) — only committed
# events recover on restart.
kill -KILL $(cat microverse.pid)
```

To resume after any of the above, just re-run the start command pointing at the same `MICROVERSE_DATA` and `MICROVERSE_HARVEST`.

### Inspect

```bash
# Latest metrics snapshot:
uv run python -m microverse.ops.metrics --report --db data/metrics.sqlite

# Static dashboard (HTML, no JS, no external assets):
uv run python scripts/render_dashboard.py --data data --harvest harvest
open harvest/dashboard.html
```

### Verify kill-safety after a SIGKILL drill

```bash
# 1) Capture the pre-kill count.
PRE=$(sqlite3 data/episodic.sqlite 'SELECT COUNT(*) FROM events')

# 2) SIGKILL the run, restart it, then verify zero tail loss:
uv run python scripts/verify_kill_drill.py \
    --db data/episodic.sqlite --min-events "$PRE"
# → kill_drill_ok (N events, ids 1..N, contiguous, >= PRE-1 (pre-kill PRE))
```

Without `--min-events`, the script only proves event-log internal integrity (no gaps, no duplicates). The pre-kill watermark is what catches silent tail loss.

### Snapshot / restore

Snapshots are taken automatically every 1000 ticks under `data/snapshots/`. WAL is the durability primary; snapshots are for catastrophic-corruption rollback only.

```bash
# Manual snapshot:
uv run python -c "from microverse.world.snapshot import take_snapshot; \
  print(take_snapshot('data', 'data/snapshots'))"

# Restore (wipes data/ and replaces with the archive):
uv run python -c "from microverse.world.snapshot import restore_snapshot; \
  restore_snapshot('data/snapshots/<archive>.tar.gz', 'data')"
```

### Watchdog tuning

`microverse.ops.watchdog.Watchdog` constructor knobs (defaults in parens):

- `runaway_max_consecutive` (4) — N identical actions per agent in a row before flagging.
- `stagnation_window` (50) / `stagnation_floor` (1) — fewer than `floor` artifacts in the most recent `window` triggers stagnation.
- `diversity_floor` (0.35) — `1 - mean Jaccard` below this triggers echo-chamber → spawns a Stranger.
- `diversity_window` (20) — number of recent actions used for the diversity calc.
- `max_strangers` (3) — caps the Stranger pool to avoid pile-up if echo persists.

Override via `Watchdog(metrics=..., episodic=..., scheduler=..., diversity_floor=0.40, ...)` in `run.py`.

### Common failure recovery

- **All agents paused:** the run loop auto-rehabs by resetting `consecutive_fail` after one rotation of skips. If the model keeps failing, check `data/metrics.sqlite` for `llm_timeout` and `json_fallback_rest` rates.
- **Trader returning all-zero scores:** `harvester` will accept nothing on tied populations (intended). Check `lore_chat_failure` to see if the Trader's chat itself is failing.
- **Lore drift loop:** `lore_drift_block` rising means the Elder keeps producing off-canon rewrites. Inspect the most recent `data/lore/world_lore.md` and consider raising `MIN_JACCARD` in `agents/elder.py`.
- **Disk filling:** snapshots accumulate in `data/snapshots/`. Manually trim oldest archives. (A retention policy is on the post-core backlog.)

## Prerequisites

- macOS on Apple Silicon (tested on M-series)
- Python 3.12 via `uv`
- Ollama running locally with `gemma4:e4b` pulled
  ```bash
  ollama pull gemma4:e4b
  ollama serve  # if not already running
  ```

## Setup

```bash
uv sync
uv run pytest -q                  # unit tests
uv run pytest -q -m integration   # hits live Ollama
```

## Thinking-mode discipline

Per official Ollama docs, `think` is a top-level field on the chat/generate API. Empirically on this codebase:

- `think=False` on `gemma4:e4b` returns `message.thinking == ""` and no `<think>` leak in `message.content`. ✅ Contract holds.
- `think=True` also returns empty `thinking` for `gemma4:e4b` — Ollama does not classify this build as a thinking-capable model in its registry. The integration test (`tests/test_ollama_think_off.py::test_think_true_branch_or_skip`) skips this branch when observed.

Defense-in-depth: `microverse.llm.thinking.strip_thinking` is unconditionally applied to response content, so callers never see thinking tokens regardless of model or runtime quirks. The `microverse.llm.ollama_client.thinking_leak` counter increments any time the strip actually trims content — this is a regression signal for monitoring.

## Architecture

The build is staged across seven phases. See `TODO.md` for the per-phase task ladder with machine-checkable acceptance commands; the more detailed implementation plan was authored locally during planning and lives outside the repo. Each phase merges as its own PR (see commit history); `PROMPT.md` is the persistent prompt used by the ralph-loop driver during the build.

## License

See `LICENSE`.

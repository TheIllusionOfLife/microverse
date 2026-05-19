# Microverse Battery

> **Gemma 4 Hackathon submission.** Live dashboard:
> https://theillusionoflife.github.io/microverse/ ·
> Writeup: see `WRITEUP.md` ·
> Video: linked from the writeup once recorded.

Microverse Battery is a long-running local multi-agent simulation inspired by
*Rick and Morty*'s Microverse Battery. A small fictional society of AI agents
generates artifacts (essays, code, sketches, observations); a Trader ranks
those artifacts; a Harvester writes the best ones to `harvest/inbox/` for
review. A Watchdog can spawn immigrant Strangers when the conversation gets
stale. The whole thing runs on one Apple Silicon laptop with zero cloud calls
and zero API bills.

## Why this matters

Most multi-agent demos depend on a hosted API: each tick has a per-token cost,
each "thought" leaves your machine, and the system stops working the moment the
network does. For privacy-sensitive use (regulated industries, classrooms,
journalism, individual creatives) and edge contexts (clinics with spotty
internet, field research, offline-first apps), that's a non-starter.

Microverse Battery shows what changes when the model invariant is *local*:
every tick is a local Ollama call against a single Gemma 4 model
(`gemma4:26b`). The episodic log is a local SQLite WAL — auditable
artifact-by-artifact. The dashboard is a static HTML file, no JS framework.
The bottleneck is your laptop, not your budget. A 24-hour soak produced
13,794 events and 1,644 accepted artifact candidates (702 of them
multi-author workshop entries), with full provenance for every
contribution. The documented limits are documented honestly — see
`docs/adr/`.

## Status

`v0.3.1` ships the core simulation loop, the shared-workshop artifact
substrate (ADR 0003), the residual-shape-attractor structural fixes
(ADR 0004), and ADR 0005 Decision 1 — hide complete workshop entries
from the persona prompt — as experimental mitigation for the v0.4
harness-shape roadmap. The soak gate evidence is committed at the repo
root (`soak-24h-v03-e4b-gates.json`, `soak-24h-v03-26b-gates.json`);
the soak event databases under `data/` are gitignored and reproducible
from a local run. The rendered dashboard for the 26b soak ships at
`docs/index.html` (live via GitHub Pages).

## What it does

- Runs a tick-based world loop where an Artisan creates artifacts each tick;
  a Trader ranks buffered artifacts at harvest-flush time; an Elder
  rewrites lore on demand with a Jaccard drift guard; and the Watchdog may
  spawn a Stranger to break echo chambers.
- Stores committed events in SQLite WAL-backed memory under `data/`.
- Builds bounded context from recent events and FTS5 semantic memory.
- Includes an Elder lore-compression component with a drift guard.
- Buffers generated artifacts, ranks them, and writes accepted artifacts to
  `harvest/inbox/`.
- Emits metrics and a static, self-contained HTML dashboard.
- Survives process crashes without losing committed events.

## Requirements

- macOS on Apple Silicon (M-series). Linux should work; not exercised.
- Python 3.12 managed through `uv`.
- Ollama running locally.
- `gemma4:26b` (17 GB) pulled in Ollama. The smaller `gemma4:e4b` (9.6 GB)
  also works and is documented in ADR 0002 / ADR 0004; the v0.3 production
  default is `gemma4:26b` per `src/microverse/config.py`.

```bash
ollama pull gemma4:26b      # production default for v0.3
ollama pull gemma4:e4b      # optional, used by the v0.3.1 acceptance smoke
ollama serve                 # skip if Ollama is already running as a service
```

## Quickstart

Install dependencies and run the non-integration test suite:

```bash
uv sync
uv run pytest -q -m 'not integration'
```

Run a bounded smoke simulation:

```bash
uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42
```

This creates local runtime data in `data/` and harvested artifacts in
`harvest/`.

## Running the simulation

Start a longer background run:

```bash
nohup uv run python -m microverse.run --seed 42 > microverse.log 2>&1 &
echo $! > microverse.pid
```

Run in the foreground with a fixed tick count:

```bash
uv run python -m microverse.run --ticks 100 --seed 42
```

Run as fast as possible for local acceptance checks:

```bash
uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42
```

Environment overrides:

- `MICROVERSE_DATA` changes the runtime data directory.
- `MICROVERSE_HARVEST` changes the harvest output directory.

Example:

```bash
MICROVERSE_DATA=/tmp/microverse/data \
MICROVERSE_HARVEST=/tmp/microverse/harvest \
uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42
```

## Inspecting output

Print the latest metrics snapshot:

```bash
uv run python -m microverse.ops.metrics --report --db data/metrics.sqlite
```

Render the static dashboard:

```bash
uv run python scripts/render_dashboard.py --data data --harvest harvest
open harvest/dashboard.html
```

Accepted artifacts are written under `harvest/inbox/`, and
`harvest/manifest.jsonl` records accepted and rejected candidates for audit.

## Architecture

The runtime is intentionally small and local-first:

- `microverse.run` wires the tick loop, scheduler, memory, metrics, harvester,
  world clock, and watchdog.
- `microverse.agents` contains the resident behaviors and artifact-ranking
  logic.
- `microverse.memory` stores episodic events in SQLite WAL and semantic recall
  in SQLite FTS5.
- `microverse.world` contains scheduling, clock events, and snapshot support.
- `microverse.ops` contains metrics reporting and watchdog detectors.
- `scripts/` contains operator tooling such as dashboard rendering and
  kill-drill verification.

All LLM calls go through the local Ollama client and use the model configured in
`microverse.config.MODEL`.

## Operations

### Stop and resume

Graceful shutdown runs final flush and close handlers:

```bash
kill -INT $(cat microverse.pid)
# or
kill -TERM $(cat microverse.pid)
```

To resume, start the process again with the same `MICROVERSE_DATA` and
`MICROVERSE_HARVEST` locations.

### Crash recovery

SQLite WAL is the durability boundary. A `SIGKILL` may discard the in-flight
tick, but committed events should remain intact.

For a kill drill, capture the committed high-watermark before the kill.
Use `MAX(id)` rather than `COUNT(*)`: after restart the process appends new
events, so a raw count can mask a missing pre-kill tail.

```bash
W=$(sqlite3 data/episodic.sqlite 'SELECT COALESCE(MAX(id), 0) FROM events')
kill -KILL $(cat microverse.pid)
```

Restart the run, then verify every pre-kill event survived:

```bash
uv run python scripts/verify_kill_drill.py \
  --db data/episodic.sqlite \
  --watermark "$W"
```

Expected output starts with `kill_drill_ok`.

### Snapshots

Snapshots are cold backups for catastrophic corruption rollback. WAL remains the
primary recovery mechanism.

Snapshots are taken automatically every 1000 ticks under `data/snapshots/`.
Create one manually with:

```bash
uv run python -c "from microverse.world.snapshot import take_snapshot; print(take_snapshot('data', 'data/snapshots'))"
```

Restore a snapshot with:

```bash
uv run python -c "from microverse.world.snapshot import restore_snapshot; restore_snapshot('data/snapshots/<archive>.tar.gz', 'data')"
```

Restore replaces the target data directory.

### Common recovery checks

- If all agents appear paused, inspect `consecutive_fail`, `llm_timeout`, and
  JSON fallback counters in `data/metrics.sqlite`.
- If no artifacts are accepted, inspect `harvest/manifest.jsonl`; all-zero or
  tied Trader scores intentionally accept nothing.
- If lore drift is blocked repeatedly, inspect the current lore input/output and
  the `lore_drift_block` metric.
- If disk usage grows too much, trim old archives in `data/snapshots/`.

## Development

Run the standard local checks:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy src/microverse
uv run pip-audit
uv run pytest -q -m 'not integration'
uv build
```

Run integration tests only when Ollama is live and `gemma4:e4b` is available:

```bash
uv run pytest -q -m integration
```

Useful CLI help:

```bash
uv run python -m microverse.run --help
uv run python -m microverse.ops.metrics --help
uv run python scripts/render_dashboard.py --help
```

GitHub Actions runs these quality gates on pull requests and `main`. Dependabot
keeps Python and workflow dependencies current.

## Security

See `SECURITY.md` for supported versions, vulnerability reporting, and guidance
for handling generated runtime data in `data/` and `harvest/`.

## Architecture Decisions

Architecture decisions live in `docs/adr/`. Start with
`docs/adr/0001-local-first-agent-runtime.md` for the local-first runtime,
single-model Ollama, SQLite WAL, and FTS5 recall decisions.

## Thinking-mode handling

Ollama exposes `think` as a top-level chat/generate API field. This project
calls the local model with thinking disabled where required, and applies
`microverse.llm.thinking.strip_thinking` defensively to response content.

For `gemma4:e4b`, the integration test verifies that `think=False` returns no
thinking content. If a future model leaks thinking tokens into content, the
client strips them and increments the `thinking_leak` counter.

## License

See `LICENSE`.

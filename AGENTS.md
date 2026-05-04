# Repository Guidelines

## Project Structure & Module Organization

Microverse is a Python 3.12 package using a `src/` layout. Runtime code lives in
`src/microverse/`: `agents/` contains resident behavior, `memory/` contains
SQLite-backed episodic and semantic memory, `world/` contains scheduling,
clock, and snapshot logic, `ops/` contains metrics and watchdog tooling, and
`llm/` wraps local Ollama calls. Operator scripts live in `scripts/`. Tests live
in `tests/` and mirror the runtime modules by behavior, for example
`tests/test_harvester.py` and `tests/test_kill_safety.py`.

Generated runtime state is intentionally outside source control: `data/`,
`harvest/`, logs, and local virtual environments should remain untracked.

## Build, Test, and Development Commands

- `uv sync` installs project and development dependencies.
- `uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42` runs a fast
  bounded smoke simulation.
- `uv run python -m microverse.ops.metrics --report --db data/metrics.sqlite`
  prints the latest metrics snapshot.
- `uv run python scripts/render_dashboard.py --data data --harvest harvest`
  renders `harvest/dashboard.html`.
- `uv run ruff check` runs lint checks.
- `uv run ruff format --check` verifies formatting.
- `uv run pytest -q -m 'not integration'` runs the default test suite.

Use `uv run pytest -q -m integration` only when Ollama is running locally with
`gemma4:e4b` pulled.

## Coding Style & Naming Conventions

Use Ruff for linting and formatting. The project targets Python 3.12, a
100-character line length, and 4-space indentation. Prefer typed, dependency-light
modules with clear boundaries. Module and function names use `snake_case`; class
names use `PascalCase`; constants use `UPPER_SNAKE_CASE`.

Keep behavior simple and local-first. All model calls should go through
`microverse.llm.ollama_client`, and shared runtime constants belong in
`microverse.config`.

## Testing Guidelines

Tests use `pytest`. Name files `test_*.py` and write focused tests around
observable behavior. For fixes, reproduce the issue with a failing test first,
then implement the smallest change that makes it pass. Keep live Ollama tests
marked `integration` so the default suite remains fast and offline.

## Commit & Pull Request Guidelines

Recent history uses short imperative or conventional-style summaries such as
`docs: refresh README`, `refactor: simplify codebase`, and
`chore: post-v0.1.0 hardening`. Keep commits focused and include verification in
the PR body. PRs should describe the change, list commands run, and call out any
operator-impacting behavior such as data, snapshot, or harvest changes.

Never push directly to `main`; push an explicit branch, for example
`git push origin docs/update-readme`.

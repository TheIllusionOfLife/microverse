# Repository Guidelines

## Project Structure & Module Organization

Microverse is a Python 3.12 package using a `src/` layout. Runtime code lives in
`src/microverse/`: `agents/`, `memory/`, `world/`, `ops/`, and `llm/` map to the
main runtime subsystems. Operator scripts live in `scripts/`. Tests live in
`tests/` and mirror behavior, for example `tests/test_harvester.py`.

Generated state stays untracked: `data/`, `harvest/`, logs, and virtualenvs.

## Build, Test, and Development Commands

- `uv sync` installs project and development dependencies.
- `uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42` runs a fast
  bounded smoke simulation.
- `uv run python -m microverse.ops.metrics --report --db data/metrics.sqlite`
  prints metrics.
- `uv run python scripts/render_dashboard.py --data data --harvest harvest`
  renders the dashboard.
- `uv run ruff check` runs lint checks.
- `uv run ruff format --check` verifies formatting.
- `uv run mypy src/microverse` runs static type checks.
- `uv run pip-audit` checks locked dependencies for known vulnerabilities.
- `uv run pytest -q -m 'not integration'` runs the default test suite.
- `uv build` verifies the package build.

Use `uv run pytest -q -m integration` only when Ollama is running locally with
`gemma4:e4b` pulled.

## Coding Style & Naming Conventions

Use Ruff for linting and formatting. The project targets Python 3.12,
100-character lines, and 4-space indentation. Prefer typed, dependency-light
modules. Use `snake_case` for functions/modules, `PascalCase` for classes, and
`UPPER_SNAKE_CASE` for constants.

Keep behavior simple and local-first. All model calls should go through
`microverse.llm.ollama_client`, and shared runtime constants belong in
`microverse.config`.

## Testing Guidelines

Tests use `pytest`. Name files `test_*.py` and write focused tests around
observable behavior. For fixes, reproduce the issue with a failing test first,
then implement the smallest change that makes it pass. Keep live Ollama tests
marked `integration` so the default suite remains fast and offline.

## Commit & Pull Request Guidelines

Recent history uses short summaries such as `docs: refresh README`,
`refactor: simplify codebase`, and `chore: post-v0.1.0 hardening`. Keep commits
focused. PRs should describe the change, list commands run, and call out
operator-impacting behavior such as data, snapshot, or harvest changes.

Never push directly to `main`; push an explicit branch, for example
`git push origin docs/update-readme`.

## Security & Architecture Notes

See `SECURITY.md` before touching generated content, dependency handling, or
runtime data paths. Architecture decisions live in `docs/adr/`; update or add an
ADR when changing core runtime invariants such as model routing, persistence, or
remote services.

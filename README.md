# Microverse Battery

A long-running multi-agent simulation inspired by *Rick and Morty*'s Microverse Battery. Inhabitant agents live in a fictional world, produce artifacts (essays, code, data, designs), and a single out-of-world Harvester ferries the best artifacts to `harvest/inbox/` for the user.

Built to run autonomously for weeks on local Apple Silicon at zero marginal cost using **only** `gemma4:e4b` via local Ollama.

## Status

Phase 0 (bootstrap) in progress. See `TODO.md` for the phase ladder and `PROMPT.md` for the build-time ralph-loop driver.

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

See the implementation plan: `~/.claude/plans/use-gemma4-e4b-via-ollama-staged-umbrella.md`.

## License

See `LICENSE`.

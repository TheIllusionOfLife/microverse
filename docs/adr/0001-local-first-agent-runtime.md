# ADR 0001: Local-First Agent Runtime

## Status

Accepted.

## Context

Microverse is a long-running multi-agent simulation intended to run on a local
Apple Silicon machine with no marginal model cost. The runtime needs durable
state, bounded prompts, inspectable outputs, and failure recovery without
introducing hosted infrastructure.

## Decision

- Use one local Ollama model, `gemma4:e4b`, through
  `microverse.llm.ollama_client`.
- Store committed events and metrics in SQLite with WAL enabled.
- Use SQLite FTS5 for semantic recall instead of an embedding service.
- Write accepted artifacts to the local `harvest/` tree with an auditable
  manifest.
- Render the dashboard as static HTML with no external assets or JavaScript
  framework.
- Keep integration and soak tests operator-run because they require live Ollama
  and long wall-clock time.

## Consequences

The system remains easy to run and inspect on one laptop, and CI can stay fast
by excluding live-model integration tests. The tradeoff is that model quality,
throughput, and long-soak validation depend on the operator's local Ollama
setup. Future changes that add hosted models, embedding services, or remote
storage should introduce a new ADR and update the security policy.

## Subsequent decisions affecting this ADR

- **ADR 0004 Decision 5 (v0.3)**: the production model swapped from
  `gemma4:e4b` (9.6 GB) to `gemma4:26b` (17 GB). Same model family —
  prompt format, tokenizer, and thinking-channel behavior are
  preserved; only parameter count and required RAM change. The
  `gemma4:e4b` tier remains supported as a low-RAM fallback documented
  in README.md, but `gemma4:26b` is the production default through
  `v1.0.0` and the soak that ships with the WRITEUP.
- **ADR 0006 (v1.0)**: a second model, `nomic-embed-text`, is pulled
  into the runtime as a **measurement-only** embedding model for gate
  8 (scene semantic dependence). It is never called from
  `agent.think()`; the single-model invariant for the agent action loop
  remains intact. Operators who skip the `ollama pull nomic-embed-text`
  step see gate 8 degrade to "unavailable" rather than a crash.

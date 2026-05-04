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

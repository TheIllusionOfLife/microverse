# ADR 0007: From Workshop to Civilization (post-v1.0 vision)

## Status

Proposed — **planning only, no implementation in this ADR**. Targets a
`v2.0` arc that runs *after* the v1.0.0-rc1 line is merged and a re-soak
with the v1.0 fixes (snapshot circuit breaker, revived diversity lever)
is complete. This document records the design intent so it is reviewable
and sequenced before any code is written. It supersedes nothing; it
extends the original project vision that ADRs 0003–0006 approached one
harness layer at a time.

> **Phase 1 halt read (2026-05-31): HALTED.** The falsifiable gate defined below was run after
> Phase 1 shipped (#43); neither Gate 1 (peer-reference) nor Gate 3 (verb monoculture) moved. See
> [ADR 0008](0008-phase1-identity-halt-read.md). Re-diagnose the substrate thesis before Phase 2.

## Context

### The original vision, restated

The project's north star was never "a program that emits scrolls." It
was **a society of small local agents that evolves a culture of its own
and, in doing so, produces things more interesting than any single agent
(or prompt) could** — running for weeks, at zero marginal cost, on one
laptop. PROMPT.md framed this as a living world; ADR 0005 named the gap
between "a society of agents" and "agents that talk past each other."

v1.0 closed an important part of that gap. The **soak-v1-2** evidence
(5.5 days, 20,102 events, two residents Aki + Cy, `gemma4:26b`, seed 38)
proved a *collaboration engine*: scenes where turns demonstrably read
each other (Gate 8, cosine 0.762 / 0.757), ~1,039 lines where one agent extends
the other's idea by name, and multi-day operational durability. That is
real and load-bearing.

But it is **collaboration, not civilization**. The same data shows why
nothing accretes into a society with a history:

1. **Work is discarded as fast as it is made.** `soak-v1-2` logged
   **1,845 `workshop.recycle` events**. A civilization is a *ratchet* —
   it keeps and builds on what came before. This world composts
   everything.
2. **Memory is a sliding retrieval window** (`build_context` caps
   `recent_episodic` at ≤1500 tok). There is no durable self. The agent
   tomorrow does not *remember being* itself today beyond the window.
   Culture cannot accumulate on amnesia.
3. **Static two-resident population.** No immigration fired this run
   (`Stranger` count 0), no turnover, no variation entering the system.
4. **No scarcity.** Nothing is finite, so nothing *forces* specialization,
   trade, property, or institutions. `Trader` ranks artifacts but the
   agents participate in no economy.
5. **The longest shared intention is ~3 ticks** (a scene). No project
   spans a "generation"; no structure is built that constrains future
   action.

The two still-failing structural gates are *symptoms* of this substrate,
not independent bugs:

- **Gate 3 (verb monoculture, 96.2% worst window).** Identical agents,
  with no persistent identity, no stakes, and no accumulating world,
  converge on the same attractor (spring / soil / light). ADR 0002
  named the model-level pull; the deeper cause is that nothing in the
  *harness* rewards divergence.
- **Gate 1 (lexical peer-reference, 0.073).** Agents engage by paraphrase
  and idea-extension, but have no durable social graph to reference
  ("the favor Cy repaid in the drought"), so cross-reference stays
  shallow and local.

Fix the substrate and both gates should move as side effects. This ADR
proposes that substrate.

## Decision

### Thesis: civilization-as-scaffold, not civilization-as-emergent-genius

The single-model invariant holds (`config.MODEL = "gemma4:26b"`, the only
entry point for `agent.think()`; embeddings remain measurement-only per
ADR 0006). A small local model **cannot plan a civilization on raw
intelligence** — its planning horizon is short. Therefore the long-term
coherence must live in **structure**, not in the model: scarcity,
persistent ledgers, and long-horizon goals carry continuity; the model
only makes good *local* decisions inside that scaffold. This is also how
real institutions work — no individual holds the whole plan; the
structure does. It is the only version of "civilization" that is
tractable on one laptop with one small model.

### Reframe the optimization target

v1.0 optimizes for **artifacts** (the Harvester grabs the best scrolls).
v2.0 optimizes for **accumulated structure and divergence**. The
interesting object is no longer the scroll; it is *the society that
produced it and how it changed*. The Harvester's mandate widens from
"harvest outputs" to "harvest history" — the founding, the drought, the
schism, the invention.

### The five pillars

Each pillar is an **evolution of an existing primitive**, not greenfield
work. None is implemented here; this is the design intent a later plan
will sequence (it commits no contract — see "What this ADR explicitly
does not do" below).

**Pillar 1 — Durable identity + social graph.** *(highest leverage; the
unlock for everything else.)*
Each agent gains a small persistent self-record that survives across
ticks and is fed back into `WorldContext`: a few stable traits, current
beliefs/commitments, and a **relationship ledger** (who helped whom, who
owes whom, reputations, agreements kept or broken). Without a persistent
self, nothing can stick. With it, "Cy is the careful one who repaid the
favor in the drought" becomes a fact the world remembers — the raw
material of culture. *Builds on:* `SemanticMemory` (FTS5) for storage,
`build_context` for the feedback path. *Invariant to preserve:* the
Path-3 self-redaction rule (Gate 5) — an agent's own *fragments* stay
redacted from its WIP input; the *self-record* is explicit identity
state, a separate carve-out (mirrors the turn-3 scene-context carve-out
in ADR 0006).

**Pillar 2 — Scarcity + a real economy.** *(the engine of specialization
and institutions.)*
Introduce finite resources (flax, clay, ink, grain) that must be
gathered, stored, and traded. Make agents *unequally good* at different
verbs so trade becomes rational (comparative advantage). Promote
`Trader` from end-of-flush ranker to an actual **market maker** that
clears exchanges. `soul_tokens` (already the scheduler weight) can become
the economic currency, closing the loop between economic success and
"voice" in the world. *Builds on:* `Trader`, `WeightedScheduler`,
`soul_tokens`. *Invariant to preserve:* `Trader` stays non-scheduled; it
acts at clearing time, not as a thinking agent.

**Pillar 3 — An accumulation ratchet.** *(the literal definition of
civilization.)*
Replace recycle-heavy WIPs with **durable, append-only structures that
persist and grow**: a village ledger, a *constructed* (not merely
compressed) body of lore, a map of built structures, and a "technique
tree" where a discovered method is permanent and shared. Each generation
builds on the last. *Builds on:* `Elder` (today: lore compression with a
Jaccard drift guard) evolves toward lore *construction* and curation;
`WorkshopProjection` gains durable structure types alongside transient
WIPs. *Invariant to preserve:* WAL remains the durability boundary;
snapshots stay cold backups.

**Pillar 4 — Population dynamics.** *(variation + inheritance.)*
Use immigration aggressively: `Stranger`s with *different priors* inject
the variation that breaks monoculture. Tie arrival/departure to
prosperity (good harvest → newcomers; famine → exodus). Generational
turnover lets norms be inherited *and mutated* — cultural evolution.
*Builds on:* the `Watchdog` → `Stranger` mechanism (already the only
authority allowed to register an immigrant mid-run, capped by
`max_strangers`). *Invariant to preserve:* the Watchdog stays the sole
scheduler-mutation authority; replay determinism via logged authorship
(ADR 0006) extends to logged arrivals/departures.

**Pillar 5 — Long-horizon goals + lightweight governance.** *(institutions.)*
Give the society multi-tick projects that need coordination across many
scenes ("the granary needs 200 contributions of the right kind"). Add a
thin **propose → assent/dissent → record** loop so decisions become
**norms** that then constrain future behavior. Institutions are nothing
more than remembered agreements with teeth. *Builds on:* the scene
contract (ADR 0006) generalizes from a 3-turn micro-loop to a
named, long-running shared project with a persistent goal record.

## Phased roadmap (sequencing, not implementation)

Ordered by leverage and dependency. Each phase ends with a smoke + a
gate read before the next begins; later phases assume earlier ones green.

| Phase | Version | Pillar | Why this order |
|---|---|---|---|
| 1 | `v1.1` | Persistent identity + relationship ledger | Cheapest change, biggest unlock; breaks the amnesia and should move Gates 1 & 3 directly. |
| 2 | `v1.2` | Scarcity + comparative advantage + Trader market | Creates the pressure that *forces* specialization. |
| 3 | `v1.3` | Durable accumulating structures (ledger, technique tree, built map) | The ratchet — replaces `workshop.recycle` churn. |
| 4 | `v1.4` | Aggressive immigration + turnover | Variation and inheritance once there is something to inherit. |
| 5 | `v2.0` | Multi-tick projects + norm-recording governance | Institutions — only meaningful once 1–4 exist. |

Phase 1 is the gate: if persistent identity does **not** move Gate 1 or
Gate 3 in a bounded smoke, re-examine the substrate thesis before
investing in Phases 2–5.

## New measurement (how we know a civilization is forming)

The current gates measure a *collaboration*; a *society* needs new
proxies. These replace shape-of-fragment checks with shape-of-society
checks:

1. **Specialization (divergence, not concentration).** Verb/role
   distributions across agents should *diverge* over time (different
   agents specialize), inverting the Gate-3 framing from "no single
   agent concentrates" to "agents concentrate on *different* things."
2. **Social-graph structure.** The relationship ledger should develop
   non-uniform structure (reputations, recurring debts), measurable as
   entropy/centralization moving away from "everyone equal."
3. **Lore growth.** Constructed lore should *grow* in size and
   reference depth over time — accumulation, not steady-state
   compression.
4. **Built environment.** The durable-structure map should grow and be
   referenced by later actions (the past constrains the present).
5. **Inheritance.** After turnover, do newcomers adopt (and mutate)
   recorded norms, rather than resetting to base priors?

A v2.0 acceptance soak passes when these trend positively over a
multi-day run while the v1.0 operational invariants (no `snapshot_fail`
storms, `thinking_leak == 0`, WAL recoverability) still hold.

## Consequences

- **Positive:** directly targets the original vision; reuses existing
  primitives (`Trader`, `Elder`, `Stranger`, `SemanticMemory`,
  `soul_tokens`, scene contract) rather than rebuilding; the failing
  structural gates are addressed at their root rather than by tuning
  proxies.
- **Negative / risk:** large scope; tractability rests on the
  scaffold thesis. If a small model cannot make coherent *local*
  decisions inside a richer economic/social state, the scaffold produces
  noise, not culture. Mitigation: Phase 1 is a cheap, falsifiable test of
  the thesis before committing to Phases 2–5.
- **Constraint preserved:** single-model invariant for `agent.think()`;
  embeddings measurement-only; WAL as durability boundary; Watchdog as
  sole scheduler-mutation authority; Path-3 self-redaction.

## Halt criteria (pre-baked)

- **Phase 1 smoke does not move Gate 1 or Gate 3** → the substrate thesis
  is wrong or incomplete; stop and re-diagnose before Phase 2. Do not
  paper over it by tuning the identity prompt.
- **Any phase regresses a v1.0 operational invariant** (snapshot storm,
  thinking leak, WAL non-recovery) → fix before proceeding;
  non-negotiable.
- **Scaffold produces incoherent local decisions at richer state** →
  reduce state richness to the largest the model handles coherently;
  report the ceiling honestly in the writeup rather than claiming
  emergence that is not there.

## What this ADR explicitly does not do

It writes **no code**, changes **no config**, and commits **no contract**.
It is the reviewable design intent. The implementing plan (with files,
tests, and per-phase acceptance, in the shape of the v1.0 roadmap) is a
separate document authored only after this direction is accepted.

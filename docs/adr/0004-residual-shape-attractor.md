# ADR 0004: Closing the Residual Artifact-Shape Attractor (v0.3)

## Status

Proposed. Targets v0.3. Promotion to Accepted is gated on the v0.3
acceptance soaks (Soak A structural-only, Soak B structural + model
swap) per the halt criteria below.

## Context

ADR 0002 closed the message-level "Layer-patch" pattern. ADR 0003
shipped a shared workshop substrate so artifacts can accrete across
many ticks. The v0.2 acceptance soak (`data/soak-24h-pr-v02`, 21,429
events, seed 38) confirmed every architectural contract in ADR 0003.
It also surfaced three residual pathologies that the substrate
decisions did *not* address:

1. **Shape attractor migrated through the new verb.** Aki contribute
   share rose to 86.7 %. Completed-WIP fragments held flat at
   ~15 words / ~91 chars across the full 24h. Single-sentence object
   descriptions ride through `contribute` the same way they rode
   through `craft` in v0.1.x.
2. **3-WIP set bottoms out after one hour.** All three configured
   WIPs reach `COMPLETE_FRAGMENT_FLOOR = 8` and lock. The projection's
   `_apply` then silently drops every subsequent contribute targeting
   a complete WIP — 11,054 of 13,348 workshop contributes (83 %)
   went to that black hole.
3. **Trader v2 cutoff conflates rule-based WIP scores with LLM-rated
   artifact scores.** The same WIP at score 0.466 is accepted when
   alone in its flush, rejected when grouped with higher-scored
   artifacts. Only 3 of 27 manifest WIP entries were ever written.

Existence proof: one accepted WIP (workshop.loom, 8 fragments, two
contributors) showed genuine cross-agent dialogue with multi-tick
continuity. The model can produce the goal output when state and
selection align. A 30-sample late-soak inspection (last 4h) found
36 % explicit peer engagement in Aki's contribute payloads, 60 %
standalone object descriptions, 4 % ambiguous. The model is
conversationally capable; the pipeline is starving the capability.

ADR 0003 explicitly deferred a model swap to gemma4:26b. v0.3 takes
that move now, paired with structural fixes, structured so a
sequential A/B soak isolates each contribution.

## What this ADR does not claim

This ADR is not "free of prompt patches." Fixes 2 and 3 do disclose
validator and projection state to the persona prompt. The honest
framing: **v0.3 is not another action-distribution coercion layer.**
It does not add prompts that ask the model to vary verbs, vary themes,
or be interesting. It adds structural fixes that close feedback-loop
pathologies and enforce schema invariants. Two of those structural
changes happen to be visible to the persona — they disclose
pipeline-enforced invariants the model would otherwise collide with
blindly. Codex review confirmed this distinction is defensible
provided the wording stays factual and does not smuggle in taste
language.

## Decision

Four structural fixes plus a single-model swap. Fixes land first,
each with its own TDD slice and 30-min smoke; the model swap is the
final config flip so the soak A/B can isolate the model effect.

### Decision 1 — WIP terminal lifecycle with bounded recycle

The WIP state machine gains a `harvest_pending` state between
`complete` and the next `forming`. Every transition is an explicit
episodic event so ADR 0003's invariant (episodic log is the sole
authoritative write, projection is a pure read-model) is preserved
end-to-end:

- `workshop.complete{wip, completed_ts}` on entry to `harvest_pending`.
- `workshop.recycle{wip, reason=accepted|attempts_exceeded|timeout,
  dropped_fragments=<n>}` on transition back to `forming`.
- Contribute events that arrive while `phase==harvest_pending` are
  hard-folded at the validator (Decision 3 below). If the log
  contains a pre-recycle contribute (e.g. interrupted process), the
  projection drops it on apply; rebuild order is single-threaded
  in-memory state machine, log in `ts` order, no out-of-band side
  channels. Restart determinism is bit-for-bit: replay from any
  point yields the same projection state.

Recycle rules:
- On a flush that **accepts** the WIP, the Harvester emits
  `workshop.recycle{reason=accepted}`.
- On a flush that **rejects** the WIP, the Harvester emits
  `workshop.harvest_attempt`. The projection-derived `attempts`
  counter is `count(harvest_attempt events between last complete
  and now)`. Once `attempts >= MAX_HARVEST_ATTEMPTS` (default 3)
  OR `now - completed_ts > HARVEST_PENDING_TIMEOUT_S` (default
  1800s), the Harvester emits `workshop.recycle{reason=
  attempts_exceeded|timeout}` and the rejected fragments are
  dropped (operator does not want a perpetual rejected backlog).

Capacity invariant: `WorkshopProjection.open_slots()` returns the
count of WIPs in `forming|developing`. Steady state guarantees
`open_slots() >= 3` (the configured WIP count). Agent-spawnable WIPs
are deferred to v0.4 unless this invariant proves insufficient.

The kill-safety drill (`scripts/verify_kill_drill.py`) is extended
to assert recycle-boundary equivalence after WAL replay.

### Decision 2 — Minimum fragment length on contribute

`_validate_contribute` enforces `MIN_FRAGMENT_CHARS = 120` (~25
words). A `contribute` whose `artifact` strips to fewer than 120
characters is hard-folded to `rest` with the
`contribute_too_short` metric per agent. Persona templates gain one
disclosure sentence: "When you weave a fragment, write at least a
few sentences (~25 words) so the next contributor can build on more
than a noun phrase." This is constraint disclosure, not coercion.

Anti-padding observables (Codex I4):
- `repeat_4gram_rate_per_wip`: ratio of 4-grams duplicated within a
  single WIP across fragments. High = padding attractor.
- `peer_reference_rate`: fraction of contribute fragments containing
  any peer name as a whole word.
- `within_wip_lexical_novelty`: mean Jaccard distance between each
  fragment and the prior fragment in the same WIP, drop-stopwords,
  min-len=4.

These observables are the load-bearing guard against
`MIN_FRAGMENT_CHARS` becoming a new padding attractor — the gate is
not "median word count ≥ 25" alone; it is the composite (≥ 25 words
AND repeat-4gram < 0.15 AND peer-reference ≥ 30 %).

### Decision 3 — Hard fold on contribute to a complete WIP

`parse_action` extends with `workshop: WorkshopProjection | None =
None` (read-only, no commit-surface implications). When a
`contribute` action's `contribute_to` references a WIP currently in
`complete|harvest_pending`, the validator folds to `rest` and bumps
`contribute_to_complete_wip`. Back-compat: tests passing
`workshop=None` skip the lookup.

Codex review noted this is a borderline seam — lifecycle admission
sits closer to the validator than the projection in v0.3. The
read-only access keeps it from creating a second commit surface, so
ADR 0003's substrate contract is preserved; the trade is a small
amount of layer-crossing in exchange for enforcement at the earliest
possible point in the pipeline. A persona-only marker would leave
gate 4 (`contribute_to_complete_wip < 1 %`) structurally
unenforceable.

`run.py` constructs the projection (already true post-PR #32) and
threads it through to each agent's parse path. The Stranger persona
gains a workshop block (the affordance is village-wide).

### Decision 4 — Separate Trader v2 cutoffs with contributor subfloor

The Harvester's `_percentile_cutoff` splits by candidate kind:

- **Artifacts**: keep the existing p70 percentile cutoff.
- **WIPs**: `WIP_ACCEPTANCE_FLOOR = 0.55` absolute floor **AND** a
  contributor subfloor — `len(contributors) < 2` is rejected
  regardless of score, with metric `wip_contributor_subfloor`.

Math (Codex-corrected): a WIP with contributor=2 scores `2/3 ≈
0.667` on that component. The minimum aggregate for novelty=1 +
length=0 + contributor=2 is `(0.667 + 1.0 + 0.0) / 3 ≈ 0.556`,
which already clears 0.55 by structure. After Decision 2 forces
length ≥ 120 chars × 8 fragments ≈ 960 chars (length component
~0.96 under the existing `chars/1000` scorer), realistic
2-contributor WIPs land far above the floor. **The contributor
subfloor is load-bearing; `WIP_ACCEPTANCE_FLOOR=0.55` is
defence-in-depth** against 1-contributor edge cases that slip past
the subfloor (e.g. via projection rebuild glitch).

The actual goal is *cross-agent dialogue*, not "long fragments." A
solo padded WIP clearing 0.55 by length alone is not the artifact
we want to harvest. Requiring ≥ 2 contributors is the cleanest
structural expression of the goal.

### Decision 5 — Single-model swap to gemma4:26b

`MODEL = "gemma4:26b"` (17 GB on disk, already pulled locally per
`ollama list` on 2026-05-16). Same model family as the current
`gemma4:e4b` (9.6 GB) — same prompt format, tokenizer, and
thinking-channel behavior; only parameter count changes. Projected
inference latency 3-5× e4b on Apple Silicon at Q4 quantisation
(~200 events/hr, ~4,800 events over a 24h soak, above the
manifest-growth floor).

Phase 0 qualification smoke (`MODEL=gemma4:26b`, 60 ticks, seed 38)
verifies (a) valid Action JSON every call, (b) per-tick latency p95
< 60s, (c) Trader `rank()` still returns a JSON array (the existing
`_RANK_SCHEMA` was empirically tuned for e4b under Ollama structured
output — `agents/trader.py:51-52` flags this is a known regression
surface), (d) no immediate action-share fingerprint divergence from
e4b baseline. If 26b fails qualification, Soak B re-runs on e4b
(degraded outcome, not a no-go; v0.3 still ships if Soak A passes
its gates).

The single-model invariant (ADR 0002) is preserved. No router, no
runtime fallback.

## Consequences

- Multi-tick artifact accretion now has a working terminal lifecycle.
  The projection no longer silently drops contributes against locked
  WIPs.
- WAL kill-safety is preserved by construction — every lifecycle
  transition writes an explicit episodic event; the projection
  remains a pure read-model.
- Pipeline-enforced invariants are disclosed to the persona where
  the model would otherwise hit them blindly. The disclosure is
  factual, not taste-based.
- Trader v2 is now a structural filter on the goal expression
  (≥ 2 contributors) rather than a vibe-rated cutoff.
- Single-model invariant (ADR 0002) preserved; only the identity
  changes.

## Acceptance criteria (v0.3 soaks)

Soak A is mandatory before Soak B. Soak A measures the structural-
only effect on `gemma4:e4b`; Soak B layers the model swap on top.

- **Soak A**: structural fixes only, `gemma4:e4b`, seed 38, 24h,
  `data/soak-24h-v03-e4b`.
- **Soak B**: structural fixes + `gemma4:26b` (or e4b if Phase 0
  smoke disqualified 26b), seed 38, 24h,
  `data/soak-24h-v03-26b`.

Gates (must hold on Soak B; Soak A measures structural-only
contribution to gates 2/4/6/7):

1. **Fragment shape composite**: (a) median completed-WIP fragment
   word count ≥ 25, (b) repeat-4gram rate < 0.15, (c) peer-reference
   rate ≥ 30 %.
2. **WIP throughput** — ≥ 5 completed-and-accepted WIPs per hour
   (v0.2: 0.125 accepted/hr).
3. **Verb concentration** — no agent's top action exceeds 70 %
   share sustained for 2 consecutive hours (v0.2 Aki: 86.7 %).
4. **Pipeline efficiency** — `contribute_to_complete_wip` < 1 % of
   total contribute attempts (v0.2: 83 %).
5. **Path-3 invariant** — `workshop_view_self_redactions` non-zero,
   structural-leak sweep clean.
6. **Acceptance throughput** — Harvester accepts ≥ 50 % of
   completed WIPs that pass the contributor subfloor (v0.2: 11 %).
7. **Capacity invariant** — `WorkshopProjection.open_slots() ≥ 3`
   throughout the soak (sampled every 5 min).

Halt criteria:
- Soak A fails gates 2, 4, 6, or 7 → structural fixes have a bug.
  Diagnose and re-soak before Soak B.
- Soak A passes 2/4/6/7 AND Soak B passes 1 → ship v0.3.
- Soak A passes 2/4/6/7 but Soak B fails 1 → diagnose first. Do
  NOT Layer J. If diagnosis indicates the shape attractor is
  resilient against the structural + model levers, write ADR 0005
  proposing a fundamentally different harness shape. Naming a
  specific candidate (e.g. Elder digest) is premature until
  diagnosis exists.
- Both soaks fail 1 → same as Soak B failing alone.

## Empirical risks (acknowledged up front)

Codex Q5:

- **Recycle lifecycle becomes the new correctness trap.** Reopen/close
  ordering, duplicate harvest, restart divergence are live failure
  modes. Decision 1's "explicit episodic events for every transition"
  and the extended kill-safety drill are the mitigations; if either
  slips, restart can diverge.
- **`MIN_FRAGMENT_CHARS=120` becomes a padding attractor.** Padded
  boilerplate fragments and low semantic gain could replace the
  word-count attractor. The anti-padding observables (Decision 2)
  and the composite gate 1 are the load-bearing guard. If gate 1
  fails because of high repeat-4gram rate, the fix is structural
  (Trader scoring) not another prompt.
- **Soak attribution becomes muddy.** If 26b changes fragment shape,
  the win could come from model capacity, persona disclosures,
  recycle semantics, or threshold geometry. Soak A → Soak B
  isolation is load-bearing for attribution; per-phase 30-min
  smokes (`smoke-phase-N.log`) carry lever-level attribution, not
  the 24h pair.

## Out of scope (v0.4+)

- Agent-spawnable WIPs (only if capacity invariant breaks at 3).
- Persona evolution via Elder lore.
- Trader v2 model for artifacts (still LLM-rated).
- Multi-model routing within a soak.

## Reference data (filled at promotion to Accepted)

- Phase 0 qualification smoke result (`smoke-phase-0-26b.log` or
  fallback notice).
- Per-phase smoke results (`smoke-phase-1.log` through
  `smoke-phase-4.log`).
- Soak A and Soak B data dirs and gate evaluations from the
  extended `scripts/spike_workshop_measure.py`.

# ADR 0005: Harness Shape for Social Artifacts (v0.4)

## Status

Proposed. Targets v0.4. Triggered by the v0.3 acceptance soaks (Soak A
`gemma4:e4b` 19,878 events, Soak B `gemma4:26b` 14,113 events, seed 38,
both 24h) per ADR 0004's halt-criterion clause: *"If diagnosis indicates
the shape attractor is resilient against the structural + model levers,
write ADR 0005 proposing a fundamentally different harness shape."*

## Context

ADR 0004 attempted to close v0.2's residual artifact-shape attractor
via four structural fixes plus a single-model swap from `gemma4:e4b`
to `gemma4:26b`. The 24h sequential soaks measured each gate against
the acceptance thresholds:

| Gate | Threshold | Soak A (e4b) | Soak B (26b) | Verdict |
|------|-----------|---------------|---------------|---------|
| 1 fragment-shape composite | peer-reference rate ≥ 0.30 | **0.014** | **0.022** | Both fail by ~14x |
| 2 WIP throughput | ≥ 5/hr accepted | 3.7/hr | 29.3/hr | A fails, B passes |
| 3 verb concentration | ≤ 70% sustained | 86.7% (Cy) | 57.4% (Stranger) | A fails, B passes |
| 4 pipeline efficiency | fold rate < 1% | 13.2% | **61.6%** | Both fail; **26b makes it worse** |
| 5 path-3 invariant | non-zero redactions | 121,451 | 23,007 | Both pass |
| 6 acceptance throughput | accept rate ≥ 50% eligible | 100% (1/1) | 100% (1/1) | Both pass trivially |
| 7 capacity invariant | open_slots ≥ 3 always | 285 violations | 240 violations | Both fail |

Three observations from the data drive this ADR:

1. **The shape attractor is not materially improved by this
   model-family size increase.** Median fragment word count rose
   33 → 47 with the larger model, but peer-reference rate stayed
   essentially flat (0.014 → 0.022) — neither value reaches the
   0.30 floor that distinguishes "long fragment" from "social
   fragment." Both models are in the same family (same prompt format,
   tokenizer, training lineage); the result generalizes to that
   family, not necessarily to a cross-family swap. The model is not
   failing to write words; under this harness it is failing to
   engage with peers.
2. **Gate 4 inverted under arrival-rate saturation, not longer
   dwell.** v0.3's hard fold on contributes targeting `complete`
   WIPs is firing constantly (3,468 of 5,627 contributes folded
   under 26b). 26b's *per-WIP* dwell time in `complete` is actually
   shorter than e4b's (Soak B accepts a WIP every ~123 s wall-time
   vs Soak A's ~970 s), but its *arrival* rate fills slots faster
   than the 50-tick harvester can clear them. With 3 configured WIPs
   and ~2.7 accepted WIPs per 50-tick flush window, the slots run
   near-saturated; any tick that lands on a `complete` slot is a
   structural near-miss for the validator to fold.
3. **The recycle pipeline cannot keep up *at saturation*.** Gate 7's
   240 violations over 288 5-minute samples is consistent with the
   above: under saturated arrival, slots spend most of their time
   in `complete` waiting for the next flush. The 5-minute sampling
   window also aliases with the flush cadence (~50 ticks ≈ 3-5 min
   under Soak B throughput), so the raw count is a sampling
   artifact in part — the underlying condition is saturation, not
   instrument noise.

Observations 2 and 3 are *consequences of v0.3 succeeding* in part:
the workshop affordance is being used heavily; the bottleneck moved
from "model never uses workshop" (v0.2) to "model fills workshop
faster than the recycle pipeline drains it" (v0.3). That is real
progress on gates 2 and 3.

Observation 1 is the residual risk ADR 0004 explicitly named for
the gemma4 family. A cross-family model swap (different lineage,
different prompt format) is *not* ruled out by this evidence; it is
deferred as a separate variable from the harness shape question.

## What this ADR does not claim

This ADR does not claim the v0.3 work was wasted. The recycle
lifecycle, the validator hard-fold, the cutoff split, and the
capacity-invariant gate are all keepers; they reveal the bottleneck
rather than create it. ADR 0005 proposes a harness-shape change
*built on top of* the v0.3 substrate, not in place of it.

ADR 0005 also does not commit to a single redesign. The available
levers fall on a spectrum from cheap-and-incremental to deep-and-
expensive; the v0.4 plan must measure the cheaper levers before
spending on the expensive one.

## Decision

Three levers, in increasing intervention depth. Decisions 1-2 land
in v0.3.1 (no model change, no acceptance soak); Decision 3 is the
v0.4 shape change with its own acceptance soak.

### Decision 1 — Workshop view hides `complete` WIPs from the persona (v0.3.1)

`microverse/memory/__init__.py::_build_workshop_view` filters the WIP
list passed to `persona_*.j2` to only those in `forming` or
`developing`. Complete WIPs are not surfaced in the prompt at all.
Their presence in the projection is unchanged (the harvester still
sees them; the validator still hard-folds on the off chance the
model targets one by name from memory).

Hypothesis: the model targets complete WIPs because it sees them in
the prompt and the prompt does not reward novelty over recency. The
hard fold catches the symptom; hiding the affordance removes the
cause.

Predicted effect on gates: gate 4 fold rate drops from 61.6% / 13.2%
toward the persona-disclosure-only baseline (Phase-2+3 smoke saw
0 folds under e4b before the soak load pattern emerged). Gate 7
violations drop because the model spends more attention on open WIPs.

Test slice: a 200-tick smoke against the v0.3 e4b baseline data dir
should show fold rate < 5% with this lever alone.

### Decision 2 — Harvester flush is opportunistic on `complete` transitions (v0.3.1)

Currently `harvester.flush()` fires every 50 ticks (`run.py:_maybe_harvest`).
With Soak B's 29.3 WIPs/hr accepted and 3 configured slots, the
arrival rate is ~2.7 accepted WIPs per 50-tick flush window — close
to full slot capacity. Slots sit in `complete` waiting for the next
scheduled flush instead of recycling on transition.

Replace the pure-timer with a transition-triggered flush: `flush()`
runs whenever the projection observes a WIP transition into
`complete`. The timer is retained as a fallback ceiling (every 50
ticks) so artifact-only flushes still happen in windows with no WIP
completions.

This is a one-line change in `run.py` plus a hook in
`workshop._apply_contribute` that signals the run loop. No new
threading; the run loop drains the signal on the next tick boundary.

Predicted effect: gate 7 capacity-invariant violations go to ~0.

### Decision 3 — Multi-turn scene as the unit-of-artifact (v0.4)

If Decisions 1+2 leave gate 1 unmoved (peer-reference rate < 0.30),
the harness shape itself is the bottleneck. The current contract is
"one tick = one Action = one optional fragment." A single Action
cannot structurally guarantee social engagement because there is no
prior turn to reference. Coercing reference is the same Layer-J
trap ADR 0004 ruled out.

The v0.4 proposal: replace the single-action tick for the workshop
path with a **scene**. A scene as the *minimum probe shape* is a
3-turn sequence ("proposal → response → closure"); 3 is the smallest
N that exercises both forward reference (turn 2 reading turn 1) and
multi-turn closure (turn 3 reading turns 1+2). It is the probe, not
the truth — v0.5 can revisit length once scenes-at-all are validated.

Minimum probe structure:

- Turn 1: agent A proposes a fragment, addressed to agent B.
- Turn 2: agent B's response reads agent A's turn before composing
  its own fragment. The prompt context for B's turn includes A's
  fragment as the most-recent input.
- Turn 3: a third agent (or A again) closes with a fragment that
  reads both prior turns.

The harvester sees the scene as a 3-fragment WIP entry. Peer-reference
rate becomes a structural property: every turn after the first sits
inside a context that exposes the prior turn's author and text. The
model is not being told "reference the peer"; the prompt's input is
the peer's words. Peer engagement becomes the path of least
resistance, not the harder path.

This is *not* an instructional change to the existing persona. It is
a control-flow change in `run.py`: the workshop affordance, when
selected, runs a 3-turn micro-scheduler instead of returning to the
top-level tick. Other action verbs (craft, study, speak, rest) keep
the v0.3 single-tick semantics.

#### Scene event contract

The v0.4 implementation plan must resolve these contract points
before code lands. They are surfaced here because each touches an
ADR 0003 invariant that v0.4 inherits.

- **Author selection is logged, not recomputed.** A new `scene.open`
  event records `{scene_id, turn1_author, turn2_author, turn3_author}`
  at scene start. Subsequent `contribute` events for the scene carry
  `scene_id` and `turn_index` in their payload. The projection
  rebuild reads `scene.open` to know who *should* have contributed
  for each turn rather than inferring from scheduler state, which
  is mutable across replay.
- **Partial-scene atomicity.** If the run crashes after turn 1 or
  turn 2, the events that landed on disk are still valid contributes
  to the WIP. A `scene.abort{scene_id, last_turn, reason}` event is
  written on parse-fail or process-exit so replay can distinguish
  partial-scene from in-progress-scene. The WIP is treated as a
  shorter accreted artifact, not orphaned; the harvester's existing
  acceptance rules apply.
- **Self-history redaction for turn 3.** When turn 3's author is
  the same as turn 1's author, the prompt context for turn 3 must
  still redact the author's own prior tick to preserve the Path-3
  invariant. The redaction sees its *own* turn-1 fragment via the
  scene context, not via autobiographical replay; ADR 0003 considers
  this an explicit scene-scoped input, not an autobiographical
  channel.  This carve-out must be visible in the redaction code
  and tested.
- **Turn-2/3 ordering in the episodic log.** Scene turns may
  interleave with non-scene ticks from other agents (Strangers,
  weather events). The episodic log records the global order; the
  workshop projection groups by `scene_id` on apply so the WIP's
  fragment list reads in turn-index order regardless of intervening
  log entries.
- **Per-scene Trader scoring.** The scene is the unit; the Trader
  scores the 3-fragment WIP as one entity. Per-turn scoring is
  rejected — it would create a per-turn attractor inside the scene.

## Consequences

- v0.3.1 (Decisions 1-2) is a cheap test that may close gates 4 and
  7 without further redesign. If it does, gate 1 alone gates the
  v0.4 decision.
- v0.4 (Decision 3) changes the contract between the scheduler and
  the workshop affordance. The episodic log gains scene-bracketed
  events; restart determinism (ADR 0003) must be re-proven.
- ADR 0002's single-model invariant is preserved.
- ADR 0003's projection-as-pure-read-model invariant is preserved.
  Scenes are sequences of standard contribute events; the workshop
  projection does not need to know about scene structure.
- The persona prompt acquires no new instructional content. The
  social-engagement property emerges from prompt *inputs* (peer
  turns) rather than prompt *demands*.
- Gates 2, 3, 5, 6 from ADR 0004 remain valid measurements. Gates 1,
  4, 7 are the load-bearing targets for v0.3.1 and v0.4.

## Acceptance criteria

### v0.3.1 (Decisions 1+2)

Attribution trade-off: validating D1 and D2 together gives shipping
confidence but cannot attribute which lever moved which metric. Two
options, picked by operator preference:

- **Bundled** (one smoke, faster): D1+D2 in the same build; the
  smoke confirms the combined effect.
- **Sequential** (two smokes, attribution-clean): D1 alone first,
  then D2 layered on top. Costs a second smoke run.

Smokes:

1. **e4b baseline smoke** — 200 ticks against the v0.3 e4b
   baseline. Cheap correctness check:
   - `contribute_to_complete_wip` fold rate < 5% (vs 13.2% in Soak A).
   - `workshop_capacity_violations` zero across the smoke.
   - No new failure modes (json_fallback_rest, meta_leak_block,
     snapshot_fail bumps stay within Soak A baseline ±20%).

2. **26b pressure smoke** — 200 ticks on gemma4:26b, where the
   saturation regime from Soak B actually reproduces. The e4b
   smoke does not reach the arrival-rate saturation that Decisions
   1-2 are designed to fix; the 26b smoke is the one that exercises
   the failure mode.
   - Same metrics as the e4b smoke.
   - Plus a `wip_target_concentration` observable: distribution of
     `contribute_to` values across the 200 ticks. Hidden-complete
     rerouting (Decision 1 side-effect) shows up here as collapse
     onto the lowest-fragment-count open WIP. Acceptable if
     concentration < 0.7 on any single WIP.

If both smokes clear, v0.3.1 ships on its own.

### v0.4 (Decision 3)

Acceptance soak: structural fixes + Decision 1 + Decision 2 +
Decision 3, `gemma4:e4b`, seed 38, 24h, `data/soak-24h-v04`.

Gates (all must hold):

1. **Fragment-shape composite** — same composite as ADR 0004 gate 1.
   The peer-reference rate ≥ 0.30 subgate is the load-bearing one
   v0.4 is designed to close. Adds a `wip_target_concentration`
   subgate: distribution of `contribute_to` values has no single
   WIP exceeding 0.7 share (guards against hidden-complete rerouting
   from Decision 1).
2. **Scene completion rate** — ≥ 70% of initiated scenes complete
   all turns (no parse failure cascade).
3. **WIP throughput** — ≥ 5 completed-and-accepted WIPs per hour.
   (Scenes count as 1 WIP. Throughput per artist-hour may drop
   because a scene is 3 turns.)
4. **Pipeline efficiency** — same as ADR 0004 gate 4
   (`contribute_to_complete_wip < 1%` of total contribute attempts).
5. **Capacity invariant** — `WorkshopProjection.open_slots() ≥ 3`
   throughout the soak.
6. **Restart determinism** — extended kill-safety drill must cover
   restart-from-mid-scene boundaries: kill-and-replay between
   `scene.open` and turn-1, between turn-1 and turn-2, between
   turn-2 and turn-3, and after turn-3 but before harvester
   acceptance. Projection state after WAL replay must match live
   state bit-for-bit at every boundary, not just at scene close.
7. **Scene semantic dependence** — turn-N → prior-turns cosine
   similarity median falls in [0.3, 0.85] across the soak. Below
   0.3 indicates non-sequitur turns; above 0.85 indicates echoing.
   This is the guard against scenes passing gate 1 by lexical
   fluke.

Halt criteria:

- v0.3.1 smokes (e4b + 26b pressure) fail → diagnose the smoke
  before starting v0.4.
- v0.4 fails gate 1 even with scenes → the harness-shape lever is
  not load-bearing for social engagement either. Write ADR 0006
  proposing either (a) a fundamentally different goal (digest-style
  artifacts that don't require turn-level dialogue), or (b)
  archiving the project as a successful negative result on local
  small-model autonomous society.
- v0.4 passes 1 and fails 2 → scene parser is brittle; tune the
  retry / fallback before re-soaking.
- v0.4 passes 1 and fails 7 → scenes pass the peer-reference subgate
  by lexical fluke (echoing or non-sequitur). Tighten the embedding
  observable thresholds and re-soak; do *not* extend to 4 turns.
- v0.4 passes 1, 2, 3, 5, 6, 7 → ship v0.4.

## Empirical risks

- **Decision 1 risks hidden-complete rerouting.** If the persona
  no longer sees `complete` WIPs, the model has no choice but to
  target an open WIP — and may collapse onto the lowest-fragment
  one (the "most open" looking option) every time. Gate 4 might
  pass while gate 1 worsens (single WIP gets all contributes from
  all agents, peer reference becomes meaningless within a degenerate
  WIP). Mitigation: the v0.3.1 smoke includes a
  `wip_target_concentration` observable on `contribute_to`
  distribution. If it exceeds 0.7 on any single WIP we have a
  rerouting trap, not a fix.
- **Decision 1 risks losing useful workshop affordance signal.**
  If the persona no longer sees that `loom` exists at all (because
  it is complete and hidden), it may stop targeting `loom` even
  after recycle. Mitigation: hide only the `phase=complete`
  instances; the WIP name returns to the view on recycle.
- **Decision 2 may starve artifact ranking.** Opportunistic flushes
  triggered by WIP completion may run before enough artifact
  candidates have buffered for the percentile cutoff to be
  meaningful. Mitigation: the 50-tick timer ceiling is retained;
  the event trigger is additive, not a replacement.
- **Decision 2 may amplify flush volume under high throughput.**
  At 29 WIPs/hr (Soak B baseline), transition-triggered flush
  fires every ~123s wall-time on average. Each flush invokes a
  Trader rank() call. Under load this is more LLM volume than the
  50-tick timer alone. Mitigation: a minimum-spacing throttle
  (e.g. no more than one transition flush per 5 ticks) caps the
  amplification; the timer covers the residual.
- **Decision 3 multiplies LLM call volume per "WIP unit."** A
  3-turn scene is 3 think() calls instead of 1. At Soak A throughput
  this is fine; at Soak B 26b throughput this is a 3x latency
  pressure on Ollama. v0.4 stays on e4b for the acceptance soak
  unless a Phase 0 smoke confirms 26b can sustain 3-turn scenes
  inside `LLM_TIMEOUT_S=90` with retries.
- **Scenes can monopolize the tick ecology.** If every agent picks
  the workshop affordance preferentially (because it is the path
  to artifact creation), other verbs (craft, study, speak, rest)
  collapse. Gate 3 (verb concentration) would still pass — different
  agents can lead different scenes — but the *non-scene* verbs lose
  weight. Mitigation: track the per-agent scene-vs-non-scene tick
  ratio; if scenes exceed 70% of an agent's ticks sustained, flag
  for v0.5 review.
- **Closed conversational circuits.** If the same 2-3 agents always
  participate in scenes (because they are the only ones with
  soul_tokens above the scheduler floor), the harness produces a
  small clique rather than a society. Mitigation: scene author
  selection should be biased toward including any agent who has not
  been in a scene in N ticks. Round-robin starting state
  approximates this for v0.4's initial implementation.
- **Scenes can become a "novelty attractor" in their own right.**
  If the model writes turn-2 and turn-3 in a stylized way that
  passes the peer-reference subgate by lexical fluke (e.g. using
  the peer name in a stock phrase), gate 1 passes but the artifact
  is still hollow. Beyond the repeat-4gram subgate and the within-WIP
  lexical-novelty observable from ADR 0004, v0.4 adds a
  **turn-to-turn semantic dependence** observable: cosine similarity
  between turn-N's content embedding and the union of prior-turn
  embeddings should fall in a "engaged but not echoing" band
  (target [0.3, 0.85] — too low is non-sequitur, too high is
  echoing). If they start to drift in v0.4, raise thresholds
  rather than retreating to a 4-turn scene.
- **Partial-scene leakage to downstream context.** A scene that
  aborts after turn 1 leaves the WIP with a single-turn fragment.
  If the harvester writes that as an accepted WIP and downstream
  consumers read it without scene context, they see an unanswered
  proposal. Mitigation: the scene event contract logs the abort;
  the harvester's manifest entry tags partial-scene WIPs.
- **The harness-shape change reopens questions ADR 0003 settled.**
  Specifically, snapshot consistency, projection rebuild order, and
  the autobiographical-channel boundary (turn 3 author = turn 1
  author) must accommodate the scene event sequence. The extended
  kill-drill in gate 6 is non-negotiable.

## Out of scope

- Agent-spawnable WIPs (ADR 0003 deferred; v0.4 may need this if
  3 configured slots prove insufficient after Decisions 1-2).
- Persona evolution via Elder lore.
- Multi-model routing within a soak.
- Alternative harness-shape candidates (Elder digest, monthly
  narrative summary, etc.) — these become candidates only if v0.4
  scenes fail gate 1.

## Reference data

- ADR 0004 soak A gates: `soak-24h-v03-e4b-gates.json` (19,878
  events; 89 accepted WIPs; gates 5,6 pass; 1/2/3/4/7 fail).
- ADR 0004 soak B gates: `soak-24h-v03-26b-gates.json` (14,113
  events; 702 accepted WIPs; gates 2,3,5,6 pass; 1/4/7 fail).
- ADR 0004 (parent) for the structural-fix decisions and the
  pre-conditions that triggered ADR 0005.
- ADR 0003 for the workshop substrate and kill-safety invariants
  that v0.4 inherits.

## Open questions for review

1. Are Decisions 1 and 2 being tested for attribution (sequential
   smokes, expensive, isolates each lever) or shipped as a bundle
   for speed (one smoke, attribution-blind, ships sooner)? Both are
   defensible; the trade-off is debugging cost when something
   regresses in v0.4 or beyond.
2. What evidence would falsify *scenes specifically* versus merely
   show that the 3-turn implementation was brittle? Concretely: if
   v0.4 fails gate 1, is the next step (a) tune scene parameters
   (turn count, author selection, retry budget), or (b) abandon
   scenes and write ADR 0006 for a digest-style artifact? The halt
   criteria currently say "ADR 0006" but reasonable people could
   read it either way.
3. Which invariant is sacred if they conflict — no-self-history
   (ADR 0003 Path-3), social-reference rate (ADR 0005 gate 1), or
   autonomous turn semantics (no scripted prompts coercing
   reference)? Decision 3's self-history-redaction carve-out for
   turn 3 already softens (1) for (2); v0.5+ may need to soften
   further, or may discover (3) is in tension with (2) under
   adversarial conditions (e.g. agents collude to satisfy gate 1
   formulaically). The hierarchy should be made explicit before
   v0.4 lands.
4. Is the project goal still "interesting artifacts emerge from
   autonomous society," or has the v0.3 evidence shifted that to
   "interesting artifacts emerge from a harness-shaped society"?
   The phrasing matters for what we measure in v0.5+.

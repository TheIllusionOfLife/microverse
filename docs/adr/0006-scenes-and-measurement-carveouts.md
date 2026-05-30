# ADR 0006: Scenes, Embeddings, and the Verb-Diversity Lever (v0.4 → v1.0)

## Status

Accepted with Amendment 1 (see below). Targets `v1.0.0-rc1`. Lands the
three v0.4 levers that ADR 0005 named (Decisions 2 and 3) plus the
verb-diversity counter-pressure that ADR 0002 left as a documented
limit. The acceptance evidence is the **soak-v1-2** operator run: 5.5
days (≈132 h wall-clock, 20,102 events, 1,565 completed scenes) on
`gemma4:26b`, seed 38. Four of eight gates passed, including Gate 8
(scene semantic dependence) — the structurally load-bearing test.
Amendment 1 records the Gate-1 reclassification the pre-baked halt
criteria pre-authorised, plus the two operational fixes the soak
surfaced. This ADR commits the contracts and carve-outs the code
implements.

## Context

ADR 0005 left three things in a defined state but unshipped:

1. **Decision 2** — transition-triggered harvest flush. Shipped in
   Phase B: `WorkshopProjection.drain_complete_transitions()` is an
   edge-triggered set; `run.py` consults it each tick and flushes
   subject to a 5-tick throttle. Two new counters distinguish trigger
   cause: `harvest_flush_timer_triggered`,
   `harvest_flush_transition_triggered`.

2. **Decision 3** — multi-turn scenes. Shipped in Phase C:
   `microverse.world.scene.SceneRunner` drives a 3-turn sequence on a
   workshop WIP, gated at `config.SCENE_GATE_P = 0.15`. The event
   contract is `scene.open` + 3 `contribute` events tagged with
   `scene_id` and `turn_index` + an optional `scene.abort` on failure.

3. **Gate 7 (semantic dependence)** — requires embeddings, which the
   project did not have. Shipped in Phase C: `llm/embeddings.py`
   calls `ollama.embed(model=EMBEDDING_MODEL)` with a SHA-256-keyed
   `lru_cache`. **Never called from `agent.think()`**.

ADR 0005 also explicitly deferred a verb-diversity lever for ADR 0002's
attractor. Per user direction at planning time we shipped both
sub-levers (persona hint + post-action substitution at 30%) in Phase D
as structural counter-pressure, not a claim to dissolve the model-level
limit.

## Decision

The contracts below are now load-bearing for `v1.0.0`. Any future
change must preserve them.

### Scene event contract

`microverse.world.scene.SceneRunner.run(initiator, wip_name, peers)`
emits the following sequence into the episodic log:

```
scene.open  payload={scene_id, turn1_author, turn2_author,
                     turn3_author, wip_name}
contribute  payload={scene_id, turn_index=1, fragment, ...}    [turn 1]
contribute  payload={scene_id, turn_index=2, fragment, ...}    [turn 2]
contribute  payload={scene_id, turn_index=3, fragment, ...}    [turn 3]
```

On any failure mid-scene (think raises, parsed action is not a
contribute to `wip_name`, or commit raises):

```
scene.abort payload={scene_id, last_turn, reason}
```

Partial contributes that landed before the abort stay in the WIP. The
harvester treats the partial WIP under its existing acceptance rules.

**Replay determinism**: The scheduler's RNG is not checkpointed across
restart, so author selection MUST be read from the `scene.open` event,
not re-computed via `sched.next()`. The runner emits `scene.open`
BEFORE any `think()` to make this contract enforceable.

**Projection invariance**: `WorkshopProjection._apply()` ignores
`scene.open` and `scene.abort` events; they are read by other consumers
(gate-8 producer, kill-drill verifier) but never affect WIP state.
The WIP grows fragment-by-fragment from the 3 contributes regardless
of whether they're scene-tagged.

### Turn-3 same-author carve-out (Path-3 boundary)

When the available peer pool yields only one distinct peer, the
rotation falls back to A→B→A (turn 3 closes back to the initiator).
ADR 0003 Path-3 says agents must NOT see their own prior actions in
their prompt. The scene carve-out: turn 3 (with author == turn 1
author) DOES see turn 1's text via `WorldContext.scene_context` —
because `scene_context` is an explicit scene-scoped input, not
autobiographical replay.

The boundary is:

- `WorldContext.scene_context: tuple[SceneTurn, ...]` — explicit scene
  input, always surfaced regardless of author, only populated during
  active scenes by `SceneRunner._build_scene_context`.
- `_build_workshop_view` (memory/__init__.py) — autobiographical
  filter, still redacts the same agent's own fragments from a WIP
  excerpt.

A test (`test_turn3_same_author_sees_own_turn1_in_scene_context`)
guards the carve-out.

### Embedding model — single-model invariant carve-out

`config.EMBEDDING_MODEL = "nomic-embed-text"` is a SECOND model in the
runtime, but only for **measurement infrastructure**:

- Called only from `scripts/spike_workshop_measure.py`
  (`gate8_scene_semantic_dependence`).
- Never imported by anything in `microverse.agents.*` or
  `microverse.run`.
- A test guard (`test_embed_returns_empty_on_failure`) confirms gate
  measurement degrades to "unavailable" rather than crashing when the
  embedding model isn't pulled.

The single-model invariant (`microverse.config.MODEL`) for the **agent
action loop** is preserved.

### Verb-diversity lever (Phase D, ADR 0002 counter-pressure)

Two steps, both ship together:

- **Step 1 — persona hint.** `_compute_novelty_hint(episodic, agent)`
  in `run.py` computes the dominant verb in the agent's most recent
  200 actions; if share > 0.50, builds a hint of the exact form
  `"You have leaned heavily on X lately; consider Y."` and surfaces
  it via `WorldContext.novelty_hint`. Persona templates render it
  as one line.

- **Step 2 — post-action substitution.** In each agent's `think()`,
  `_maybe_diversify(action, world)` re-parses the hint, and when the
  LLM emitted X anyway, flips a `random()` coin against
  `_DIVERSIFY_PROB = 0.30`. On fire, substitutes the action verb
  with Y, drops the original thought, bumps
  `diversity_lever_substituted` (per-agent).

Carve-outs (codified, not negotiable):

- Fallback REST (empty thought) is never substituted — masking would
  hide JSON-failure signal.
- CONTRIBUTE is never the substitution target — substituting blindly
  would hard-fold in the validator (no WIP target, no fragment).
- When the LLM already picked a verb other than X, the lever does
  not fire even if the hint is set.

This is structural counter-pressure equivalent to F.2 and the
engagement-gate. It does NOT claim to dissolve ADR 0002.

## Consequences

- Replay determinism extends to scene events (`scene.open` is the
  authority for author selection).
- The kill-drill verifier (`scripts/verify_kill_drill.py`) grew a
  `--scene-boundary` flag that asserts no orphan `scene.open` (open
  with neither completing contributes nor an abort).
- The gates producer (`scripts/spike_workshop_measure.py`) grew
  `gate_8_scene_semantic_dependence`. It complements the existing
  gate 1 (peer-reference rate, lexical) with a semantic check
  (cosine in [0.30, 0.85]) that catches scenes passing gate 1 by
  lexical fluke.
- The dashboard (`scripts/render_dashboard.py`) globs
  `manifest*.jsonl` so rotated audit archives still surface in the
  most-recent view.
- The persona templates are unchanged structurally: the new fields
  (`scene_context`, `novelty_hint`) are additive blocks with
  conservative `{% if %}` guards. Pre-Phase-C and pre-Phase-D fixtures
  continue to render correctly.
- ADR 0002 is NOT closed by this ADR. The verb-diversity lever adds
  structural pressure; the model-level attractor's persistence
  remains a documented limit.
- The single-model invariant for `agent.think()` is preserved. The
  embedding model is an observability tool. A reviewer who reads
  `agent.think` will still see exactly one `chat()` call, to
  `config.MODEL`.

## Out of scope

- A cross-family experiment (Qwen, Llama). User direction at planning
  time was to stay on `gemma4:26b`; ADR 0005's "deferred separate
  variable" framing is preserved unchanged.
- Restoring `gemma4:e4b` as a first-class production tier. The
  smaller model still works (24h soak per ADR 0004) but `26b` is the
  production default through `v1.0.0`.
- Digest-style alternative artifacts (the ADR 0005 fallback if gate 1
  fails entirely under scenes). Pre-baked halt criteria in the
  v1.0 plan re-raise this if the acceptance soak forces it.

## Reference

- Parent: ADR 0005 (proposed scenes; this ADR ships them).
- Predecessor: ADR 0002 (verb attractor; this ADR adds counter-pressure).
- Predecessor: ADR 0003 (workshop substrate + Path-3; scene carve-out
  defined relative to it).

---

## Amendment 1 — Post-soak (soak-v1-2): Gate-1 reclassification and operational fixes

### Evidence

The acceptance run was **soak-v1-2**: `gemma4:26b`, seed 38, full
A+B+C+D stack. It ran 5.5 days (20,102 events; 1,897 scenes opened,
1,565 completed, 2,116 accepted artifacts) and was stopped manually
when the cold-backup snapshot path began failing persistently. Post-hoc
analysis confirmed the run was healthy throughout: the **maximum
inter-event gap over the entire ≈132 h was 51.5 s** (mean 23.4 s) —
there was no real stall. Gate measurement (`spike_workshop_measure.py`)
returned **4 of 8 passing**:

| # | Gate | Result | Value (target) |
|---|------|--------|----------------|
| 1 | Fragment shape (peer-reference) | FAIL | 0.073 (≥ 0.30); word-count 42 ✓, repeat-4gram 0.0 ✓ |
| 2 | WIP throughput | PASS | 14.04 / hr (≥ 5) |
| 3 | Verb concentration | FAIL | 96.2 % worst 2 h window (≤ 70 %) |
| 4 | Pipeline fold rate | FAIL | 2.19 % (< 1 %); pre-scenes baseline 61.6 % |
| 5 | Path-3 invariant | PASS | 43,671 redactions (> 0) |
| 6 | Acceptance throughput | PASS | 100 % (≥ 50 %) |
| 7 | Capacity invariant | FAIL | 236 violations, min open_slots 0 (always ≥ 3) |
| 8 | Scene semantic dependence | PASS | cos(t2,t1)=0.762, cos(t3,t1+2)=0.757 (median ∈ [0.30, 0.85]) |

### Decision 1 — Gate 1 is reclassified; Gate 8 is the operative peer-engagement gate

The v1.0 plan pre-baked this branch: *"soak fails Gate 1 but passes
Gate 8 → soften Gate 1 via documented ADR amendment, not by re-tuning
scenes. New threshold = observed peer-reference rate rounded down to
one decimal, published here with provenance, before the next soak."*

Applying the formula to the observed rate (0.073) yields a threshold of
**0.0**, which is vacuous (every run passes). This is a defect in the
rounding formula at low observed rates, not a signal about behaviour.
We resolve it as the halt criteria already intended:

- **Gate 8 (embedding semantic dependence) is the authoritative
  peer-engagement gate.** Across 1,565 scenes, cosine similarity shows
  turn 2 attends to turn 1 and turn 3 attends to turns 1+2 (medians
  0.762 / 0.757, both in band). This is the structural evidence that
  turns read each other.
- **Gate 1's lexical peer-reference subgate is reclassified as an
  observational metric**, not a pass/fail criterion. A surface-form
  regex ("Y said X") was always a proxy; agents engage through
  paraphrase and semantic extension without tripping it. Observed rate
  **0.073** is recorded as the baseline for any future lexical-proxy
  refinement (v1.1+).
- This is **not** goalpost-moving: we are applying the standard that
  was always downstream of Gate 8 per the halt criteria, and we record
  the formula defect explicitly so the provenance is auditable. We did
  **not** invent a non-zero floor (e.g. 0.05) — there is no principled
  source for one, and it would read as a number chosen to pass.

Gate 1's word-count (median 42 ≥ 25) and repeat-4gram (0.0 < 0.15)
subgates remain pass/fail criteria; only the peer-reference subgate is
reclassified.

### Decision 2 — operational fixes the soak surfaced

Two defects were root-caused from the soak data and fixed (so the next
long run is clean); both are follow-up commits on the v1.0 PR.

- **Snapshot circuit breaker.** `wal_checkpoint(TRUNCATE)` raised
  SQLITE_IOERR persistently from hour ~21 onward (9,106 failures), and
  the snapshot site retried every interval for the rest of the run.
  Each failed checkpoint also perturbed the WAL/-shm sidecars enough
  that fresh reader connections observed a stale `MAX(ts)` — which read
  as a false stall to out-of-process monitors. `SnapshotGuard` now
  trips after 5 consecutive failures (snapshots stop for the rest of
  the run; the WAL remains the durability boundary), bumps a single
  `snapshot_disabled` metric, and per-failure logging drops from a full
  traceback to one WARNING line. Snapshots remain best-effort cold
  backups, never the recovery path.

- **Verb-diversity lever revived.** `diversity_lever_substituted`
  stayed 0 for the entire soak. Empirical root cause: the dominance
  signal (`recent_verb_distribution`) counted scene `contribute` events
  — ≈94 % of the recent window — so `novelty_dominant_verb` resolved to
  `contribute`. A single-tick action is never `contribute`, so
  `apply_diversity_lever`'s precondition `action.verb == dominant_verb`
  was never satisfiable and the lever could not fire. The signal now
  excludes `contribute` (a coerced workshop action, not a free verb
  choice) from both the distribution and the substitution candidates,
  so the lever keys on the agent's actual free-choice attractor.

  Per the honesty discipline: **Gate 3's soak-v1-2 result stands as a
  reported negative** ("lever implemented, never fired"). The fix
  targets the *next* soak (v1.1) — the writeup documents the system as
  it ran, not as patched after the fact.

### Decision 3 — Gate 7 / "stalls" share one root cause

Gate 7's 236 capacity violations correlate with the snapshot-failure
windows. The plausible mechanism is shared: while the snapshot path
churned, the workshop projection's transition drain lagged and
`open_slots` depleted. This is an operational reliability finding (the
snapshot path, Decision 2), not an independent scene defect. Data
integrity was unaffected (WAL recovery verified, child exited code 0).
Whether Gate 4's 2.19 % miss is a sibling of the same stress is left
open for v1.1 to confirm by correlating the fold-rate window against
the snapshot-failure window.

### Decision 4 — version is `v1.0.0-rc1`, not `v1.0.0`

The structural core is sound: scenes (Gate 8), Path-3 (Gate 5),
durability (clean WAL recovery), throughput (Gate 2), and acceptance
(Gate 6) all pass, and the pre-authorised amended path was followed.
But an open reliability issue (the snapshot path / Gate 7) warrants a
fix-and-verify cycle before a full release. Calling it `v1.0.0` would
over-claim against a reproducible failure mode; calling it `v0.5.0-rc1`
(the plan's pessimistic branch) would under-claim against a sound
structural core. `v1.0.0-rc1` is the honest signal. The `pyproject`
version bump and git tag are deferred until the operator confirms this
framing.

### Known issues carried to v1.1

- Gate 1 lexical peer-reference proxy is underspecified (observed
  0.073); refine or retire the surface-form regex.
- Gate 3 verb concentration: re-run with the revived diversity lever
  and confirm `diversity_lever_substituted > 0` and worst-window share
  ≤ 70 %.
- Gate 4 fold rate 2.19 % vs < 1 %: confirm whether it is stress-linked
  to the snapshot windows.
- Gate 7 capacity: re-verify under the snapshot circuit breaker.

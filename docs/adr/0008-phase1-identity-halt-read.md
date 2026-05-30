# ADR 0008: Phase 1 Identity — Halt Read (Gate 1 / Gate 3)

**Status:** Accepted (measurement record) — records the falsifiable halt read for ADR 0007 Phase 1.
**Halt decision: HALT.** Re-diagnose the substrate thesis before building Phase 2.

**Date:** 2026-05-31

**Context:** ADR 0007 ("From Workshop to Civilization") makes Phase 1 (persistent identity +
relationship ledger, shipped in #43, squash-merged as `b38685e`) the *falsifiable gate* for the
whole arc: if durable identity does not move **Gate 1 (lexical peer-reference, baseline 0.073)** or
**Gate 3 (verb monoculture, ~0.96 worst 2-hour window)**, the substrate thesis is wrong and Phases
2–5 halt (ADR 0007, "Empirical risks" / halt criterion). This ADR records that read and its result.
The ADR explicitly forbids tuning prompts to manufacture movement.

## Method

- **Identity-ON** = current `main` (`b38685e`). **Identity-OFF control** = `52631b7` (parent of the
  squash), which has the identical roster (Aki/Artisan `soul_tokens=100` + Cy/Scholar
  `soul_tokens=70`) and differs only in the absence of `WorldContext.self_view`.
- Runner: `python -m microverse.run --tempo 0 --seed {42,38,7}` into isolated `MICROVERSE_DATA`/
  `MICROVERSE_HARVEST` dirs. Model: `gemma4:26b` (the configured `agent.think()` model).
- Measurement: `scripts/spike_workshop_measure.py` from `main` for **both** arms, so the only
  variable is the runtime (identity on/off), not the gate definitions.
- **Gate-1 A/B**: 300 ticks per seed, OFF vs ON.
- **Gate-3 soak**: identity-ON, seed 42, 3000 ticks. Gate 3 bins events into wall-clock hour
  buckets and needs ≥2 *consecutive* hours; the run spanned **6.21 h / 4715 events**, so the
  windows actually form. (A 300-tick run spans only ~39 min — all events land in hour 0 and Gate 3
  returns its `worst_share=0.0 / actor=null` default, a non-reading. This is a run-length issue, not
  a tempo issue: `gemma4:26b` think() is model-bound, so even at `--tempo 0` wall-clock accrues.)

## Results

### Gate 1 — peer_reference_rate (baseline 0.073; higher is better)

| Condition | seed 42 | seed 38 | seed 7 | median |
|---|---|---|---|---|
| identity OFF (`52631b7`, 300t) | 0.068 | 0.071 | 0.082 | **0.071** |
| identity ON (`main`, 300t) | 0.081 | 0.058 | 0.060 | **0.060** |
| identity ON (6.21 h soak, 3424 fragments) | 0.070 | — | — | **0.070** |

All conditions cluster at ~0.07, within ~1 standard error (≈0.014 at ~350 fragments) of each other
and of the 0.073 baseline. The OFF control median (0.071) lands on the documented baseline, which
calibrates the A/B. **Identity does not move Gate 1.**

### Gate 3 — worst_share (baseline ~0.96; lower is better)

| Condition | worst_share | worst_actor | window |
|---|---|---|---|
| baseline (5.5-day soak, pre-identity) | ~0.96 | — | — |
| identity ON (6.21 h soak, seed 42) | **0.94** | Cy | hour 1 |

0.96 → 0.94 (~0.02) is within noise. The world remains a **contribute-monoculture** over the soak:
3427 `contribute` vs 175 `speak`, 92 `craft`, 29 `study`, 18 `rest`. **Gate 3 does not move.**

### Run health

Identity is verifiably active and correct: beliefs regenerate on cadence, Path-3 self-redactions
fire, the relationship ledger populates, zero crashes/leaks across ~14 h of runs, and zero belief
meta-leak blocks. The systems work; they just produce no measurable behavioral movement.

## Decision

**The ADR 0007 halt criterion is tripped: neither Gate 1 nor Gate 3 moved. HALT before Phase 2.**

Because Gate 3 did not move at all, the anticipated Stage-A-novelty-vs-identity attribution confound
(the #43 squash bundled the `build_context` field-preservation fix, which reactivates
`apply_diversity_lever`, together with identity) is moot — there is no Gate-3 movement to attribute.

## Re-diagnosis directions (before any Phase 2 investment)

1. **Gate 1 (peer reference)** counts peer *names inside completed fragments*; identity only raises
   peer salience in the *prompt*. That indirection yields ~zero gain here. Either the salience must
   be much stronger, or peer-reference-in-fragments is the wrong proxy for "the world remembers
   relationships" — pick a metric closer to the mechanism identity actually changes.
2. **Gate 3 (verb monoculture)** is **structural**: the scene/workshop loop funnels ~95% of actions
   into `contribute`. No identity layer can diversify verbs without changing the action economy
   itself. This is a Phase-2-level intervention (new action affordances / incentives), not something
   identity alone can unlock.

## Reproduction

```bash
# identity-OFF control
git worktree add /tmp/mv-identity-off 52631b7
# per seed: run 300t in the worktree, measure from main
MICROVERSE_DATA=… MICROVERSE_HARVEST=… \
  uv run python -m microverse.run --ticks 300 --tempo 0 --seed 42   # in /tmp/mv-identity-off
uv run python scripts/spike_workshop_measure.py --data … --harvest …  # from main

# Gate-3 soak (identity-ON): long enough to span >=2 consecutive wall-clock hours
MICROVERSE_DATA=… MICROVERSE_HARVEST=… \
  uv run python -m microverse.run --ticks 3000 --tempo 0 --seed 42
uv run python scripts/spike_workshop_measure.py --data … --harvest …
```

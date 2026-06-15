# ADR 0015: Action-economy roster generality — PARTIAL (role pairing transfers, more residents does not)

**Status:** Accepted (measurement record) — records the held-out roster-generality sweep (ADR 0012
Phase 2 item 4) of the replicated `bal@30` Gate 9 PASS. **Verdict: PARTIAL.** The instrument gate
passed on all six runs. **R2 (Artisan + Stranger) GENERALIZES** (3/3 seeds pass Layer 1 + Layer 2);
**R3 (Artisan + Scholar + Stranger) DOES NOT GENERALIZE** (0/3 seeds clear the Layer 1 cross-agent
divergence floor). The verdict is read against the two-layer rule locked before the sweep's first
tick, with no post-hoc amendment.

**Date:** 2026-06-16

## Context

ADR 0012 produced the arc's first Gate 9 PASS at `bal@30`; ADR 0013 confirmed the scarcity hint as
the operative channel; ADR 0014 replicated the PASS on fresh seeds — all on the default 2-resident
dyad Aki(Artisan, 100) + Cy(Scholar, 70). ADR 0014 Decision 2 named roster generality as the open
frontier. Item 4 is the first held-out test: hold the dose T = 30 fixed (the transfer question) and
change the roster. Two cells — **R2 role-swap** (Artisan + Stranger, a different pairing at the
dyad's weight shape) and **R3 all-roles** (Artisan + Scholar + Stranger, N = 3 + a third specialty).

The pre-registration (`docs/economy-phase2-roster-generality-runbook.md`, locked `4388a64`) flagged
R3 as the at-risk cell on a dose hypothesis: its weight-70 agents, scheduled less at N = 3, were
predicted to under-fire (~0.20) at fixed T = 30.

## Method

One sweep, `{R2, R3}` × seeds `{101, 202, 303}` × 3000 ticks, `gemma4:26b`, energy knobs at the live
defaults. Roster selection via the new `MICROVERSE_ROSTER` env hook (this PR). The two-layer rule,
instrument gate, verdict mapping, and a calibrated offline firing pre-flight were committed before
the first tick. Measurement reuses `gate9_verb_diversity` (chosen stream) and
`replay_economy.py --audit`; the audit ledger now seeds from the run's actual residents (this PR) so
it is faithful for the Stranger resident, while the 2-agent reads stay byte-identical.

## Results (summary — full tables in the findings doc)

- **Instrument gate PASS (all six runs).** Fidelity hint-off 1.000, hint-on 0.921–0.953;
  `hint_logged` ≥ 0.998; coverage well above the floor — on both rosters, incl. the Stranger.
- **R2 — GENERALIZES.** Chosen `jsd_norm` 0.332 / 0.375 / 0.354 (≥ 0.25, bracketing the dyad's
  replicated 0.34–0.39); per-agent role stability clean (Aki→`craft`, Vesna→`travel`, cross-bleed
  ≤ 0.003) at all three seeds.
- **R3 — DOES NOT GENERALIZE.** Chosen `jsd_norm` 0.196 / 0.194 / 0.212 (< 0.25) at all three seeds;
  Layer 2 passes only 1/3 (cross-bleed grazes the 0.05 line). Society entropy is high (0.61–0.67,
  near the N = 3 ceiling 0.613) — the society IS diverse, but the three agents are not differentiated
  from each other.
- **The dose hypothesis is REFUTED.** Live firing ran ~1.5–2× the pre-flight prediction; R3's
  weight-70 agents fired ~0.40–0.50 (adequate, near the dyad's ~0.41), not ~0.20. Firing is not the
  R3 bottleneck.
- **The Stranger specializes weakly.** Vesna's obedience to its `travel` hint is ~0.31–0.33 across
  both rosters — far below the Scholar's `study` (0.62–0.72) and Artisan's `craft` (0.45–0.55) — so
  it stays contribute-dominant (chosen-contribute 0.70–0.78 in R3) and pulls little divergence.
- **R3 scenes do not collapse** (`scene_completed` 339–349 vs R2 317–349); the hint-text deconfound
  holds on both rosters (`P(spec|hint)` ≫ `P(spec|absent)`, `absent_low` flat).

## Decision

1. **Generality is PARTIAL: role pairing transfers, adding a resident does not (yet).** The
   `bal@30` specialization mechanism reproduces cleanly when the Scholar is swapped for a Stranger
   (R2), but the three-resident roster (R3) does not clear the cross-agent divergence floor. The
   claim is scoped accordingly: the effect is not unique to the Aki/Cy dyad, but it is not yet shown
   to scale past two residents.
2. **The R3 failure is a divergence/specialization-strength problem, not a dose problem.** The
   pre-registered dose under-shoot did not occur; firing was adequate. Two drivers are recorded:
   (a) the Stranger obeys its `travel` hint weakly (~0.31), staying contribute-heavy; (b) the
   log2(n)-normalized cross-agent JSD is a harder bar at N = 3 even with high society entropy.
3. **The dose re-tune (~T = 42) follow-up is shelved** as refuted. The re-scoped follow-ups are:
   (a) retest R3 with a *strongly*-specializing third agent (a role/verb the model obeys like
   study/craft, or a higher-weight third resident); (b) revisit whether `jsd ≥ 0.25` is the right
   invariant divergence bar at N > 2, or whether an entropy-relative reading better captures
   "broke monoculture + specialized" — a measurement-design question for a future ADR, not a
   post-hoc rescue of this one.
4. **The pre-registration discipline is recorded as drafted.** R3 fails Layer 1 as written; the
   refuted dose hypothesis is reported, not quietly replaced. This mirrors the arc's practice since
   ADR 0013.

## Scope and limitations

- Single model (`gemma4:26b`), fixed dose T = 30, three fresh seeds per roster, two rosters. The
  sweep does not decompose the R3 failure between its two drivers (weak Stranger specialization vs
  the N = 3 JSD normalization); the follow-ups target each.
- No in-sweep control (criteria are absolute); no mid-run restarts (instrument gate passed on logged
  ground truth).
- "Different weights" — the third roster-generality axis — was deliberately deferred (a
  dose/scheduling confound) and remains untested.

## Reproduction

Findings: `docs/economy-phase2-roster-generality-findings.md`. Pre-registration:
`docs/economy-phase2-roster-generality-runbook.md` (locked `4388a64`). Driver:
`scripts/run_roster_gen_sweep.sh`. Audit:
`scripts/replay_economy.py --audit bal@30=data/econ-roster-<r>-s<seed>`. Run dirs
`data/econ-roster-*` (untracked, kept locally).

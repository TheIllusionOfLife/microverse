# ADR 0018: Action-economy R3 levers II — economic re-tune achieves N=3, weights does not

**Status:** Accepted (measurement record) — the two remaining ADR 0015 Decision 3(a) levers after the
persona lever was rejected (ADR 0017). **Verdicts: economic `bal@42` → N=3 ACHIEVABLE** (Layer 1
rescued on all three seeds, ≥2/3 pass both layers); **weights `wt130` → N=3 STILL FAILS** (a
redistribution wash, stopped at the pilot). The combined reading: R3's N=3 Gate-9 failure (ADR 0015
PARTIAL) was a **fixed-dose artifact, not intrinsic roster-size difficulty** — the specialization
mechanism generalizes to N=3 once the contribute cost is rescaled for the diluted per-agent
scheduling.

**Date:** 2026-06-18

## Context

ADR 0015 read R3 (Artisan + Scholar + Stranger, N=3) as DOES NOT GENERALIZE at the fixed dyad dose
T=30; ADR 0016 confirmed the FAIL under the size-invariant `mean_pairwise_jsd` (0.207 < 0.25); ADR
0017 showed a stronger-travel Stranger persona *backfired*. ADR 0017 re-scoped the frontier toward the
**contribute pull** (economic) and the **weights axis**. ADR 0015 had itself pre-scoped the economic
lever as a "drain-equated ~T=42" re-tune: at N=3 the weight-70 residents are scheduled less, so the
fixed T=30 under-drains them and the scarcity hint under-bites.

## Method

Two fresh live sweeps (`gemma4:26b`, 3000 ticks), **default Stranger persona**, existing knobs only —
no new code. Pre-registered before launch (`docs/economy-phase2-r3-levers2-runbook.md`): metric (ADR
0016 `mean_pairwise_jsd ≥ 0.25`), Layer 2 role stability, instrument fidelity gate, pilot-then-continue
(≥0.235), per-lever verdict mapping, and per-lever predictions.

- **Lever (a) economic `bal@42`** (`MICROVERSE_BAL_CONTRIBUTE=42`, R3 100/70/70): raise every role's
  contribute cost so over-contribution is unaffordable and agents are forced to their cheap specialty.
  T=42 set by an offline round-robin always-contribute throttle sweep (ceiling 0.71 → 0.45), disclosed
  as over-modelling throttling for the under-scheduled agents. Full 3-seed sweep.
- **Lever (b) weights `wt130`** (Vesna 70→130, `bal@30`): restore dyad-like scheduling for the
  Stranger. Pre-registered as the weak bet — the live R3 firing (Vesna 0.50) had already refuted the
  under-firing premise. Pilot only unless it cleared.

## Results (summary — full tables in the findings doc)

- **`bal@42` Layer 1 PASS on all three seeds:** `mean_pairwise_jsd` 0.305 / 0.299 / 0.326 (vs baseline
  0.207 / 0.206 / 0.223). `identity_verb_nmi` 0.19 → 0.26–0.28 corroborates real cross-agent
  differentiation. Instrument gate passes every seed.
- **`bal@42` Layer 2 PASS on 2/3** (s202, s303). Every resident's top non-contribute verb is its
  specialty on every seed (Cy's `study` even ties/leads `contribute`). The sole blemish is **Aki's
  `study` cross-bleed** at the 0.05 floor (0.049 / 0.050 / 0.070) — structural, because `study` is the
  artisan's second-cheapest verb; s101 (0.070) is the one per-seed Layer-2 fail.
- **Mechanism confirmed:** dear contribute suppresses society-wide contribute-dominance; Cy specializes
  hardest (study 0.28 → 0.46–0.49), Aki craft rises (0.36 → 0.43–0.49), Vesna travel improves modestly
  (0.16 → 0.18–0.19). The bar clears through the two strong specialists; the obedience-limited Stranger
  need not be fixed.
- **`wt130` STILL FAILS:** pilot `mean_pairwise_jsd` 0.209 ≈ baseline, below 0.235 → stopped. A wash:
  Vesna got more turns and travel (0.156 → 0.233) but Aki/Cy were starved and grew *more*
  contribute-dominant. Weights redistribute scheduling; they cannot add total per-agent draining at
  N=3.

## Decision

1. **`bal@42` rescues R3 generality: N=3 is ACHIEVABLE.** ≥2/3 seeds pass both layers and Layer 1
   passes 3/3. The economic re-tune is adopted as the working N=3 dose. R3's failure was the dyad's
   T=30 dose under-draining the diluted N=3 schedule — **a fixed-dose artifact, not intrinsic
   roster-size difficulty.** The specialization mechanism (ADRs 0013/0014) generalizes to N=3; the
   *dose* must scale with roster size.
2. **This refines, not overturns, ADR 0015.** ADR 0015 held T fixed to ask "does the dyad's exact dose
   transfer to a new roster without re-tuning?" — and it correctly does not (R2 transfers, R3 does
   not). ADR 0018 answers the distinct, pre-scoped question "does the mechanism exist at N=3 with a
   drain-equated re-tune?" — and it does. The PARTIAL "transfer" verdict stands; the "mechanism at N=3"
   verdict is now POSITIVE.
3. **The weights axis is rejected** as an N=3 lever: it is a redistribution wash and cannot increase
   total per-agent draining. Stopped at the pilot per the locked rule.
4. **The Stranger remains the weakest specialist but is no longer the blocker.** ADR 0017 showed the
   Stranger is obedience-limited; `bal@42` clears N=3 through the two strong specialists regardless.
   The residual Aki `study` cross-bleed at the 0.05 floor is a cost-table structural artifact, noted
   for any future Layer-2 tightening.

## Scope and limitations

- **N=3 only, one re-tuned dose.** `bal@42` was set by an offline throttle sweep and pre-committed;
  this is one drain-equated dose, not a claim that 42 is uniquely optimal. Larger rosters (N>3) would
  need their own dose rescaling — the generalizable claim is "dose scales with scheduling dilution,"
  not "42 is the number."
- **Layer 2 is 2/3, not 3/3.** The verdict meets the locked ≥2/3 rule but is not a clean sweep; s101's
  Aki cross-bleed (0.070) is disclosed, not waved away. A stricter Layer 2 would call this borderline.
- **Ollama runtime instability** degraded two runs (one full wedge, one 43%-fallback partial); both
  were caught by a fallback-rate check (≤3% healthy vs 43–100% degraded), quarantined, and re-run
  clean. The fidelity instrument gate does NOT catch LLM-runtime degradation; reproductions must check
  per-run fallback rate. See the findings operational note.
- The `MICROVERSE_STRANGER_PERSONA` toggle + travel variant (ADR 0017) remain in the tree, default-off
  and unused by this result.

## Reproduction

Findings: `docs/economy-phase2-r3-levers2-findings.md`. Pre-registration:
`docs/economy-phase2-r3-levers2-runbook.md`. Runner: `scripts/run_r3_levers2_sweep.sh {bal42|wt130}`.
Metric/audit: `scripts/spike_workshop_measure.py`, `scripts/replay_economy.py`. Run dirs
`data/econ-r3bal42-s{101,202,303}`, `data/econ-r3wt130-s101`; baseline `data/econ-roster-r3-s*`
(untracked, kept locally).

# ADR 0016: Action-economy N>2 divergence criterion — criterion refined, R3 verdict unchanged

**Status:** Accepted (measurement record) — records the measurement-design re-analysis ADR 0015
Decision 3(b) handed forward: is `jsd_norm ≥ 0.25` (log2(n)-normalized) the right invariant
divergence bar at N>2? **Verdict: CRITERION REFINED, VERDICT UNCHANGED.** A size-invariant metric
(`mean_pairwise_jsd`) replaces the defective `log2(n)` normalization at N>2, equals the old metric
exactly on the entire N=2 calibration corpus, and re-reads R3 as **still FAILING** (0.207 / 0.206 /
0.223 < 0.25 on all three seeds). The ADR 0015 PARTIAL roster-generality verdict stands.

**Date:** 2026-06-16

## Context

ADR 0015 read R3 (Artisan + Scholar + Stranger, N=3) as DOES NOT GENERALIZE: chosen-stream
`jsd_norm` 0.196 / 0.194 / 0.212 < 0.25 at all three seeds, **even though society entropy sat near
the N=3 perfect-specialist ceiling** (0.61–0.67 vs 0.613). ADR 0015 Decision 3(b) flagged this as a
measurement-design question — is the bar itself right at N>2? — and scoped it as a future ADR, "not
a post-hoc rescue of this one."

The headline metric `_multi_jsd` is arity-split: at N=2 it returns the pairwise JSD `_jsd_bits`
(range [0,1]); at N>2 it computes the centroid radius `H(mean) − mean(H)` and divides by `log2(n)`.
That divisor is the *maximum* radius, so it rescales the ceiling to 1.0 correctly — but it is not a
similarity-preserving rescaling of the interior: a fixed amount of pairwise specialization is divided
by a larger constant as N grows, folding a roster-size penalty into a quantity ADR 0015 sold as
"roster-size invariant." That is a metric defect independent of any run.

## Method

One additive change to `scripts/spike_workshop_measure.py` (TDD red→green) plus a frozen-data
re-analysis — **no new GPU compute**. The new metric, floor, equivalence claim, pass rule, verdict
mapping, and the bar against gating the entropy-relative diagnostic were committed
(`docs/economy-phase2-divergence-criterion-runbook.md`) before the criterion was adopted.

`mean_pairwise_jsd = (2 / (n(n−1))) · Σ_{i<j} JSD(pᵢ, pⱼ)` — the mean of pairwise JSDs, each in
[0,1], so the scale is N-invariant with no divisor. **It equals `_multi_jsd` exactly at N=2** (both
return the single `_jsd_bits`), so the 0.25 floor is **inherited** from the locked N=2 calibration,
not re-derived. Two diagnostics are reported but **barred from gating**: `specialization_ratio`
(society entropy / perfect-specialist ceiling) and `identity_verb_nmi` (I(agent;verb) normalized).
The gate default stays `jsd_norm` (no behavior change); the new metric is opt-in via
`divergence_metric="mean_pairwise_jsd"`.

## Results (summary — full tables in the findings doc)

- **Equivalence CONFIRMED.** All 12 N=2 runs (3 R2 + 9 dyad) have `mean_pairwise_jsd == jsd_norm` to
  4 decimals. The metrics diverge only at N=3.
- **R3 still FAILS.** Under the size-invariant metric R3 reads 0.2073 / 0.2063 / 0.2231 — higher than
  the log2(n)-normalized 0.196 / 0.194 / 0.212 (the 1.585 divisor is removed), but **still < 0.25 on
  all three seeds**. The reframe demonstrably cannot flip the verdict.
- **R2 + dyad still PASS** under both metrics (R2 0.33–0.37, dyad 0.31–0.39).
- **The R3 signature is explained, not excused.** `specialization_ratio ≈ 1.0` for R3 (the society
  reached the 3-specialist entropy ceiling) while `identity_verb_nmi` (0.19–0.20) sits below R2's
  (~0.25): the society is diverse but its agents are not differentiated from each other. Gating on
  the ~1.0 ratio would have manufactured an R3 pass — the named forbidden move, structurally barred.
- **Layer 2 unchanged.** R3 agents hold their specialty as the top non-contribute verb; the failure
  is purely Layer 1 (divergence) under both metrics.

## Decision

1. **Adopt `mean_pairwise_jsd` as the size-invariant divergence metric for N>2 reads.** It removes a
   real `log2(n)` defect and preserves the entire locked N=2 calibration byte-for-byte. It is added
   as a reported metric and an opt-in gate (`divergence_metric`); the default gate stays `jsd_norm`
   so no prior read changes until a future confirmatory read formally switches the default.
2. **The ADR 0015 R3 verdict is UNCHANGED.** R3 fails Layer 1 under both the old and the new metric.
   The DOES NOT GENERALIZE read for R3 (and the PARTIAL sweep verdict) was a genuine
   weak-differentiation finding, not a normalization artifact. This is a refinement of the
   instrument, not a rescue of the result — exactly as ADR 0015 Decision 3(b) required.
3. **The entropy-relative reading is rejected as a gate.** `specialization_ratio` answers a different
   question ("did the society diversify") than the arc tests ("did the agents differentiate from each
   other"); it is ~1.0 for the undifferentiated R3 and is therefore reported-only, never gated. This
   closes the ADR 0015 Decision 3(b) "entropy-relative vs jsd" question: entropy-relative does not
   capture cross-agent specialization and must not set the bar.
4. **The R3 weak-differentiation drivers remain the open frontier** (ADR 0015 Decision 3(a)): a
   strongly-specializing third agent, or the deferred weights axis. This ADR resolves only the
   measurement-design half; it does not attempt to make R3 pass.

## Scope and limitations

- This is a re-analysis of frozen data, not a new behavioral read. `mean_pairwise_jsd` was computed
  on R3 during design (disclosed in the runbook honesty note); the anti-fitting defense is structural
  (inherited floor + R3 still fails + pre-committed expected outcome), not "unseen data."
- The default gate is intentionally not flipped here; switching `gate9_verb_diversity`'s default
  metric and re-baselining the N=2 reports is a separate, mechanical follow-up.
- The metric removes the `log2(n)` defect but does not claim to be the unique correct N>2 divergence
  measure; it is the minimal change that preserves the locked calibration.

## Reproduction

Findings: `docs/economy-phase2-divergence-criterion-findings.md`. Pre-registration:
`docs/economy-phase2-divergence-criterion-runbook.md`. Metric + diagnostics:
`scripts/spike_workshop_measure.py` (`_mean_pairwise_jsd`, `_specialization_ratio`,
`_identity_verb_nmi`). Tests: `tests/test_gate9_verb_diversity.py` (ADR 0016 block). Run dirs
`data/econ-roster-*`, `data/econ-rep30-*`, `data/econ-stage6-*` (untracked, kept locally).

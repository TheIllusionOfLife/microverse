# Phase 2 item 4 follow-up runbook — the N>2 divergence criterion (ADR 0016)

Pre-registration for the measurement-design question ADR 0015 Decision 3(b) handed forward: **is
`jsd_norm ≥ 0.25` the right invariant divergence bar at N > 2, or does the `log2(n)` normalization
mis-measure cross-agent specialization at N = 3?**

This is a **re-analysis of frozen data**, not a live sweep — no new GPU compute. It re-reads the six
existing `data/econ-roster-*` runs (and the dyad runs) under an alternative divergence metric. Because
the data is already on disk and was read once under the old metric (ADR 0015), the usual "unseen at
pre-registration" guarantee does NOT apply. The anti-fitting defense is therefore **structural**, and
this runbook locks that structure before the criterion is adopted. See the Honesty note.

## Motivation (measurement theory only)

The headline divergence metric `_multi_jsd` (`scripts/spike_workshop_measure.py`) does two different
things by arity:

- at **N = 2** it returns the pairwise Jensen-Shannon divergence `_jsd_bits(p₁, p₂)`, range [0, 1];
- at **N > 2** it computes the centroid radius `H(mean) − mean(H)` and **divides by `log2(n)`**.

The divisor is the *maximum possible* value of that radius (n perfect specialists on n distinct
verbs gives raw = log2(n)), so it correctly rescales the **ceiling** to 1.0. But it is **not a
similarity-preserving rescaling of the interior**: a fixed amount of pairwise specialization is
divided by a larger constant as N grows, so the same per-pair differentiation reads *lower* at N = 3
than at N = 2. The divisor folds a roster-size penalty into a quantity the item-4 runbook explicitly
sold as "roster-size invariant" (`economy-phase2-roster-generality-runbook.md` Layer 1). That is a
measurement defect independent of any particular run.

**The fix:** mean pairwise JSD, `mean_pairwise_jsd = (2 / (n(n−1))) · Σ_{i<j} JSD(pᵢ, pⱼ)`. Each pair
is independently in [0, 1]; the mean of [0, 1] values is in [0, 1] at every N. No N-dependent divisor.
It answers "is the average pair of agents distinct" without conflating that with roster size.

This motivation cites only the metric algebra and the locked N = 2 calibration anchors
(`tests/test_gate9_verb_diversity.py`); it does not appeal to any R3 number.

## Why the floor is inherited, not re-derived

`mean_pairwise_jsd` **equals `_multi_jsd` exactly at N = 2** (both return the single `_jsd_bits`;
verified at `spike_workshop_measure.py` n==2 branch and by
`test_mean_pairwise_equals_multi_jsd_at_n2` over every locked anchor). So on the entire 2-agent
calibration corpus — disjoint specialists (1.0), partial specialization (0.6), shared-modal-disjoint-
secondary (0.4), relocating-modal (0.335), stage3-codrift (0.185, fails), monoculture (0.0) — the new
metric is byte-identical to the old one. Keeping the floor at **0.25** therefore preserves every locked
N = 2 pass/fail decision. The floor is not chosen, tuned, or re-derived against any N = 3 read; it is
the existing value, and the only change is removing the `log2(n)` divisor that applies only at N > 2.

This is the crux: a metric that is identical on the calibration set and differs only by removing a
defective N-dependent term cannot be a threshold fit.

---

## ===== DO NOT EDIT BELOW (pre-registered before the criterion is adopted) =====

### The locked metric

`mean_pairwise_jsd = (2 / (n(n−1))) · Σ_{i<j} JSD(pᵢ, pⱼ)`, computed on the **chosen** (`parsed_verb`)
stream's per-agent verb distributions, via `gate9_verb_diversity(..., divergence_metric=
"mean_pairwise_jsd")`. Floor **0.25**, inherited from the N = 2 calibration (see above).

### Falsifiable equivalence claim (locked)

For **every** N = 2 run in the frozen corpus, `mean_pairwise_jsd == jsd_norm` to 4 decimal places.
Checked on the R2 runs (`data/econ-roster-r2-s{101,202,303}`) and the dyad runs
(`data/econ-rep30-bal30-s*`, `data/econ-stage6-bal30-s*`). A single counterexample falsifies the
whole inherited-floor argument and the criterion is withdrawn.

### Reported-only diagnostics — BARRED from gating (locked)

These are computed and reported for triangulation; **none may gate the verdict**:

- **`specialization_ratio`** `= society_entropy_norm / (log2(min(n,k)) / log2(k))`, k = 6 verbs.
  Captures "did the society reach the entropy a fully-specialized roster of this size would". It is
  ≈ 1.0 for a high-entropy society **even when agents are not differentiated from each other** —
  which is exactly the R3 signature ADR 0015 found — so gating on it would manufacture a pass. It is
  the named forbidden move.
- **`identity_verb_nmi`** `= I(agent; verb) / sqrt(H(A)·H(V))`. Independent-family corroboration of
  the divergence ordering; its scale differs from the 0.25-on-JSD calibration, so adopting it as the
  gate would require a new contestable floor. Reported only.

### Pass rule (locked)

Per run, on the chosen stream, with the instrument gate (ADR 0014 fidelity, unchanged) already
passed:

- **Layer 1 — society divergence (hard):** `mean_pairwise_jsd ≥ 0.25`.
- **Layer 2 — per-agent role stability (hard, unchanged from ADR 0015):** every resident's top
  NON-contribute chosen verb is its own specialty, cross-bleed ≤ 0.05.

`jsd_norm`, `society_entropy_norm`, and the two diagnostics above are reported alongside, not gated.

### Verdict mapping (locked)

The deliverable is whether the criterion change **alters the ADR 0015 generality verdict**:

- **CRITERION REFINED, VERDICT UNCHANGED** — R3 still FAILS Layer 1 under `mean_pairwise_jsd`
  (and R2 / dyad still PASS). The arc gains a size-invariant bar; the PARTIAL verdict and the R3
  weak-differentiation reading stand. This is the pre-committed expected outcome (see Honesty note).
- **VERDICT MATERIALLY CHANGED — FLAGGED FOR SCRUTINY** — R3 now PASSES Layer 1 under the new metric.
  This would mean the `log2(n)` divisor, not agent behavior, drove the ADR 0015 R3 FAIL. Both readings
  (old and new metric, all six runs) are reported in full and the result is flagged as a criterion
  change that moves a verdict, with the equivalence claim and floor-inheritance re-audited before any
  claim is made.
- **EQUIVALENCE BROKEN** — any N = 2 run shows `mean_pairwise_jsd ≠ jsd_norm`. The inherited-floor
  argument fails; the criterion is withdrawn and re-derived from scratch in a separate ADR.

No post-hoc amendment: the rule above is committed before the criterion is adopted.

### Honesty note

This is frozen-data re-analysis. **`mean_pairwise_jsd` was computed on the R3 runs during the design
of this change** and read **0.207 / 0.206 / 0.223** for seeds 101 / 202 / 303 (vs the ADR 0015
`jsd_norm` 0.196 / 0.194 / 0.212) — all still **< 0.25**. These numbers are disclosed here, not hidden,
mirroring the arc's practice since ADR 0013 of reporting design-time computation rather than claiming
false blindness. The criterion is adopted **because** R3 still fails under it (so it cannot be a
rescue) and **with** the floor inherited from the untouched N = 2 calibration (so it cannot be a
threshold fit) — the pre-committed expected outcome is **CRITERION REFINED, VERDICT UNCHANGED**. The
one outcome that would demand scrutiny (R3 flips to PASS) is named above and handled, not assumed away.

---

After the read: `docs/economy-phase2-divergence-criterion-findings.md` (the full old-vs-new metric
table across all six roster runs + the dyad regression and the sanity matrix) and
`docs/adr/0016-action-economy-divergence-criterion.md` recording the verdict, appended to this PR.
